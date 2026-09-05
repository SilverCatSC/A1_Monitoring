from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_

from app.config import settings
from app.models import (
    AbsenceEpisode,
    DealerDiscoveryRun,
    DealerListingCandidate,
    EngineType,
    FeedbackStatus,
    Listing,
    ListingObservation,
    ManagerFeedback,
    ObservationState,
    ScanRun,
    ScanRunStatus,
    SearchFilter,
    SourceImportSnapshot,
)
from app.scraper.base import is_marketplace_listing_url
from app.service.evidence import evidence_pages
from app.service.filters import FilterRegistryService


def _safe_listing_url(source: EngineType, value: str | None) -> str | None:
    return value if is_marketplace_listing_url(source, value) else None


def _page_statistics(observations: list[ListingObservation]) -> dict:
    def summarize(rows: list[ListingObservation]) -> dict:
        page_counts = {
            page: sum(1 for item in rows if item.page_number == page) for page in (1, 2, 3)
        }
        total = len(rows)
        absolute_positions = [
            item.absolute_position for item in rows if item.absolute_position is not None
        ]
        latest = rows[0] if rows else None
        return {
            'found_total': total,
            'page_counts': page_counts,
            'page_shares': {
                page: round(page_counts[page] * 100 / total, 1) if total else 0
                for page in (1, 2, 3)
            },
            'average_absolute_position': round(
                sum(absolute_positions) / len(absolute_positions), 1
            )
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
        .filter(ListingObservation.observed_at >= since, ListingObservation.found.is_(True))
        .count()
    )
    missed = (
        session.query(ListingObservation)
        .filter(
            ListingObservation.observed_at >= since,
            ListingObservation.state.in_(
                [ObservationState.ABSENT_UNCERTAIN, ObservationState.ABSENT_CONFIRMED]
            ),
        )
        .count()
    )
    success_runs = (
        session.query(ScanRun)
        .filter(ScanRun.started_at >= since, ScanRun.status == ScanRunStatus.SUCCESS)
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

    last_import = (
        session.query(SourceImportSnapshot)
        .order_by(SourceImportSnapshot.started_at.desc())
        .first()
    )
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
            .filter(ScanRun.source == source)
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


def latest_scan_runs_status(session) -> dict:
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
        run = (
            session.query(ScanRun)
            .filter(ScanRun.source == source)
            .order_by(ScanRun.started_at.desc(), ScanRun.id.desc())
            .first()
        )
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
                        evidence_page_numbers.add(
                            int(key.removeprefix('page_').removesuffix('_evidence'))
                        )
                    except ValueError:
                        continue
        rows.append(
            {
                'id': run.id,
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
    return {
        'requested_pages': settings.scan_pages_limit,
        'network_profile': settings.network_profile,
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
    last_import = (
        session.query(SourceImportSnapshot)
        .order_by(SourceImportSnapshot.started_at.desc())
        .first()
    )
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
        'listings_total': session.query(Listing).count(),
        'listings_active': session.query(Listing).filter(Listing.is_active.is_(True)).count(),
        'auto_links': session.query(Listing).filter(Listing.source_auto_ru.is_not(None)).count(),
        'avito_links': session.query(Listing).filter(Listing.source_avito.is_not(None)).count(),
        'active_filters': active_filters,
        'canonical_filter_catalog': FilterRegistryService(
            session
        ).canonical_catalog_status(),
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
    page_size: int = 24,
) -> dict:
    safe_page = max(page, 1)
    safe_page_size = min(max(page_size, 12), 96)
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
    latest_found: dict[str, ListingObservation] = {}
    if listing_ids:
        for observation in (
            session.query(ListingObservation)
            .filter(
                ListingObservation.listing_id.in_(listing_ids),
                ListingObservation.state == ObservationState.FOUND,
            )
            .order_by(ListingObservation.observed_at.desc())
        ):
            latest_found.setdefault(observation.listing_id, observation)

    brand_options = [
        value
        for (value,) in session.query(Listing.brand)
        .filter(Listing.is_active.is_(True), Listing.brand.is_not(None), Listing.brand != '')
        .distinct()
        .order_by(Listing.brand)
        .all()
    ]
    return {
        'query': query_text or '',
        'brand': brand or '',
        'platform': platform.value if platform else '',
        'page': safe_page,
        'pages_total': pages_total,
        'total': total,
        'brand_options': brand_options,
        'cards': [
            {
                'listing': listing,
                'observation': latest_found.get(listing.id),
                'evidence_pages': evidence_pages(latest_found[listing.id])
                if listing.id in latest_found
                else [],
                'auto_url': _safe_listing_url(EngineType.AUTO_RU, listing.source_auto_ru),
                'avito_url': _safe_listing_url(EngineType.AVITO, listing.source_avito),
            }
            for listing in listings
        ],
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
                'listing_url': _safe_listing_url(observation.source, observation.listing_url),
            }
            for observation in observations
        ],
        'episodes': episodes,
        'feedback': feedback,
        'link_events': link_events,
        'page_statistics': _page_statistics(found_observations),
        'previews': [
            {
                'observation': observation,
                'evidence_pages': evidence_pages(observation),
                'listing_url': _safe_listing_url(observation.source, observation.listing_url),
            }
            for observation in observations
            if observation.state == ObservationState.FOUND and observation.listing_url
        ][:8],
    }
