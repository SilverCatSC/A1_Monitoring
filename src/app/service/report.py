from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_

from app.models import (
    AbsenceEpisode,
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
        .filter(ListingObservation.observed_at >= since)
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


def dashboard_context(session, days: int = 7) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    observation_counts = {
        state.value: session.query(ListingObservation)
        .filter(
            ListingObservation.observed_at >= since,
            ListingObservation.state == state,
        )
        .count()
        for state in ObservationState
    }
    recent_runs = session.query(ScanRun).order_by(ScanRun.started_at.desc()).limit(20).all()
    open_absences = (
        session.query(AbsenceEpisode)
        .filter(AbsenceEpisode.open.is_(True))
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
        .filter(ListingObservation.state == ObservationState.FOUND)
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
        'listings_total': session.query(Listing).count(),
        'listings_active': session.query(Listing).filter(Listing.is_active.is_(True)).count(),
        'auto_links': session.query(Listing).filter(Listing.source_auto_ru.is_not(None)).count(),
        'avito_links': session.query(Listing).filter(Listing.source_avito.is_not(None)).count(),
        'active_filters': active_filters,
        'observation_counts': observation_counts,
        'open_absences': open_absences,
        'feedback': feedback,
        'recent_runs': recent_runs,
        'last_import': last_import,
        'missing_links': missing_links,
        'latest_found': latest_found,
        'filter_statistics': filter_statistics(session, since),
        'open_feedback_count': len(feedback),
        'technical_runs': session.query(ScanRun)
        .filter(ScanRun.started_at >= since, ScanRun.technical_errors > 0)
        .count(),
        'weekend_summary': weekend_summary(session, days=max(days, 14)),
    }
