"""Aggregate-only projection for an explicitly approved public export."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import BUSINESS_TRUSTED_NETWORK_PROFILES
from app.models import (
    EngineType,
    FeedbackStatus,
    Listing,
    ListingObservation,
    ManagerFeedback,
    ObservationState,
    ScanRun,
)


def public_report_context(session, *, now: datetime | None = None) -> dict:
    """Return only aggregate marketplace health; never add per-car data here."""
    current = now or datetime.now(UTC)
    since = current - timedelta(days=7)
    source_rows = []
    for source in EngineType:
        run = (
            session.query(ScanRun)
            .filter(
                ScanRun.source == source,
                ScanRun.network_profile.in_(tuple(BUSINESS_TRUSTED_NETWORK_PROFILES)),
            )
            .order_by(ScanRun.started_at.desc(), ScanRun.id.desc())
            .first()
        )
        source_rows.append(
            {
                'source': source.value,
                'last_checked_at': run.finished_at if run and run.finished_at else (run.started_at if run else None),
                'last_status': run.status.value if run else 'never_run',
                'filters_total': run.filters_total if run else 0,
                'filters_ok': run.filters_ok if run else 0,
                'technical_errors': run.technical_errors if run else 0,
            }
        )
    trusted_run = ListingObservation.scan_run.has(
        ScanRun.network_profile.in_(tuple(BUSINESS_TRUSTED_NETWORK_PROFILES))
    )
    return {
        'schema_version': 1,
        'generated_at': current,
        'period_start': since,
        'summary': {
            'active_listings': session.query(Listing).filter(Listing.is_active.is_(True)).count(),
            'found_observations': session.query(ListingObservation)
            .filter(
                ListingObservation.observed_at >= since,
                ListingObservation.state == ObservationState.FOUND,
                trusted_run,
            )
            .count(),
            'technical_observations': session.query(ListingObservation)
            .filter(
                ListingObservation.observed_at >= since,
                ListingObservation.state == ObservationState.TECHNICAL_ERROR,
                trusted_run,
            )
            .count(),
            'open_feedback': session.query(ManagerFeedback)
            .filter(ManagerFeedback.status != FeedbackStatus.CONFIRMED)
            .count(),
        },
        'sources': source_rows,
        'privacy': {
            'contains_vin': False,
            'contains_marketplace_urls': False,
            'contains_feedback_text': False,
        },
    }
