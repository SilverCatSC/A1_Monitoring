"""Keep a host run pending while transient workbook connectivity recovers."""
from __future__ import annotations

import time
from contextvars import ContextVar

import httpx

from app.config import settings

_source_progress = ContextVar('monitoring_source_progress', default=None)


def transient_source_error(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, httpx.TransportError):
            return True
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code in {429, 500, 502, 503, 504}
        error = error.__cause__
    return False


def source_with_recovery(read, *, progress=None, phase, enabled=None, sleep=None):
    sleep = sleep or time.sleep
    progress = progress or _source_progress.get()
    if enabled is None:
        enabled = (settings.local_browser_host_admission
                   and settings.network_profile in {'local_browser', 'local_no_vpn', 'local_routed_diagnostic'})
    attempt = 0
    while True:
        try:
            token = _source_progress.set(progress)
            try:
                value = read()
            finally:
                _source_progress.reset(token)
        except Exception as error:
            if not enabled or not transient_source_error(error):
                raise
            attempt += 1
            delay = min(attempt * 15, 60)
            if progress:
                progress({'event': 'source_recovery_waiting', 'phase': phase,
                          'error_kind': type(error).__name__, 'retry_seconds': delay,
                          'attempt': attempt, 'stale_data_used': False})
            sleep(delay)
        else:
            if attempt and progress:
                progress({'event': 'source_recovery_resolved', 'phase': phase, 'attempts': attempt})
            return value
