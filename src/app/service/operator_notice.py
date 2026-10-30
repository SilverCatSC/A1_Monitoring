"""Deduplicated, privacy-bounded local notices for a manually started run."""
from __future__ import annotations

from datetime import datetime


class OperatorNoticeTracker:
    def __init__(self, started_at: datetime):
        if started_at.tzinfo is None:
            raise ValueError('timezone-aware run start required')
        self.started_at = started_at
        self.episode = None

    def update(self, progress: dict) -> tuple[str, str] | None:
        try:
            stamp = datetime.fromisoformat(str(progress.get('updated_at', '')).replace('Z', '+00:00'))
            if stamp.tzinfo is None or stamp < self.started_at:
                return None
        except (ValueError, TypeError):
            return None
        status = progress.get('status')
        current = progress.get('current') or {}
        # URLs identify separate pending pages internally, but never appear in
        # a notification. No VIN, price, account or customer data is exposed.
        key = (progress.get('cycle_id'), status, current.get('source'),
               current.get('url'), current.get('page'), current.get('filter_id'))
        messages = {
            'waiting_captcha': ('Monitoring needs your help', 'Solve CAPTCHA in the monitoring Chrome window. The run is paused; no adverts are marked inactive.'),
            'waiting_navigation': ('Monitoring needs your help', 'The marketplace page has not loaded. Check the monitoring Chrome window; the run is paused.'),
            'completed': ('Monitoring finished', 'Open the local dashboard to review the report. A completed run can still contain marketing issues.'),
            'partial': ('Monitoring finished with gaps', 'Open the local dashboard. Some checks remain unverified; review the incomplete results.'),
            'failed': ('Monitoring failed', 'Check the local dashboard and host run log. No successful result is implied.'),
        }
        if status not in messages:
            self.episode = None
            return None
        if key == self.episode:
            return None
        self.episode = key
        return messages[status]
