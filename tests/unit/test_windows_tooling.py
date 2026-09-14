import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.bitrix_publish import _message, _valid_url
from scripts.doctor import chrome_available
from scripts.export_public_report import DIRECT_EVIDENCE_RE, _route_for
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


def test_public_routes_are_static_and_relative_free():
    assert _route_for('/api/v1/dashboard') == 'index.html'
    assert _route_for('/api/v1/dashboard/placements?days=7') == 'placements/index.html'
    assert _route_for('/api/v1/dashboard/listings/listing-1') == 'listings/listing-1/index.html'


def test_public_export_recognizes_direct_card_evidence():
    match = DIRECT_EVIDENCE_RE.search('/api/v1/reconciliations/check-1/evidence')

    assert match is not None
    assert match.group(1) == 'check-1'


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
        assert 'dropdb --if-exists --force "$RESTORE_DB"' in script
        assert 'RESTORE_OK' in script
