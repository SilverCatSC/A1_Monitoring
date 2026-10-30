"""Read-only operational assessment; never equate missing heartbeats with ad removal."""
from __future__ import annotations

from datetime import UTC, datetime

ACTIVE_STATUSES = frozenset({
    'starting', 'preparing', 'running', 'reconciling', 'link_sync', 'checking_cards',
    'waiting_captcha', 'waiting_navigation', 'waiting_source',
})


def assess_scan_health(progress: dict, *, now: datetime | None = None, stale_seconds: int = 180) -> dict:
    """Detect blocked work even when its wait loop keeps writing fresh events.

    This assesses the progress record, not process liveness or report integrity.
    A terminal status does not establish complete marketplace coverage.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None or stale_seconds <= 0:
        raise ValueError('timezone-aware clock and positive stale threshold required')
    status = progress.get('status', 'unavailable')
    age = None
    try:
        updated = datetime.fromisoformat(str(progress.get('updated_at', '')).replace('Z', '+00:00'))
        if updated.tzinfo is None:
            raise ValueError('missing timezone')
        age = (now - updated).total_seconds()
        if age < -30:
            age = None
    except (ValueError, TypeError):
        pass
    stale = status in ACTIVE_STATUSES and (age is None or age > stale_seconds)
    state, action = 'unknown', 'inspect_local_service'
    if status == 'idle':
        state, action = 'idle', 'none'
    elif status == 'waiting_source':
        state, action = 'waiting_source', 'automatic_retry'
    elif status in {'waiting_captcha', 'waiting_navigation'}:
        state = 'blocked'
        action = 'resolve_captcha' if status == 'waiting_captcha' else 'restore_page_access'
    elif stale:
        state, action = 'heartbeat_missing', 'inspect_runner'
    elif status in ACTIVE_STATUSES:
        state, action = 'running', 'none'
    elif status in {'completed', 'partial', 'failed'}:
        state = status
        action = {'completed': 'review_report', 'partial': 'review_incomplete_checks',
                  'failed': 'inspect_runner'}[status]
    return {
        'schema_version': 1,
        'state': state,
        'action': action,
        'cycle_id': progress.get('cycle_id'),
        'heartbeat_stale': stale,
        'heartbeat_age_seconds': round(max(age, 0)) if age is not None else None,
        'requires_attention': state not in {'idle', 'running', 'completed', 'waiting_source'},
        'process_liveness_verified': False,
        'coverage_verified': False,
    }
