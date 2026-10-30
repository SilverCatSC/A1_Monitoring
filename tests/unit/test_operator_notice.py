from datetime import UTC, datetime

from app.service.operator_notice import OperatorNoticeTracker

START = datetime(2026, 10, 28, 12, tzinfo=UTC)


def progress(status='waiting_captcha', **current):
    return {'cycle_id': 'new', 'status': status, 'updated_at': START.isoformat(), 'current': current}


def test_repeated_wait_loop_notifies_once_then_new_episode_notifies_again():
    tracker = OperatorNoticeTracker(START)
    assert tracker.update(progress())
    assert tracker.update(progress(wait_reason='stabilizing')) is None
    assert tracker.update(progress('running')) is None
    assert tracker.update(progress())


def test_previous_runs_are_not_announced_on_start():
    tracker = OperatorNoticeTracker(START)
    old = progress('partial')
    old['updated_at'] = '2026-10-27T12:00:00Z'
    assert tracker.update(old) is None
    assert tracker.update({'status': 'waiting_captcha'}) is None


def test_separate_blocked_page_and_source_receive_their_own_notice_without_private_data():
    tracker = OperatorNoticeTracker(START)
    assert tracker.update(progress(source='avito', url='https://example.test/private', page=1))
    notice = tracker.update(progress(source='avito', url='https://example.test/other', page=2))
    assert notice and 'example.test' not in str(notice)
    assert tracker.update(progress(source='auto_ru', page=2))


def test_partial_notification_does_not_claim_success_or_inactivity():
    tracker = OperatorNoticeTracker(START)
    title, text = tracker.update(progress('partial'))
    assert 'gaps' in title
    assert 'unverified' in text
    assert tracker.update(progress('partial')) is None


def test_native_observer_passes_fixed_text_as_arguments_and_releases_inherited_lock(monkeypatch):
    from types import SimpleNamespace

    from scripts import notify_monitoring_macos as observer

    closed, calls = [], []
    monkeypatch.setattr(observer.sys, 'platform', 'darwin')
    monkeypatch.setattr(observer.sys, 'argv', [
        'notice', '--evidence-dir', '/unused', '--started-at', START.isoformat(),
        '--parent-pid', '123', '--once',
    ])
    monkeypatch.setenv('A1_MONITORING_HOST_LOCK_FD', '9')
    monkeypatch.setattr(observer.os, 'close', closed.append)
    monkeypatch.setattr(observer, 'read_scan_progress', lambda _: progress('partial', url='private-data'))
    monkeypatch.setattr(observer.subprocess, 'run',
                        lambda args, **kw: calls.append((args, kw)) or SimpleNamespace(returncode=0))
    observer.main()
    assert closed == [9]
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv[0] == '/usr/bin/osascript'
    assert 'private-data' not in str(argv)
    assert 'item 2 of argv' in argv[2]
    assert options['timeout'] == 5


def test_observer_does_not_read_progress_after_parent_has_exited(monkeypatch):
    from scripts import notify_monitoring_macos as observer

    monkeypatch.delenv('A1_MONITORING_HOST_LOCK_FD', raising=False)
    monkeypatch.setattr(observer.sys, 'platform', 'darwin')
    monkeypatch.setattr(observer.sys, 'argv', [
        'notice', '--evidence-dir', '/unused', '--started-at', START.isoformat(), '--parent-pid', '123',
    ])
    monkeypatch.setattr(observer.os, 'getppid', lambda: 1)
    monkeypatch.setattr(observer, 'read_scan_progress', lambda _: (_ for _ in ()).throw(AssertionError('orphan read')))
    observer.main()
