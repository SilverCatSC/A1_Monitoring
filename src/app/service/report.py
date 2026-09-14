from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import or_

from app.config import BUSINESS_TRUSTED_NETWORK_PROFILES, PRODUCTION_NETWORK_PROFILES, settings
from app.models import (
    AbsenceEpisode,
    DealerDiscoveryRun,
    DealerListingCandidate,
    EngineType,
    FeedbackStatus,
    Listing,
    ListingChangeEvent,
    ListingObservation,
    ListingReconciliation,
    ManagerFeedback,
    ObservationState,
    ScanRun,
    ScanRunStatus,
    SearchFilter,
    SourceImportSnapshot,
)
from app.scraper.base import canonical_listing_key, is_marketplace_listing_url
from app.service.company_site_report import company_site_audit_context
from app.service.evidence import evidence_pages, has_card_evidence
from app.service.feedback import ALLOWED_CATEGORIES, ALLOWED_SEVERITIES, ALLOWED_TRANSITIONS
from app.service.filters import FilterRegistryService


def _safe_listing_url(source: EngineType, value: str | None) -> str | None:
    return value if is_marketplace_listing_url(source, value) else None


def _head_records_for_listings(
    listings: list[Listing],
) -> tuple[dict[str, dict], list[dict]]:
    path = Path(settings.head_table_audit_dir) / 'latest.json'
    try:
        if not path.is_file() or path.stat().st_size > 5_000_000:
            return {}, []
        payload = json.loads(path.read_text(encoding='utf-8'))
        records = payload.get('records', []) if isinstance(payload, dict) else []
    except (OSError, ValueError, json.JSONDecodeError):
        return {}, []
    by_vin: dict[str, list[dict]] = {}
    by_url: dict[tuple[EngineType, str], list[dict]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        vin = str(record.get('vin') or '').strip().upper()
        if vin:
            by_vin.setdefault(vin, []).append(record)
        for source, field in (
            (EngineType.AUTO_RU, 'auto_ru_url'),
            (EngineType.AVITO, 'avito_url'),
        ):
            key = canonical_listing_key(source, record.get(field))
            if key:
                by_url.setdefault((source, key), []).append(record)
    result = {}
    matched_rows: set[int] = set()
    for listing in listings:
        matches = list(by_vin.get(str(listing.vin or '').strip().upper(), []))
        if not matches:
            for source, url in (
                (EngineType.AUTO_RU, listing.source_auto_ru),
                (EngineType.AVITO, listing.source_avito),
            ):
                key = canonical_listing_key(source, url)
                matches.extend(by_url.get((source, key), []) if key else [])
        unique = {int(record.get('row_number') or 0): record for record in matches}
        if unique:
            matched_rows.update(unique)
            chosen = sorted(unique.values(), key=lambda record: (not bool(record.get('active')), int(record.get('row_number') or 0)))[0]
            result[listing.id] = {**chosen, 'duplicate_rows': len(unique)}
    unmatched = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get('active')
        and int(record.get('row_number') or 0) not in matched_rows
    ]
    return result, unmatched


def _filter_unmatched_head_records(
    records: list[dict],
    *,
    query_text: str | None,
    brand: str | None,
    platform: EngineType | None,
) -> list[dict]:
    result = []
    for record in records:
        auto_url = _safe_listing_url(EngineType.AUTO_RU, record.get('auto_ru_url'))
        avito_url = _safe_listing_url(EngineType.AVITO, record.get('avito_url'))
        searchable = ' '.join(
            str(record.get(key) or '')
            for key in ('vin', 'brand_model', 'configuration', 'status', 'availability')
        ).casefold()
        if query_text and query_text.strip().casefold() not in searchable:
            continue
        if brand and brand.strip().casefold() not in str(record.get('brand_model') or '').casefold():
            continue
        if platform == EngineType.AUTO_RU and not auto_url:
            continue
        if platform == EngineType.AVITO and not avito_url:
            continue
        result.append({**record, 'auto_ru_url': auto_url, 'avito_url': avito_url})
    return result


def _trusted_observation_clause():
    return ListingObservation.scan_run.has(
        ScanRun.network_profile.in_(tuple(BUSINESS_TRUSTED_NETWORK_PROFILES))
    )


def _page_statistics(observations: list[ListingObservation]) -> dict:
    def summarize(rows: list[ListingObservation]) -> dict:
        page_counts = {page: sum(1 for item in rows if item.page_number == page) for page in (1, 2, 3)}
        total = len(rows)
        absolute_positions = [item.absolute_position for item in rows if item.absolute_position is not None]
        latest = rows[0] if rows else None
        return {
            'found_total': total,
            'page_counts': page_counts,
            'page_shares': {
                page: round(page_counts[page] * 100 / total, 1) if total else 0 for page in (1, 2, 3)
            },
            'average_absolute_position': round(sum(absolute_positions) / len(absolute_positions), 1)
            if absolute_positions
            else None,
            'latest': latest,
        }

    grouped: dict[str, list[ListingObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.filter_id, []).append(observation)
    by_filter = []
    for rows in grouped.values():
        row = summarize(rows)
        row.update(
            {
                'filter_id': rows[0].filter_id,
                'filter_name': rows[0].filter.name,
                'source': rows[0].source.value,
            }
        )
        by_filter.append(row)
    by_filter.sort(key=lambda row: (row['source'], row['filter_name']))
    return {'overall': summarize(observations), 'by_filter': by_filter}


def weekend_windows_for_last_days(days: int = 14) -> list[tuple[datetime, datetime]]:
    now = datetime.now(UTC)
    start = now - timedelta(days=days)
    windows = []
    current = start
    while current <= now:
        if current.weekday() >= 5:
            windows.append(
                (
                    current.replace(hour=0, minute=0, second=0, microsecond=0),
                    current.replace(hour=23, minute=59, second=59, microsecond=999999),
                )
            )
        current += timedelta(days=1)
    return windows


def kpi_overview(session, days: int = 7) -> dict[str, int | float]:
    since = datetime.now(UTC) - timedelta(days=days)
    found = (
        session.query(ListingObservation)
        .filter(
            ListingObservation.observed_at >= since,
            ListingObservation.found.is_(True),
            _trusted_observation_clause(),
        )
        .count()
    )
    missed = (
        session.query(ListingObservation)
        .filter(
            ListingObservation.observed_at >= since,
            ListingObservation.state.in_(
                [ObservationState.ABSENT_UNCERTAIN, ObservationState.ABSENT_CONFIRMED]
            ),
            _trusted_observation_clause(),
        )
        .count()
    )
    success_runs = (
        session.query(ScanRun)
        .filter(
            ScanRun.started_at >= since,
            ScanRun.status == ScanRunStatus.SUCCESS,
            ScanRun.network_profile.in_(tuple(BUSINESS_TRUSTED_NETWORK_PROFILES)),
        )
        .count()
    )
    return {
        'observed_found': found,
        'observed_missed': missed,
        'scan_runs_success': success_runs,
        'absent_active': session.query(AbsenceEpisode).filter(AbsenceEpisode.open.is_(True)).count(),
    }


def weekend_absence(session, days: int = 14) -> dict[str, int]:
    weekends = weekend_windows_for_last_days(days)
    started = {}
    for start, end in weekends:
        key = start.date().isoformat()
        started[key] = (
            session.query(AbsenceEpisode)
            .filter(AbsenceEpisode.started_at >= start, AbsenceEpisode.started_at <= end)
            .count()
        )
    return started


def weekend_summary(
    session, days: int = 14, now: datetime | None = None, timezone_name: str = 'Europe/Moscow'
) -> list[dict]:
    timezone = ZoneInfo(timezone_name)
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local_today = current.astimezone(timezone).date()
    rows = []
    for offset in range(days - 1, -1, -1):
        day = local_today - timedelta(days=offset)
        if day.weekday() < 5:
            continue
        local_start = datetime.combine(day, datetime.min.time(), tzinfo=timezone)
        local_end = local_start + timedelta(days=1)
        start = local_start.astimezone(UTC)
        end = local_end.astimezone(UTC)
        technical = (
            session.query(ListingObservation)
            .filter(
                ListingObservation.observed_at >= start,
                ListingObservation.observed_at < end,
                ListingObservation.state == ObservationState.TECHNICAL_ERROR,
            )
            .count()
        )
        confirmed = (
            session.query(ListingObservation)
            .filter(
                ListingObservation.observed_at >= start,
                ListingObservation.observed_at < end,
                ListingObservation.state == ObservationState.ABSENT_CONFIRMED,
            )
            .count()
        )
        overlapping = (
            session.query(AbsenceEpisode)
            .filter(
                AbsenceEpisode.started_at < end,
                (AbsenceEpisode.ended_at.is_(None) | (AbsenceEpisode.ended_at >= start)),
            )
            .count()
        )
        rows.append(
            {
                'date': day.isoformat(),
                'technical_errors': technical,
                'confirmed_absence_observations': confirmed,
                'absence_episodes_overlapping': overlapping,
            }
        )
    return rows


def filter_statistics(session, since: datetime) -> list[dict]:
    aggregates: dict[str, dict] = {}
    observations = (
        session.query(ListingObservation)
        .filter(
            ListingObservation.observed_at >= since,
            ListingObservation.filter.has(active=True),
            _trusted_observation_clause(),
        )
        .order_by(ListingObservation.observed_at.desc())
        .all()
    )
    for item in observations:
        row = aggregates.setdefault(
            item.filter_id,
            {
                'filter_id': item.filter_id,
                'name': item.filter.name,
                'source': item.source.value,
                'found': 0,
                'absent_confirmed': 0,
                'absent_uncertain': 0,
                'technical_error': 0,
                'page_1': 0,
                'page_2': 0,
                'page_3': 0,
            },
        )
        row[item.state.value] = row.get(item.state.value, 0) + 1
        if item.state == ObservationState.FOUND and 1 <= item.page_number <= 3:
            row[f'page_{item.page_number}'] += 1
    return sorted(aggregates.values(), key=lambda row: (row['source'], row['name']))


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def operational_status(
    session,
    *,
    now: datetime | None = None,
    interval_minutes: int | None = None,
    enabled_sources: list[str] | None = None,
) -> dict:
    current = _as_utc(now) or datetime.now(UTC)
    interval = interval_minutes or settings.scan_interval_minutes
    grace_minutes = max(15, interval // 4)
    overdue_after = timedelta(minutes=interval + grace_minutes)
    sources = enabled_sources if enabled_sources is not None else settings.scan_engines

    last_import = session.query(SourceImportSnapshot).order_by(SourceImportSnapshot.started_at.desc()).first()
    import_time = _as_utc(last_import.finished_at if last_import else None)
    if last_import is None:
        import_state = 'never_run'
        import_reason = 'Импорт ещё не выполнялся'
    elif last_import.blocked_by_schema_drift:
        import_state = 'quarantine'
        import_reason = 'Последний импорт помещён в карантин из-за изменения структуры'
    elif import_time is None or current - import_time > overdue_after:
        import_state = 'overdue'
        import_reason = 'Новый снимок источника не получен в ожидаемый интервал'
    else:
        import_state = 'healthy'
        import_reason = 'Последний импорт завершён без карантина'

    source_rows = []
    for source_name in sources:
        try:
            source = EngineType(source_name)
        except ValueError:
            source_rows.append(
                {
                    'source': source_name,
                    'state': 'invalid_configuration',
                    'reason': 'Неизвестное имя адаптера в SCAN_ENABLED_ENGINES',
                    'last_run_at': None,
                    'age_minutes': None,
                }
            )
            continue
        active_count = (
            session.query(SearchFilter)
            .filter(SearchFilter.source == source, SearchFilter.active.is_(True))
            .count()
        )
        last_run = (
            session.query(ScanRun)
            .filter(
                ScanRun.source == source,
                ScanRun.network_profile.in_(tuple(BUSINESS_TRUSTED_NETWORK_PROFILES)),
            )
            .order_by(ScanRun.started_at.desc())
            .first()
        )
        run_time = _as_utc(last_run.finished_at if last_run else None) or _as_utc(
            last_run.started_at if last_run else None
        )
        age_minutes = int((current - run_time).total_seconds() // 60) if run_time else None
        if active_count == 0:
            state = 'not_configured'
            reason = 'Нет утверждённых активных поисковых фильтров'
        elif last_run is None:
            state = 'never_run'
            reason = 'После настройки фильтра проверка ещё не выполнялась'
        elif run_time is None or current - run_time > overdue_after:
            state = 'overdue'
            reason = 'Плановый цикл просрочен'
        elif last_run.status == ScanRunStatus.SUCCESS:
            state = 'healthy'
            reason = 'Последний цикл завершён полностью'
        else:
            state = 'degraded'
            reason = last_run.notes.strip() if last_run.notes else 'Последний цикл завершён не полностью'
        source_rows.append(
            {
                'source': source.value,
                'state': state,
                'reason': reason,
                'last_run_at': run_time,
                'age_minutes': age_minutes,
                'active_filters': active_count,
                'last_run_status': last_run.status.value if last_run else None,
            }
        )

    states = {import_state, *(row['state'] for row in source_rows)}
    overall = 'healthy' if states == {'healthy'} else 'action_required'
    return {
        'overall': overall,
        'checked_at': current,
        'network_profile': settings.network_profile,
        'expected_interval_minutes': interval,
        'grace_minutes': grace_minutes,
        'import': {
            'state': import_state,
            'reason': import_reason,
            'last_finished_at': import_time,
        },
        'sources': source_rows,
    }


def latest_scan_runs_status(session, cycle_id: str | None = None) -> dict:
    rows = []
    for source_name in settings.scan_engines:
        try:
            source = EngineType(source_name)
        except ValueError:
            rows.append(
                {
                    'source': source_name,
                    'status': 'invalid_configuration',
                    'reason': 'unknown source in SCAN_ENABLED_ENGINES',
                }
            )
            continue
        query = session.query(ScanRun).filter(ScanRun.source == source)
        if cycle_id is not None:
            query = query.filter(ScanRun.cycle_id == cycle_id)
        run = query.order_by(ScanRun.started_at.desc(), ScanRun.id.desc()).first()
        if run is None:
            rows.append({'source': source.value, 'status': 'never_run'})
            continue

        observations = list(run.observations)
        state_counts = {
            state.value: sum(observation.state == state for observation in observations)
            for state in ObservationState
        }
        evidence_references: set[str] = set()
        evidence_page_numbers: set[int] = set()
        for observation in observations:
            diagnostics = (observation.raw_payload or {}).get('scan_diagnostics') or {}
            for key, value in diagnostics.items():
                if not key.startswith('page_') or not key.endswith('_evidence'):
                    continue
                if isinstance(value, str) and value.strip():
                    evidence_references.add(value)
                    try:
                        evidence_page_numbers.add(int(key.removeprefix('page_').removesuffix('_evidence')))
                    except ValueError:
                        continue
        rows.append(
            {
                'id': run.id,
                'cycle_id': run.cycle_id,
                'source': source.value,
                'status': run.status.value,
                'network_profile': run.network_profile,
                'started_at': run.started_at,
                'finished_at': run.finished_at,
                'filters_total': run.filters_total,
                'filters_ok': run.filters_ok,
                'pages_scanned': run.pages_scanned,
                'technical_errors': run.technical_errors,
                'observations': len(observations),
                'state_counts': state_counts,
                'evidence_files': len(evidence_references),
                'evidence_pages': sorted(evidence_page_numbers),
                'notes': run.notes,
            }
        )
    worker_query = session.query(ScanRun)
    if cycle_id is not None:
        worker_query = worker_query.filter(ScanRun.cycle_id == cycle_id)
    latest_worker_run = worker_query.order_by(ScanRun.started_at.desc(), ScanRun.id.desc()).first()
    return {
        'cycle_id': cycle_id,
        'requested_pages': settings.scan_pages_limit,
        'network_profile': settings.network_profile,
        'latest_worker_profile': latest_worker_run.network_profile if latest_worker_run else None,
        'runs': rows,
    }


def dashboard_context(session, days: int = 7) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    observation_counts = {
        state.value: session.query(ListingObservation)
        .filter(
            ListingObservation.observed_at >= since,
            ListingObservation.state == state,
            ListingObservation.filter.has(active=True),
            _trusted_observation_clause(),
        )
        .count()
        for state in ObservationState
    }
    recent_runs = session.query(ScanRun).order_by(ScanRun.started_at.desc()).limit(20).all()
    open_absences = (
        session.query(AbsenceEpisode)
        .join(SearchFilter, AbsenceEpisode.filter_id == SearchFilter.id)
        .join(Listing, AbsenceEpisode.listing_id == Listing.id)
        .filter(AbsenceEpisode.open.is_(True))
        .filter(SearchFilter.active.is_(True), Listing.is_active.is_(True))
        .order_by(AbsenceEpisode.started_at.desc())
        .limit(200)
        .all()
    )
    active_filters = (
        session.query(SearchFilter)
        .filter(SearchFilter.active.is_(True))
        .order_by(SearchFilter.source, SearchFilter.name)
        .all()
    )
    feedback = (
        session.query(ManagerFeedback)
        .filter(ManagerFeedback.status != FeedbackStatus.CONFIRMED)
        .order_by(ManagerFeedback.created_at.desc())
        .limit(100)
        .all()
    )
    last_import = session.query(SourceImportSnapshot).order_by(SourceImportSnapshot.started_at.desc()).first()
    missing_links = (
        session.query(Listing)
        .filter(
            Listing.is_active.is_(True),
            or_(Listing.source_auto_ru.is_(None), Listing.source_avito.is_(None)),
        )
        .order_by(Listing.brand, Listing.model, Listing.vin)
        .limit(200)
        .all()
    )
    latest_found = []
    found_keys = set()
    for observation in (
        session.query(ListingObservation)
        .filter(
            ListingObservation.state == ObservationState.FOUND,
            ListingObservation.filter.has(active=True),
            _trusted_observation_clause(),
        )
        .order_by(ListingObservation.observed_at.desc())
        .limit(1000)
    ):
        key = (observation.listing_id, observation.filter_id)
        if key in found_keys:
            continue
        found_keys.add(key)
        latest_found.append(observation)
        if len(latest_found) >= 200:
            break
    return {
        'days': days,
        'generated_at': datetime.now(UTC),
        'network_profile': settings.network_profile,
        'local_browser_active': any(
            run.network_profile == 'local_browser' and run.status == ScanRunStatus.SUCCESS
            for run in recent_runs
        ),
        'production_network_profiles': PRODUCTION_NETWORK_PROFILES,
        'listings_total': session.query(Listing).count(),
        'listings_active': session.query(Listing).filter(Listing.is_active.is_(True)).count(),
        'auto_links': session.query(Listing).filter(Listing.source_auto_ru.is_not(None)).count(),
        'avito_links': session.query(Listing).filter(Listing.source_avito.is_not(None)).count(),
        'active_filters': active_filters,
        'canonical_filter_catalog': FilterRegistryService(session).canonical_catalog_status(),
        'observation_counts': observation_counts,
        'open_absences': open_absences,
        'feedback': feedback,
        'recent_runs': recent_runs,
        'last_import': last_import,
        'missing_links': missing_links,
        'latest_found': latest_found,
        'latest_found_evidence': {
            observation.id: evidence_pages(observation) for observation in latest_found
        },
        'latest_found_urls': {
            observation.id: _safe_listing_url(observation.source, observation.listing_url)
            for observation in latest_found
        },
        'listing_urls': {
            listing.id: {
                'auto_ru': _safe_listing_url(EngineType.AUTO_RU, listing.source_auto_ru),
                'avito': _safe_listing_url(EngineType.AVITO, listing.source_avito),
            }
            for listing in missing_links
        },
        'filter_statistics': filter_statistics(session, since),
        'open_feedback_count': len(feedback),
        'technical_runs': session.query(ScanRun)
        .filter(ScanRun.started_at >= since, ScanRun.technical_errors > 0)
        .count(),
        'weekend_summary': weekend_summary(session, days=max(days, 14)),
        'operational_status': operational_status(session),
        'dealer_candidates': session.query(DealerListingCandidate)
        .filter(DealerListingCandidate.active.is_(True))
        .order_by(DealerListingCandidate.source, DealerListingCandidate.title)
        .limit(300)
        .all(),
        'dealer_discovery_runs': session.query(DealerDiscoveryRun)
        .order_by(DealerDiscoveryRun.started_at.desc())
        .limit(10)
        .all(),
        # The A1Auto catalogue is an independent publication check, not a
        # marketplace-visibility result.  Its audit lives outside the DB.
        'company_site_audit': company_site_audit_context(),
    }


def feedback_queue_context(
    session,
    *,
    status: str = 'open',
    severity: str | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    safe_page = max(page, 1)
    safe_page_size = min(max(page_size, 10), 100)
    query = session.query(ManagerFeedback)
    if status == 'open':
        query = query.filter(ManagerFeedback.status != FeedbackStatus.CONFIRMED)
    elif status != 'all':
        query = query.filter(ManagerFeedback.status == FeedbackStatus(status))
    if severity:
        query = query.filter(ManagerFeedback.severity == severity)
    if category:
        query = query.filter(ManagerFeedback.category == category)

    total = query.count()
    pages_total = max(1, (total + safe_page_size - 1) // safe_page_size)
    safe_page = min(safe_page, pages_total)
    tickets = (
        query.order_by(ManagerFeedback.created_at.desc(), ManagerFeedback.id)
        .offset((safe_page - 1) * safe_page_size)
        .limit(safe_page_size)
        .all()
    )
    filter_ids = {item.filter_id for item in tickets if item.filter_id}
    observation_ids = {item.observed_id for item in tickets if item.observed_id}
    filters = (
        {item.id: item for item in session.query(SearchFilter).filter(SearchFilter.id.in_(filter_ids)).all()}
        if filter_ids
        else {}
    )
    observations = (
        {
            item.id: item
            for item in session.query(ListingObservation)
            .filter(ListingObservation.id.in_(observation_ids))
            .all()
        }
        if observation_ids
        else {}
    )
    status_counts = {
        item.value: session.query(ManagerFeedback).filter(ManagerFeedback.status == item).count()
        for item in FeedbackStatus
    }
    return {
        'status': status,
        'severity': severity or '',
        'category': category or '',
        'page': safe_page,
        'page_size': safe_page_size,
        'pages_total': pages_total,
        'total': total,
        'status_counts': status_counts,
        'statuses': [item.value for item in FeedbackStatus],
        'severities': sorted(ALLOWED_SEVERITIES),
        'categories': sorted(ALLOWED_CATEGORIES),
        'rows': [
            {
                'ticket': ticket,
                'filter': filters.get(ticket.filter_id),
                'observation': observations.get(ticket.observed_id),
                'events': sorted(ticket.events, key=lambda item: (item.created_at, item.id)),
                'allowed_next': [
                    item.value
                    for item in sorted(ALLOWED_TRANSITIONS[ticket.status], key=lambda item: item.value)
                ],
            }
            for ticket in tickets
        ],
    }


def observation_history_context(
    session,
    *,
    days: int = 30,
    source: EngineType | None = None,
    brand: str | None = None,
    model: str | None = None,
    state: ObservationState | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    safe_days = min(max(days, 1), 365)
    safe_page = max(page, 1)
    safe_page_size = min(max(page_size, 10), 200)
    since = datetime.now(UTC) - timedelta(days=safe_days)
    query = (
        session.query(ListingObservation)
        .join(Listing, ListingObservation.listing_id == Listing.id)
        .join(SearchFilter, ListingObservation.filter_id == SearchFilter.id)
        .filter(ListingObservation.observed_at >= since)
    )
    if source is not None:
        query = query.filter(ListingObservation.source == source)
    if brand:
        query = query.filter(Listing.brand.ilike(brand.strip()))
    if model:
        query = query.filter(Listing.model.ilike(model.strip()))
    if state is not None:
        query = query.filter(ListingObservation.state == state)

    total = query.count()
    pages_total = max(1, (total + safe_page_size - 1) // safe_page_size)
    safe_page = min(safe_page, pages_total)
    observations = (
        query.order_by(ListingObservation.observed_at.desc(), ListingObservation.id)
        .offset((safe_page - 1) * safe_page_size)
        .limit(safe_page_size)
        .all()
    )
    brand_options = [
        value
        for (value,) in session.query(Listing.brand)
        .filter(Listing.brand.is_not(None), Listing.brand != '')
        .distinct()
        .order_by(Listing.brand)
        .all()
    ]
    model_options = [
        value
        for (value,) in session.query(Listing.model)
        .filter(Listing.model.is_not(None), Listing.model != '')
        .distinct()
        .order_by(Listing.model)
        .all()
    ]
    return {
        'days': safe_days,
        'source': source.value if source else '',
        'brand': brand or '',
        'model': model or '',
        'state': state.value if state else '',
        'page': safe_page,
        'page_size': safe_page_size,
        'pages_total': pages_total,
        'total': total,
        'brand_options': brand_options,
        'model_options': model_options,
        'states': [item.value for item in ObservationState],
        'rows': [
            {
                'observation': observation,
                'evidence_pages': evidence_pages(observation),
                'evidence_kind': 'card' if has_card_evidence(observation) else 'page',
                'listing_url': _safe_listing_url(observation.source, observation.listing_url),
            }
            for observation in observations
        ],
    }


def listing_catalog_context(
    session,
    *,
    query_text: str | None = None,
    brand: str | None = None,
    platform: EngineType | None = None,
    page: int = 1,
    page_size: int = 500,
) -> dict:
    safe_page = max(page, 1)
    safe_page_size = min(max(page_size, 12), 500)
    all_active_listings = (
        session.query(Listing)
        .filter(Listing.is_active.is_(True))
        .order_by(Listing.brand, Listing.model, Listing.year.desc(), Listing.vin)
        .all()
    )
    head_records, unmatched_head_records = _head_records_for_listings(all_active_listings)
    query = session.query(Listing).filter(Listing.is_active.is_(True))
    if query_text:
        pattern = f'%{query_text.strip()}%'
        query = query.filter(
            or_(
                Listing.vin.ilike(pattern),
                Listing.brand.ilike(pattern),
                Listing.model.ilike(pattern),
            )
        )
    if brand:
        query = query.filter(Listing.brand.ilike(brand.strip()))
    if platform == EngineType.AUTO_RU:
        query = query.filter(Listing.source_auto_ru.is_not(None))
    elif platform == EngineType.AVITO:
        query = query.filter(Listing.source_avito.is_not(None))

    total = query.count()
    pages_total = max(1, (total + safe_page_size - 1) // safe_page_size)
    safe_page = min(safe_page, pages_total)
    listings = (
        query.order_by(Listing.brand, Listing.model, Listing.year.desc(), Listing.vin)
        .offset((safe_page - 1) * safe_page_size)
        .limit(safe_page_size)
        .all()
    )
    listing_ids = [item.id for item in listings]
    listings_by_id = {item.id: item for item in listings}
    latest_any: dict[tuple[str, EngineType], ListingObservation] = {}
    latest_found: dict[tuple[str, EngineType], ListingObservation] = {}
    latest_card: dict[tuple[str, EngineType], ListingObservation] = {}
    latest_reconciliation: dict[tuple[str, EngineType], ListingReconciliation] = {}
    if listing_ids:
        for observation in (
            session.query(ListingObservation)
            .filter(
                ListingObservation.listing_id.in_(listing_ids),
                _trusted_observation_clause(),
            )
            .order_by(ListingObservation.observed_at.desc())
        ):
            key = (observation.listing_id, observation.source)
            listing = listings_by_id[observation.listing_id]
            current_url = listing.source_auto_ru if observation.source == EngineType.AUTO_RU else listing.source_avito
            expected_key = (observation.raw_payload or {}).get('expected_listing_key')
            observed_key = expected_key or canonical_listing_key(observation.source, observation.listing_url)
            if observed_key != canonical_listing_key(observation.source, current_url):
                continue
            latest_any.setdefault(key, observation)
            if observation.state == ObservationState.FOUND:
                latest_found.setdefault(key, observation)
            if observation.state == ObservationState.FOUND and has_card_evidence(observation):
                latest_card.setdefault(key, observation)
        for record in (
            session.query(ListingReconciliation)
            .filter(ListingReconciliation.listing_id.in_(listing_ids))
            .order_by(ListingReconciliation.checked_at.desc(), ListingReconciliation.id.desc())
        ):
            key = (record.listing_id, record.source)
            current = listings_by_id[record.listing_id]
            current_url = current.source_auto_ru if record.source == EngineType.AUTO_RU else current.source_avito
            if canonical_listing_key(record.source, record.url) == canonical_listing_key(record.source, current_url):
                latest_reconciliation.setdefault(key, record)

    brand_options = [
        value
        for (value,) in session.query(Listing.brand)
        .filter(Listing.is_active.is_(True), Listing.brand.is_not(None), Listing.brand != '')
        .distinct()
        .order_by(Listing.brand)
        .all()
    ]
    filtered_unmatched = _filter_unmatched_head_records(
        unmatched_head_records,
        query_text=query_text,
        brand=brand,
        platform=platform,
    )
    return {
        'query': query_text or '',
        'brand': brand or '',
        'platform': platform.value if platform else '',
        'page': safe_page,
        'pages_total': pages_total,
        'total': total,
        'expected_total': total + len(filtered_unmatched),
        'unmatched_total': len(filtered_unmatched),
        'unmatched_marketing': filtered_unmatched,
        'brand_options': brand_options,
        'cards': [
            _listing_catalog_card(
                listing,
                latest_any,
                latest_found,
                latest_card,
                latest_reconciliation,
                head_records.get(listing.id),
            )
            for listing in listings
        ],
    }


def _listing_catalog_card(
    listing: Listing,
    latest_any: dict[tuple[str, EngineType], ListingObservation],
    latest_found: dict[tuple[str, EngineType], ListingObservation],
    latest_card: dict[tuple[str, EngineType], ListingObservation],
    latest_reconciliation: dict[tuple[str, EngineType], ListingReconciliation],
    marketing: dict | None,
) -> dict:
    observations = {
        source: latest_found.get((listing.id, source))
        for source in (EngineType.AUTO_RU, EngineType.AVITO)
    }
    latest_results = {
        source: latest_any.get((listing.id, source))
        for source in (EngineType.AUTO_RU, EngineType.AVITO)
    }
    available = [
        latest_card.get((listing.id, source))
        for source in (EngineType.AUTO_RU, EngineType.AVITO)
        if latest_card.get((listing.id, source)) is not None
    ]
    for source, observation in observations.items():
        current_url = listing.source_auto_ru if source == EngineType.AUTO_RU else listing.source_avito
        if observation and canonical_listing_key(source, observation.listing_url) != canonical_listing_key(source, current_url):
            observations[source] = None
    available = [observation for observation in available if canonical_listing_key(observation.source, observation.listing_url)
                 == canonical_listing_key(observation.source, listing.source_auto_ru if observation.source == EngineType.AUTO_RU else listing.source_avito)]
    preview = max(available, key=lambda item: item.observed_at) if available else None
    direct_previews = []
    for source in (EngineType.AUTO_RU, EngineType.AVITO):
        reconciliation = latest_reconciliation.get((listing.id, source))
        direct = (reconciliation.details or {}).get('direct_inspection') if reconciliation else None
        if reconciliation and isinstance(direct, dict) and direct.get('evidence'):
            direct_previews.append((reconciliation, direct))
    direct_preview = max(direct_previews, key=lambda item: item[0].checked_at) if direct_previews else None

    direct_labels = {
        'active': 'Карточка работает',
        'sold': 'Автомобиль продан',
        'unpublished': 'Объявление снято',
        'closed': 'Объявление закрыто',
        'removed': 'Есть отметка о снятии или продаже',
        'redirected': 'Ссылка ведёт на другую страницу',
        'blocked': 'Площадка запросила проверку',
        'skipped_after_block': 'Не проверено после блокировки',
        'http_error': 'Ошибка открытия ссылки',
        'ambiguous_status_text': 'Статус страницы неоднозначен',
        'limit_reached': 'Не проверено: защитный лимит',
        'unknown': 'Статус карточки не распознан',
    }
    platforms = {}
    for source in (EngineType.AUTO_RU, EngineType.AVITO):
        name = source.value
        url = listing.source_auto_ru if source == EngineType.AUTO_RU else listing.source_avito
        found = observations[source]
        latest = latest_results[source]
        if found and latest and found.run_id != latest.run_id:
            found = None
        reconciliation = latest_reconciliation.get((listing.id, source))
        direct = (reconciliation.details or {}).get('direct_inspection') if reconciliation else None
        direct = direct if isinstance(direct, dict) else {}
        code = direct.get('status_code') or direct.get('state')
        direct_label = direct_labels.get(code)
        if not direct_label and not url:
            direct_label = 'Нет корректной прямой ссылки'
        elif not direct_label and reconciliation and reconciliation.state == 'missing_link':
            direct_label = 'Ссылка отсутствует или некорректна'
        elif not direct_label:
            direct_label = 'Прямая карточка ещё не проверена'
        search_state = 'found' if found else latest.state.value if latest else 'not_checked'
        platforms[name] = {
            'url': _safe_listing_url(source, url),
            'observation': found,
            'search_state': search_state,
            'search_label': {
                'found': 'Найдено в поиске',
                'absent_confirmed': 'Не найдено повторно',
                'absent_uncertain': 'Не найдено — нужна перепроверка',
                'filter_mismatch': 'Не соответствует фильтру',
                'technical_error': 'Поиск проверить не удалось',
                'not_checked': 'Поиск ещё не проверялся',
            }.get(search_state, search_state),
            'reconciliation': reconciliation,
            'direct': direct,
            'direct_label': direct_label,
            'direct_class': (
                'found'
                if code == 'active'
                else 'absent_confirmed'
                if code in {'removed', 'sold', 'unpublished', 'closed'}
                else 'technical_error'
                if code
                else 'not_checked'
            ),
            'proof': f'/api/v1/reconciliations/{reconciliation.id}/evidence'
            if reconciliation and direct.get('evidence') else None,
        }
    return {
        'listing': listing,
        'observation': preview,
        'evidence_pages': evidence_pages(preview) if preview else [],
        'preview_url': (
            f'/api/v1/reconciliations/{direct_preview[0].id}/evidence'
            if direct_preview else
            f'/api/v1/observations/{preview.id}/evidence/{evidence_pages(preview)[0]}'
            if preview and evidence_pages(preview) else None
        ),
        'preview_kind': 'Прямая карточка' if direct_preview else 'Превью поиска' if preview else None,
        'marketing': marketing or {},
        'platforms': platforms,
    }


def listing_detail_context(session, listing_id: str, observation_limit: int = 200) -> dict | None:
    listing = session.get(Listing, listing_id)
    if listing is None:
        return None
    observations = (
        session.query(ListingObservation)
        .filter(ListingObservation.listing_id == listing_id)
        .order_by(ListingObservation.observed_at.desc())
        .limit(min(max(observation_limit, 10), 500))
        .all()
    )
    found_observations = (
        session.query(ListingObservation)
        .filter(
            ListingObservation.listing_id == listing_id,
            ListingObservation.state == ObservationState.FOUND,
            _trusted_observation_clause(),
        )
        .order_by(ListingObservation.observed_at.desc())
        .all()
    )
    episodes = (
        session.query(AbsenceEpisode)
        .filter(AbsenceEpisode.listing_id == listing_id)
        .order_by(AbsenceEpisode.started_at.desc())
        .all()
    )
    feedback = (
        session.query(ManagerFeedback)
        .filter(ManagerFeedback.listing_id == listing_id)
        .order_by(ManagerFeedback.created_at.desc())
        .all()
    )
    link_events = sorted(listing.link_events, key=lambda item: item.created_at, reverse=True)
    return {
        'listing': listing,
        'links': {
            'auto_ru': _safe_listing_url(EngineType.AUTO_RU, listing.source_auto_ru),
            'avito': _safe_listing_url(EngineType.AVITO, listing.source_avito),
        },
        'observations': [
            {
                'observation': observation,
                'evidence_pages': evidence_pages(observation),
                'evidence_kind': 'card' if has_card_evidence(observation) else 'page',
                'listing_url': _safe_listing_url(observation.source, observation.listing_url),
            }
            for observation in observations
        ],
        'episodes': episodes,
        'feedback': feedback,
        'link_events': link_events,
        'registry_events': session.query(ListingChangeEvent).filter_by(listing_id=listing_id)
        .order_by(ListingChangeEvent.created_at.desc()).limit(200).all(),
        'page_statistics': _page_statistics(found_observations),
        'previews': [
            {
                'observation': observation,
                'evidence_pages': evidence_pages(observation),
                'listing_url': _safe_listing_url(observation.source, observation.listing_url),
            }
            for observation in observations
            if observation.state == ObservationState.FOUND
            and observation.listing_url
            and has_card_evidence(observation)
            and observation.scan_run.network_profile in BUSINESS_TRUSTED_NETWORK_PROFILES
        ][:8],
    }
