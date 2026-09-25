from pathlib import Path


def test_finder_monitoring_launcher_is_preflight_only() -> None:
    root = Path(__file__).resolve().parents[2]
    launcher = (root / 'Запустить мониторинг.command').read_text(encoding='utf-8')

    assert './scripts/run_monitoring_host_macos.sh --preflight' in launcher
    assert 'FINDER_PREFLIGHT_OK cycle_not_started=true' in launcher
    assert 'M7_CONTROLLED_CYCLE' in launcher
    for forbidden in (
        'local_scan',
        'run_full_monitoring_macos.sh',
        'run_ai_review_macos.sh',
        'run_ouroboros',
        'caffeinate',
    ):
        assert forbidden not in launcher


def test_mac_host_runner_exposes_bounded_captcha_wait() -> None:
    root = Path(__file__).resolve().parents[2]
    runner = (root / 'scripts' / 'run_monitoring_host_macos.sh').read_text(encoding='utf-8')
    assert '--captcha-wait-seconds' in runner
    assert 'invalid_captcha_wait_seconds' in runner
    assert 'CAPTCHA_WAIT_SECONDS=180' in runner
