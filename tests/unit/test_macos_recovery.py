import os
from pathlib import Path

from scripts.recover_open_cycles import configure_host_database


def test_recovery_uses_the_private_host_loopback_dsn(monkeypatch):
    configure_host_database({'DB_PASSWORD': 'safe password', 'DB_BIND_PORT': '15433'})
    assert os.environ['DATABASE_DSN'] == (
        'postgresql+psycopg2://monitor:safe+password@127.0.0.1:15433/a1_search_monitor'
    )


def test_macos_runner_uses_host_aware_recovery_before_browser_scan():
    root = Path(__file__).resolve().parents[2]
    runner = (root / 'scripts' / 'run_monitoring_host_macos.sh').read_text(encoding='utf-8')

    assert 'recover_open_cycles.py' in runner
    assert 'A1_MONITORING_HOST_RUNNER_CONTEXT=1' in runner
    assert runner.index('recover_open_cycles.py') < runner.index('$ROOT_DIR/scripts/local_scan.sh')
