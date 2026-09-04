from __future__ import annotations

from datetime import datetime, timedelta

from app.models import AbsenceEpisode, ListingObservation, ObservationState, ScanRun, ScanRunStatus


def weekend_windows_for_last_days(days: int = 14) -> list[tuple[datetime, datetime]]:
    now = datetime.utcnow()
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
    since = datetime.utcnow() - timedelta(days=days)
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
