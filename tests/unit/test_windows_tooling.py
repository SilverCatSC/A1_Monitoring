import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.bitrix_publish import _message, _valid_url
from scripts.doctor import chrome_available, compose_available
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


def test_doctor_finds_user_docker_desktop_install(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr('scripts.doctor.shutil.which', lambda _name: None)
    monkeypatch.setattr(Path, 'home', lambda: Path('C:/Users/operator'))
    expected = 'C:/Users/operator/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
    monkeypatch.setattr(Path, 'is_file', lambda path: path.as_posix() == expected)

    assert compose_available()


def test_windows_chrome_user_install(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('LOCALAPPDATA', 'C:/Users/operator/AppData/Local')
    expected = Path('C:/Users/operator/AppData/Local/Google/Chrome/Application/chrome.exe')
    monkeypatch.setattr(Path, 'is_file', lambda path: path.as_posix() == expected.as_posix())
    assert _chrome_executable() == str(expected)


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
    assert "start_local_ai_windows.ps1') -Profile heavy" in script
    assert "AiProfile = 'light'" in script
    assert '-Profile $AiProfile' in script
    assert 'MinFreeMemoryMb = 7500' in script
    assert '-AllowSwap:$AllowSwap' in script


def test_windows_ai_review_selects_requested_model_profile():
    root = Path(__file__).resolve().parents[2]
    wrapper = (root / 'scripts' / 'run_ai_review_windows.ps1').read_text(encoding='utf-8')
    helper = (root / 'scripts' / 'hermes_monitoring_oneshot_windows.py').read_text(encoding='utf-8')

    assert "$env:A1_AI_PROFILE = $Profile" in wrapper
    assert "os.environ.get('A1_AI_PROFILE', 'light')" in helper
    assert '/v1/chat/completions' in helper


def test_windows_ouroboros_uses_executable_hermes_with_heavy_profile():
    root = Path(__file__).resolve().parents[2]
    template = (root / 'config' / 'ouroboros-windows.yaml.template').read_text(encoding='utf-8')
    wrapper = (root / 'scripts' / 'run_ouroboros_live_audit_windows.ps1').read_text(encoding='utf-8')

    assert 'hermes_cli_path: "hermes"' in template
    assert "$env:HERMES_HOME = Join-Path $Root 'artifacts\\hermes_ouroboros_llm_home'" in wrapper
    assert "artifacts\\hermes_agent\\venv\\Scripts" in wrapper
    assert 'run_heavy_staged_audit.py' in wrapper
    assert 'OUROBOROS_WINDOWS_FALLBACK_READY' in wrapper


def test_monitoring_chrome_shutdown_tolerates_process_exit_race():
    root = Path(__file__).resolve().parents[2]
    script = (root / 'scripts' / 'stop_monitoring_chrome_windows.ps1').read_text(encoding='utf-8')

    assert 'Stop-Process' in script
    assert '-ErrorAction SilentlyContinue' in script


def test_windows_ai_server_is_cpu_only_single_slot_and_multimodal():
    root = Path(__file__).resolve().parents[2]
    script = (root / 'scripts' / 'start_local_ai_windows.ps1').read_text(encoding='utf-8')

    assert "'--mmproj', $mmproj" in script
    assert "Profile = 'light'" in script
    assert "AI_HEAVY_MODEL_FILE" in script
    assert "$reasoningFormat = 'off'" in script
    assert "$reasoningFormat = 'auto'" in script
    assert 'LOCAL_AI_DIFFERENT_MODEL_WINDOWS' in script
    assert "'-ngl', '0'" in script
    assert "'-np', '1'" in script
    assert 'free RAM' in script
    assert 'AI start continuing with swap' in script


def test_windows_ai_install_is_pinned_and_local():
    root = Path(__file__).resolve().parents[2]
    script = (root / 'scripts' / 'install_ai_tools_windows.ps1').read_text(encoding='utf-8')

    assert '-Commit $lock.HERMES_COMMIT' in script
    assert "$HermesHome = Join-Path $Root 'artifacts\\hermes_home'" in script
    assert "Join-Path $HermesHome 'bin\\uv.exe'" in script
    assert 'ouroboros-ai[mcp]==$($lock.OUROBOROS_VERSION)' in script
    assert 'install_llama_cpp_windows.ps1' in script


def test_windows_model_download_supports_light_and_heavy_profiles():
    root = Path(__file__).resolve().parents[2]
    script = (root / 'scripts' / 'download_local_model_windows.ps1').read_text(encoding='utf-8')

    assert "Profile = 'light'" in script
    assert "AI_HEAVY_MODEL_REPO" in script
    assert "LOCAL_MULTIMODAL_MODEL_READY_WINDOWS profile=$Profile" in script
