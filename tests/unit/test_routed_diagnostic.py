import os
import subprocess
from pathlib import Path

import pytest

from app.config import BUSINESS_TRUSTED_NETWORK_PROFILES, Settings, settings
from app.service import cycle
from app.service.monitor import ScanConfigurationError
from scripts import local_scan


def test_routing_diagnostic_has_distinct_untrusted_profile(tmp_path, monkeypatch):
    for name in ('NETWORK_PROFILE', 'BROWSER_CDP_URL', 'PLAYWRIGHT_HEADLESS',
                 'SCAN_ENABLED_ENGINES', 'PLACEMENT_RECONCILIATION_ENABLED',
                 'SCAN_PAGES_LIMIT', 'EVIDENCE_DIR', 'CAPTCHA_OPERATOR_WAIT_SECONDS',
                 'VPN_OPERATIONAL_POLICY_PATH', 'DATABASE_DSN', *local_scan.PACING_PROFILES['fast']):
        monkeypatch.delenv(name, raising=False)
    args = local_scan._parser().parse_args([
        '--diagnostic-routing', '--evidence-dir', str(tmp_path), '--pace', 'fast',
        '--captcha-wait-seconds', '-1',
    ])
    local_scan._configure_runtime(args, {'DB_PASSWORD': 'test'})
    assert os.environ['NETWORK_PROFILE'] == 'local_routed_diagnostic'
    assert os.environ['SCAN_PAGES_LIMIT'] == '5'
    assert os.environ['CAPTCHA_OPERATOR_WAIT_SECONDS'] == '-1'
    assert Settings(_env_file=None).network_profile == 'local_routed_diagnostic'
    assert 'local_routed_diagnostic' not in BUSINESS_TRUSTED_NETWORK_PROFILES


def test_routed_admission_preserves_host_guard_without_vpn_state_requirement(monkeypatch):
    monkeypatch.setattr(settings, 'network_profile', 'local_routed_diagnostic')
    monkeypatch.setattr(settings, 'app_env', 'stage')
    monkeypatch.setattr(settings, 'local_browser_host_admission', True)
    calls = []
    monkeypatch.setattr(cycle, 'require_verified_macos_host_runner_context', lambda: calls.append('host'))
    monkeypatch.setattr(cycle, 'require_no_vpn_diagnostic_admission', lambda: pytest.fail('VPN-state gate'))
    monkeypatch.setattr(cycle, 'require_operational_vpn_admission', lambda _: pytest.fail('VPSUS gate'))
    cycle.require_macos_primary_monitoring_profile()
    cycle.require_local_browser_vpn_admission()
    assert calls == ['host']
    monkeypatch.setattr(settings, 'local_browser_host_admission', False)
    with pytest.raises(ScanConfigurationError):
        cycle.require_local_browser_vpn_admission()
    monkeypatch.setattr(settings, 'local_browser_host_admission', True)
    monkeypatch.setattr(settings, 'app_env', 'production')
    with pytest.raises(ScanConfigurationError):
        cycle.require_macos_primary_monitoring_profile()


@pytest.mark.parametrize('args,reason', [
    (['--diagnostic-routing', '--diagnostic-no-vpn'], 'conflicting_diagnostic_modes'),
    (['--diagnostic-routing', '--preflight'], 'preflight_does_not_accept_scan_parameters'),
    (['--diagnostic-routing', '--retry-cycle', '11111111-1111-1111-1111-111111111111'], 'diagnostic_retry_not_supported'),
])
def test_shell_rejects_conflicting_modes_before_services(args, reason):
    result = subprocess.run(['bash', str(Path('scripts/run_monitoring_host_macos.sh')), *args],
                            capture_output=True, text=True)
    assert result.returncode == 64
    assert reason in result.stdout + result.stderr
