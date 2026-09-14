import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import fcntl
except ModuleNotFoundError:  # Windows has no POSIX kernel-lock module.
    fcntl = None

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.bitrix_publish import _message, _valid_url
from scripts.doctor import chrome_available
from scripts.export_public_report import PUBLIC_REPORT_PATH
from scripts.local_scan import _chrome_executable


def test_doctor_uses_platform_aware_chrome_lookup(monkeypatch):
    monkeypatch.setattr('scripts.doctor._chrome_executable', lambda: 'C:/Chrome/chrome.exe')
    assert chrome_available()


def test_doctor_reports_missing_chrome(monkeypatch):
    def missing():
        raise RuntimeError('Not found')
    monkeypatch.setattr('scripts.doctor._chrome_executable', missing)
    assert not chrome_available()


def test_windows_chrome_user_install(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('LOCALAPPDATA', 'C:/Users/operator/AppData/Local')
    expected = 'C:/Users/operator/AppData/Local/Google/Chrome/Application/chrome.exe'
    monkeypatch.setattr(Path, 'is_file', lambda path: str(path) == expected)
    assert _chrome_executable() == expected


def test_public_export_has_one_aggregate_only_route():
    assert PUBLIC_REPORT_PATH == '/api/v1/public/report'


def test_bitrix_message_keeps_report_url_and_validates_https():
    message = _message('https://silvercatsc.github.io/A1_Monitoring/', 'Проверка готова')
    assert 'Проверка готова' in message
    assert message.endswith('https://silvercatsc.github.io/A1_Monitoring/')
    assert _valid_url('https://portal.example.bitrix24.ru/rest/hook', 'webhook-url').startswith('https://')


def test_windows_full_run_is_sequential_and_memory_guarded():
    root = Path(__file__).resolve().parents[2]
    script = (root / 'scripts' / 'run_full_monitoring_windows.ps1').read_text(encoding='utf-8')

    assert 'stop_monitoring_chrome_windows.ps1' in script
    assert "@('compose', 'stop', 'app', 'backup', 'db')" in script
    assert 'start_local_ai_windows.ps1' in script
    assert 'run_ai_review_windows.ps1' in script
    assert 'run_ouroboros_live_audit_windows.ps1' in script
    assert 'MinFreeMemoryMb = 7500' in script


def test_windows_ai_server_is_cpu_only_single_slot_and_multimodal():
    root = Path(__file__).resolve().parents[2]
    script = (root / 'scripts' / 'start_local_ai_windows.ps1').read_text(encoding='utf-8')

    assert "'--mmproj', $mmproj" in script
    assert "'-ngl', '0'" in script
    assert "'-np', '1'" in script
    assert 'free RAM' in script


def test_windows_ai_install_is_pinned_and_local():
    root = Path(__file__).resolve().parents[2]
    script = (root / 'scripts' / 'install_ai_tools_windows.ps1').read_text(encoding='utf-8')

    assert '-Commit $lock.HERMES_COMMIT' in script
    assert 'ouroboros-ai[mcp]==$($lock.OUROBOROS_VERSION)' in script
    assert 'install_llama_cpp_windows.ps1' in script


def test_backup_and_restore_scripts_require_integrity_and_keep_restore_isolated():
    root = Path(__file__).resolve().parents[2]
    backup_sh = (root / 'scripts' / 'backup_now.sh').read_text(encoding='utf-8')
    restore_sh = (root / 'scripts' / 'restore_test.sh').read_text(encoding='utf-8')
    backup_ps1 = (root / 'scripts' / 'backup_now.ps1').read_text(encoding='utf-8')
    restore_ps1 = (root / 'scripts' / 'restore_test.ps1').read_text(encoding='utf-8')

    assert 'sha256sum' in backup_sh
    assert 'BACKUP_OK' in backup_ps1
    for script in (restore_sh, restore_ps1):
        assert 'sha256sum -c' in script
        assert 'pg_restore --list' in script
        assert 'alembic_version' in script
        assert 'monitoring_cycles' in script
        assert 'manager_feedback' in script
        assert 'source_missing=true' in script
        assert 'to_regclass' in script
        assert 'dropdb --if-exists --force "$RESTORE_DB"' in script
        assert 'RESTORE_OK' in script


def test_deploy_requires_explicit_apply_and_checks_schema_state():
    root = Path(__file__).resolve().parents[2]
    deploy_sh = (root / 'scripts' / 'deploy.sh').read_text(encoding='utf-8')

    assert 'MODE="${1:---preflight}"' in deploy_sh
    assert 'DEPLOY_PREFLIGHT_BLOCKED migration_required=true apply_not_requested=true' in deploy_sh
    assert 'git status --short' in deploy_sh
    assert 'alembic heads' in deploy_sh
    assert '"${COMPOSE[@]}" up -d --build app db backup' in deploy_sh
    assert 'DEPLOY_OK health=' in deploy_sh


def test_app_compose_service_has_a_loopback_healthcheck():
    root = Path(__file__).resolve().parents[2]
    compose = (root / 'docker-compose.yml').read_text(encoding='utf-8')

    assert 'healthcheck:' in compose
    assert "http://127.0.0.1:8000/api/v1/health" in compose


def test_legacy_live_acceptance_fails_closed_for_the_macos_primary_host():
    root = Path(__file__).resolve().parents[2]
    script_path = root / 'scripts' / 'live_acceptance.sh'
    legacy = script_path.read_text(encoding='utf-8')

    assert script_path.stat().st_mode & stat.S_IXUSR
    assert 'LIVE_ACCEPTANCE_DEPRECATED reason=container_scan_not_valid_for_macos_primary_host' in legacy
    assert 'run_monitoring_host_macos.sh --preflight' in legacy
    assert 'docs/production/ACCEPTANCE_M7.md' in legacy
    assert 'source .env' not in legacy
    assert 'curl ' not in legacy
    assert 'POST /api/v1/scan' in legacy  # explanatory comment; it is never executed
    assert 'exit 2' in legacy


def test_windows_host_runner_requires_interactive_desktop_and_is_single_cycle():
    root = Path(__file__).resolve().parents[2]
    runner = (root / 'scripts' / 'run_monitoring_host_windows.ps1').read_text(encoding='utf-8')

    assert 'SessionId -eq 0' in runner
    assert "identity.User.Value -eq 'S-1-5-18'" in runner
    assert 'Get-Process -Name explorer' in runner
    assert '[System.Threading.Mutex]::new($false, (Get-A1MutexName $Root))' in runner
    assert '$mutex.WaitOne(0)' in runner
    assert '[System.Threading.AbandonedMutexException]' in runner
    assert 'HOST_RUNNER_SKIPPED_ACTIVE' in runner
    assert '[System.IO.File]::WriteAllText(' in runner
    assert '[System.IO.File]::Replace($temporaryPath, $script:StatusPath, $null)' in runner
    assert '[System.IO.File]::Move($temporaryPath, $script:StatusPath)' in runner
    assert "'-m', 'app.cli', 'recover-open-cycles'" in runner
    assert runner.index("'-m', 'app.cli', 'recover-open-cycles'") < runner.index(
        "'local_scan_windows.ps1'"
    )
    assert "$Pace = 'cautious'" in runner
    assert "'-Pace' $Pace" in runner
    assert 'HOST_RUNNER_REFUSED reason=windows_fallback_not_accepted' in runner
    assert 'HOST_RUNNER_PARTIAL: no automatic retry was started.' in runner


def test_windows_fallback_entrypoints_fail_closed_until_separate_acceptance():
    root = Path(__file__).resolve().parents[2]
    local_scan = (root / 'scripts' / 'local_scan_windows.ps1').read_text(encoding='utf-8')
    full_run = (root / 'scripts' / 'run_full_monitoring_windows.ps1').read_text(encoding='utf-8')

    assert 'LOCAL_SCAN_REFUSED reason=windows_fallback_not_accepted' in local_scan
    assert 'MONITORING_SYSTEM_REFUSED reason=windows_fallback_not_accepted' in full_run
    assert local_scan.index('windows_fallback_not_accepted') < local_scan.index(
        "Invoke-A1Native 'docker'"
    )
    assert full_run.index('windows_fallback_not_accepted') < full_run.index(
        "scripts/local_scan.py"
    )


def test_windows_task_registration_is_plan_only_and_never_autostarts_scan():
    root = Path(__file__).resolve().parents[2]
    registrar = (
        root / 'scripts' / 'register_monitoring_task_windows.ps1'
    ).read_text(encoding='utf-8')

    assert 'SessionId -eq 0' in registrar
    assert "identity.User.Value -eq 'S-1-5-18'" in registrar
    assert 'Get-Process -Name explorer' in registrar
    assert "if (-not $Apply)" in registrar
    assert 'TASK_REGISTRATION_PLAN_ONLY' in registrar
    assert registrar.index('if (-not $Apply)') < registrar.index(
        'Ensure-A1TaskFolder -Path $TaskPath'
    )
    assert "CreateFolder('A1Monitoring', $null)" in registrar
    assert registrar.index('Ensure-A1TaskFolder -Path $TaskPath') < registrar.index(
        'Register-ScheduledTask -TaskPath $TaskPath'
    )
    assert '(Get-Date).Date.AddDays(1)' in registrar
    assert 'New-ScheduledTaskTrigger -Daily -At $firstRun' in registrar
    assert '-LogonType Interactive -RunLevel Limited' in registrar
    assert '-MultipleInstances IgnoreNew -RestartCount 0' in registrar
    assert 'Start-ScheduledTask' not in registrar
    assert '--watch' not in registrar
    assert registrar.index('windows_fallback_not_accepted') < registrar.index(
        'Ensure-A1TaskFolder -Path $TaskPath'
    )


def test_macos_host_runner_requires_visible_console_and_uses_kernel_lock():
    root = Path(__file__).resolve().parents[2]
    runner = (root / 'scripts' / 'run_monitoring_host_macos.sh').read_text(encoding='utf-8')

    assert '[[ "$EUID" -eq 0 ]]' in runner
    assert 'root_not_allowed' in runner
    assert "stat -f '%Su' /dev/console" in runner
    assert 'gui/$CONSOLE_UID' in runner
    assert 'IOConsoleLocked' in runner
    assert "CONSOLE_LOCKED" in runner
    assert 'CGSSessionScreenIsLocked' in runner
    assert 'SESSION_SCREEN_LOCKED' in runner
    assert 'screen_locked_or_state_unavailable' in runner
    assert runner.count('require_console_gui_user') >= 3
    assert 'with_monitoring_host_lock_macos.py' in runner
    assert 'LOCK_CONTEXT_REQUESTED' in runner
    assert '--verify-inherited-fd' in runner
    assert 'HOST_RUNNER_SKIPPED_ACTIVE' in (
        root / 'scripts' / 'with_monitoring_host_lock_macos.py'
    ).read_text(encoding='utf-8')
    assert 'app.cli recover-open-cycles' in runner
    assert runner.index('app.cli recover-open-cycles') < runner.index(
        '$ROOT_DIR/scripts/local_scan.sh'
    )
    assert runner.index("RUNNER_PHASE='browser_preflight'") < runner.index(
        '$ROOT_DIR/scripts/local_scan.sh'
    )
    assert "PACE='cautious'" in runner
    assert 'HOST_RUNNER_PARTIAL no_automatic_retry=true' in runner
    assert '--watch' not in runner
    assert '/usr/bin/mktemp' in runner
    assert '/bin/mv -f' in runner


def test_macos_lock_helper_validates_inherited_descriptor_identity(tmp_path):
    if fcntl is None:
        pytest.skip('fcntl lock semantics are verified on POSIX hosts only')

    root = Path(__file__).resolve().parents[2]
    helper = root / 'scripts' / 'with_monitoring_host_lock_macos.py'
    lock_path = tmp_path / 'runner.lock'
    wrong_path = tmp_path / 'wrong.lock'
    lock_path.touch()
    wrong_path.touch()
    inherited_fd = os.open(lock_path, os.O_RDWR)
    wrong_fd = os.open(wrong_path, os.O_RDWR)
    try:
        fcntl.flock(inherited_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.set_inheritable(inherited_fd, True)
        os.set_inheritable(wrong_fd, True)
        valid = subprocess.run(
            [
                sys.executable, str(helper), '--lock-path', str(lock_path),
                '--verify-inherited-fd', str(inherited_fd),
            ],
            check=False,
            capture_output=True,
            text=True,
            pass_fds=(inherited_fd,),
        )
        forged = subprocess.run(
            [
                sys.executable, str(helper), '--lock-path', str(lock_path),
                '--verify-inherited-fd', str(wrong_fd),
            ],
            check=False,
            capture_output=True,
            text=True,
            pass_fds=(wrong_fd,),
        )
    finally:
        os.close(inherited_fd)
        os.close(wrong_fd)

    assert valid.returncode == 0
    assert forged.returncode == 1
    assert 'inherited_fd_mismatch' in forged.stderr


def test_macos_lock_helper_skips_an_active_runner_and_releases_after_exec(tmp_path):
    if fcntl is None:
        pytest.skip('fcntl lock semantics are verified on POSIX hosts only')

    root = Path(__file__).resolve().parents[2]
    helper = root / 'scripts' / 'with_monitoring_host_lock_macos.py'
    lock_path = tmp_path / 'runner.lock'

    with lock_path.open('w', encoding='utf-8') as held_lock:
        fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        skipped = subprocess.run(
            [
                sys.executable,
                str(helper),
                '--lock-path',
                str(lock_path),
                '--',
                sys.executable,
                '-c',
                'raise SystemExit(47)',
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert skipped.returncode == 0
        assert skipped.stdout == 'HOST_RUNNER_SKIPPED_ACTIVE\n'

    executed = subprocess.run(
        [
            sys.executable,
            str(helper),
            '--lock-path',
            str(lock_path),
            '--',
            sys.executable,
            '-c',
            'raise SystemExit(47)',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert executed.returncode == 47


def test_macos_launchagent_registration_is_plan_only_and_never_autostarts():
    root = Path(__file__).resolve().parents[2]
    registrar = (
        root / 'scripts' / 'register_monitoring_launchagent_macos.sh'
    ).read_text(encoding='utf-8')

    assert 'root_not_allowed' in registrar
    assert "stat -f '%Su' /dev/console" in registrar
    assert 'IOConsoleLocked' in registrar
    assert 'CGSSessionScreenIsLocked' in registrar
    assert 'SESSION_SCREEN_LOCKED' in registrar
    assert 'screen_locked_or_state_unavailable' in registrar
    assert 'if [[ "$APPLY" -eq 0 ]]' in registrar
    assert 'LAUNCHAGENT_REGISTRATION_PLAN_ONLY' in registrar
    assert registrar.index('if [[ "$APPLY" -eq 0 ]]') < registrar.index(
        '/bin/mkdir -p "$AGENTS_DIR" "$LOG_DIR"'
    )
    assert '<key>WorkingDirectory</key>' in registrar
    assert '<key>EnvironmentVariables</key>' in registrar
    assert '/opt/homebrew/bin:/usr/local/bin' in registrar
    assert '<key>RunAtLoad</key>' in registrar
    assert '<key>KeepAlive</key>' in registrar
    assert '/bin/launchctl bootstrap "gui/$CONSOLE_UID" "$PLIST_PATH"' in registrar
    assert 'launchctl kickstart' not in registrar
    assert 'launchctl bootout' not in registrar
    assert '--watch' not in registrar


def test_macos_finder_launchers_are_executable():
    if sys.platform == 'win32':
        pytest.skip('Git executable bits are not represented by Windows file modes')

    root = Path(__file__).resolve().parents[2]
    for name in ('Открыть дашборд.command', 'Запустить мониторинг.command'):
        assert (root / name).stat().st_mode & stat.S_IXUSR
