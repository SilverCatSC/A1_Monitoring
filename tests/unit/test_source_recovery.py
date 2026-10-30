import httpx
import pytest

from app.service.scan_progress import ScanProgressTracker
from app.service.source_recovery import source_with_recovery


def test_transient_feed_failures_wait_then_continue_to_report(tmp_path):
    tracker = ScanProgressTracker(str(tmp_path))
    tracker({'event': 'link_preflight_started'})
    attempts, sleeps = [], []

    def read():
        attempts.append(1)
        if len(attempts) < 3:
            try:
                raise httpx.ConnectTimeout('network unavailable')
            except httpx.ConnectTimeout as error:
                raise ValueError('outbound feed snapshot unavailable') from error
        return {'fresh_feed': True}

    def wait(seconds):
        sleeps.append(seconds)
        assert tracker.state['status'] == 'waiting_source'
        assert tracker.state['source_recovery']['stale_data_used'] is False

    feed = source_with_recovery(read, progress=tracker, phase='placement_feeds', enabled=True, sleep=wait)
    # The same caller can finish its report; no new browser or cycle is needed.
    report = {'feed': feed, 'report_generated': True}
    assert report['feed']['fresh_feed'] and sleeps == [15, 30]
    assert tracker.state['status'] == 'link_sync'
    assert tracker.state['source_recovery']['resolved']


@pytest.mark.parametrize('status', [400, 401, 403, 404])
def test_permission_and_invalid_source_errors_are_not_retried(status):
    def read():
        response = httpx.Response(status, request=httpx.Request('GET', 'https://docs.google.com/test'))
        response.raise_for_status()
    with pytest.raises(httpx.HTTPStatusError):
        source_with_recovery(read, phase='registry', enabled=True,
                             sleep=lambda _: pytest.fail('must not retry permission/schema errors'))


def test_interrupt_is_never_swallowed():
    def read():
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        source_with_recovery(read, phase='registry', enabled=True)
