from datetime import UTC, datetime, timedelta

import pytest

from app.service.scan_health import assess_scan_health

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


def record(status, seconds=0):
    return {'status': status, 'cycle_id': 'example',
            'updated_at': (NOW - timedelta(seconds=seconds)).isoformat()}


@pytest.mark.parametrize('status,action', [
    ('waiting_captcha', 'resolve_captcha'), ('waiting_navigation', 'restore_page_access'),
])
def test_fresh_wait_heartbeats_do_not_imply_unattended_progress(status, action):
    result = assess_scan_health(record(status), now=NOW)
    assert result['state'] == 'blocked'
    assert result['action'] == action
    assert result['requires_attention']
    assert not result['heartbeat_stale']
    assert not result['coverage_verified']


@pytest.mark.parametrize('status', ['starting', 'checking_cards', 'reconciling', 'running'])
def test_each_active_phase_detects_missing_heartbeat_without_claiming_dead_process(status):
    result = assess_scan_health(record(status, 181), now=NOW)
    assert result['state'] == 'heartbeat_missing'
    assert not result['process_liveness_verified']


@pytest.mark.parametrize('timestamp', [None, 'bad', '2026-10-28T12:00:00', '2026-10-28T13:00:00Z'])
def test_invalid_or_future_timestamp_does_not_mask_stalled_scan(timestamp):
    result = assess_scan_health({'status': 'checking_cards', 'updated_at': timestamp}, now=NOW)
    assert result['state'] == 'heartbeat_missing'


def test_old_terminal_record_is_not_misreported_as_running_or_coverage_proof():
    result = assess_scan_health(record('completed', 86400), now=NOW)
    assert result['state'] == 'completed'
    assert result['action'] == 'review_report'
    assert not result['heartbeat_stale']
    assert not result['coverage_verified']


def test_partial_keeps_unfinished_checks_actionable():
    result = assess_scan_health(record('partial'), now=NOW)
    assert result['action'] == 'review_incomplete_checks'
    assert result['requires_attention']


def test_live_card_phase_is_running_not_idle():
    result = assess_scan_health(record('checking_cards'), now=NOW)
    assert result['state'] == 'running'
    assert result['action'] == 'none'
