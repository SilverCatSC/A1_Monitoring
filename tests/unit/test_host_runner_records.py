"""Launch attempts retain diagnostics even before a DB cycle exists.

GUI, services, database recovery and scanning are all replaced with local
fixtures. No Chrome, Docker or marketplace access occurs.
"""
import fcntl
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_CYCLE = '00000000-0000-0000-0000-000000000001'


def isolated_runner(tmp_path, *, stage='scan', exit_code=2):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    for name in ('doctor.py', 'recover_open_cycles.py'):
        (scripts / name).write_text('print("LOCAL_FIXTURE_READY")\n')
    services = scripts / 'services.py'
    services.write_text('print("LOCAL_FIXTURE_SERVICE_DETAIL")\n'
                        + ('raise SystemExit(9)\n' if stage == 'services' else ''))
    scanner = scripts / 'local_scan.sh'
    scanner.write_text('#!/bin/bash\nprintf "PRIVATE_SCAN_DETAIL\\n"\n'
                       + (f'printf "LOCAL_CYCLE_ID {FIXTURE_CYCLE}\\n"\n' if exit_code in {0, 2} else
                          'printf "LOCAL_CYCLE_FAILED ValidationError: fixture configuration rejected\\n" >&2\n')
                       + f'exit {exit_code}\n')
    scanner.chmod(0o700)
    source = (ROOT / 'scripts/run_monitoring_host_macos.sh').read_text()
    source = source.rsplit('\nmain "$@"', 1)[0]
    q = shlex.quote
    overrides = f'''
ROOT_DIR={q(str(tmp_path))}
ARTIFACTS_DIR="$ROOT_DIR/artifacts"
STATUS_PATH="$ARTIFACTS_DIR/monitoring_host_runner_macos_status.json"
select_python() {{ PYTHON={q(sys.executable)}; }}
select_compose() {{ COMPOSE=("$PYTHON" {q(str(services))}); }}
prepare_vpn_admission_directory() {{ return 0; }}
acquire_kernel_lock_and_reexec() {{ LOCK_HELD=1; }}
require_console_gui_user() {{ {'safe_message "HOST_RUNNER_REFUSED reason=fixture_gui_refusal"; exit 1;' if stage == 'gui' else 'return 0;'} }}
main "$@"
'''
    driver = scripts / 'fixture_runner.sh'
    driver.write_text(source + overrides)
    return driver


@pytest.mark.parametrize('exit_code,state', [(0, 'succeeded'), (2, 'partial'), (1, 'failed')])
def test_scanner_output_is_private_persistent_and_cycle_id_is_exposed(tmp_path, exit_code, state):
    driver = isolated_runner(tmp_path, exit_code=exit_code)
    result = subprocess.run(['bash', str(driver), '--diagnostic-routing', '--engines', 'avito'],
                            capture_output=True, text=True)
    assert result.returncode == exit_code
    assert 'PRIVATE_SCAN_DETAIL' not in result.stdout + result.stderr
    records = list((tmp_path / 'artifacts/host_runs').iterdir())
    assert len(records) == 1
    log = (records[0] / 'host_runner.log').read_text()
    assert 'PRIVATE_SCAN_DETAIL' in log
    assert (records[0].stat().st_mode & 0o777) == 0o700
    assert ((records[0] / 'host_runner.log').stat().st_mode & 0o777) == 0o600
    assert (records[0] / 'exit_code.txt').read_text().strip() == str(exit_code)
    status = json.loads((records[0] / 'status.json').read_text())
    assert status == json.loads((tmp_path / 'artifacts/monitoring_host_runner_macos_status.json').read_text())
    assert status['state'] == state
    if exit_code in {0, 2}:
        assert status['cycle_id'] == FIXTURE_CYCLE
        assert f'HOST_RUNNER_CYCLE_ID {FIXTURE_CYCLE}' in result.stderr
    else:
        assert 'fixture configuration rejected' in log
        assert 'cycle_id' not in status


def test_prelock_refusal_has_a_record_without_overwriting_prior_cycle(tmp_path):
    driver = isolated_runner(tmp_path, stage='gui')
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    old_status = artifacts / 'monitoring_host_runner_macos_status.json'
    old_status.write_text('{"cycle_id":"previous"}')
    result = subprocess.run(['bash', str(driver), '--diagnostic-routing'], capture_output=True, text=True)
    assert result.returncode == 1
    assert old_status.read_text() == '{"cycle_id":"previous"}'
    record = next((artifacts / 'host_runs').iterdir())
    assert 'fixture_gui_refusal' in (record / 'host_runner.log').read_text()
    assert (record / 'exit_code.txt').read_text().strip() == '1'


def test_service_failure_detail_is_saved_before_any_scan(tmp_path):
    driver = isolated_runner(tmp_path, stage='services')
    result = subprocess.run(['bash', str(driver), '--diagnostic-routing'], capture_output=True, text=True)
    assert result.returncode == 1
    record = next((tmp_path / 'artifacts/host_runs').iterdir())
    assert 'LOCAL_FIXTURE_SERVICE_DETAIL' in (record / 'host_runner.log').read_text()
    assert 'PRIVATE_SCAN_DETAIL' not in (record / 'host_runner.log').read_text()
    assert json.loads((record / 'status.json').read_text())['phase'] == 'services'


def test_busy_lock_cannot_look_like_a_successful_scan(tmp_path):
    log = tmp_path / 'host_runner.log'
    log.write_text('')
    log.chmod(0o600)
    lock = tmp_path / 'runner.lock'
    with lock.open('w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run([
            sys.executable, str(ROOT / 'scripts/with_monitoring_host_lock_macos.py'),
            '--lock-path', str(lock), '--attempt-log', str(log), '--active-exit-code', '75',
            '--', sys.executable, '-c', 'raise RuntimeError("must never run")',
        ], capture_output=True, text=True)
    assert result.returncode == 75
    assert 'no_cycle_created=true' in result.stderr
    assert 'another_runner_active' in log.read_text()
    assert (tmp_path / 'exit_code.txt').read_text().strip() == '75'


def test_targeted_runner_skips_recovery_and_passes_only_one_filter(tmp_path):
    driver = isolated_runner(tmp_path, exit_code=0)
    (tmp_path / 'scripts/recover_open_cycles.py').write_text('raise RuntimeError("recovery must not run")\n')
    capture = tmp_path / 'child_args.json'
    child = tmp_path / 'scripts/local_scan.sh'
    child.write_text('#!/bin/bash\nexec ' + shlex.quote(sys.executable) + ' '
                     + shlex.quote(str(tmp_path / 'scripts/search_fixture.py')) + ' "$@"\n')
    (tmp_path / 'scripts/search_fixture.py').write_text(
        'import json,sys\nfrom pathlib import Path\n'
        + f'Path({str(capture)!r}).write_text(json.dumps(sys.argv[1:]))\n'
        + 'Path(sys.argv[sys.argv.index("--search-result")+1]).write_text("{}")\n')
    result = subprocess.run(['bash', str(driver), '--diagnostic-routing', '--search-only',
                             'avito_voyah_dream_all', '--pages', '5'], capture_output=True, text=True)
    assert result.returncode == 0
    arguments = json.loads(capture.read_text())
    assert arguments[arguments.index('--search-only') + 1] == 'avito_voyah_dream_all'
    assert arguments[arguments.index('--engines') + 1] == 'avito'
    assert '--retry-cycle' not in arguments
    record = next((tmp_path / 'artifacts/host_runs').iterdir())
    status = json.loads((record / 'status.json').read_text())
    assert status['run_kind'] == 'targeted_search'
    assert not status['writes_monitoring_cycle']
    assert 'cycle_id' not in status
    assert 'HOST_RUNNER_SEARCH_OK no_cycle_created=true' in result.stderr


def test_targeted_success_requires_a_saved_result(tmp_path):
    driver = isolated_runner(tmp_path, exit_code=0)
    result = subprocess.run(['bash', str(driver), '--diagnostic-routing', '--search-only',
                             'avito_voyah_dream_all'], capture_output=True, text=True)
    assert result.returncode == 1
    record = next((tmp_path / 'artifacts/host_runs').iterdir())
    status = json.loads((record / 'status.json').read_text())
    assert status['state'] == 'failed'
    assert 'cycle_id' not in status
    assert 'targeted_result_missing' in result.stderr
