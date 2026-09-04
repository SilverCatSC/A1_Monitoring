from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
        'open_feedback_count': len(feedback),
        'technical_runs': session.query(ScanRun)
        .filter(ScanRun.started_at >= since, ScanRun.technical_errors > 0)
        .count(),
        'weekend_absence': weekend_absence(session, days=max(days, 14)),
    }
