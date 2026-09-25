import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import discover_dealer_listings, retry_monitoring_cycle, trigger_cycle, trigger_scan
from app.config import settings
from app.models import Base, DealerDiscoveryRun, ListingObservation, MonitoringCycle, ScanRun
from app.security import AuthenticatedActor
from app.service.cycle import MonitoringCycleService, ScanConfigurationError
from app.service.vpn_admission import (
    SCUTIL_PATH,
    VPNAdmissionError,
    _macos_extended_acl_present,
    prepare_attestation_directory,
    require_operational_vpn_admission,
    require_vpn_admission,
)

PROJECT_ROOT = Path(__file__).parents[2]
LOCAL_SCAN_PATH = PROJECT_ROOT / 'scripts' / 'local_scan.py'


def _attestation(now: datetime, **changes) -> dict:
    payload = {
        'schema_version': 1,
        'status': 'approved',
        'issued_at_utc': (now - timedelta(minutes=1)).isoformat().replace('+00:00', 'Z'),
        'expires_at_utc': (now + timedelta(hours=1)).isoformat().replace('+00:00', 'Z'),
        'services': {
            'chatgpt': {'route': 'vpn_exit', 'ipv4': 'verified', 'ipv6': 'verified'},
            'auto_ru': {
                'route': 'direct_physical_connection',
                'ipv4': 'verified',
                'ipv6': 'verified',
            },
            'avito': {
                'route': 'direct_physical_connection',
                'ipv4': 'verified',
                'ipv6': 'verified',
            },
        },
    }
    payload.update(changes)
    return payload


def _operational_policy(**changes) -> dict:
    payload = {
        'schema_version': 2,
        'status': 'approved',
        'services': {
            'chatgpt': {'route': 'vpn_exit'},
            'auto_ru': {'route': 'direct_physical_connection'},
            'avito': {'route': 'direct_physical_connection'},
        },
    }
    payload.update(changes)
    return payload


def _connected_scutil(command, **_kwargs):
    if command == [SCUTIL_PATH, '--nc', 'list']:
        return SimpleNamespace(
            returncode=0,
            stdout='* (Connected) VPN (com.vpsus.vpsus) "VPSUS" [VPN:com.vpsus.vpsus]\n',
        )
    if command == [SCUTIL_PATH, '--nc', 'status', 'VPSUS']:
        return SimpleNamespace(returncode=0, stdout='Connected\nExtended Status ...\n')
    raise AssertionError(f'unexpected command: {command}')


def _write_attestation(path: Path, payload: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if os.name == 'posix':
        os.chmod(path.parent, 0o700)
    path.write_text(json.dumps(payload), encoding='utf-8')
    os.chmod(path, mode)


def _operator_request():
    return SimpleNamespace(
        state=SimpleNamespace(actor=AuthenticatedActor(username='operator', role='operator'))
    )


def _local_browser_settings(monkeypatch, attestation_path: Path) -> None:
    monkeypatch.setattr(settings, 'network_profile', 'local_browser')
    monkeypatch.setattr(settings, 'browser_cdp_url', 'http://127.0.0.1:19222')
    monkeypatch.setattr(settings, 'scan_enabled_engines', 'auto_ru')
    monkeypatch.setattr(settings, 'local_browser_host_admission', True)
    monkeypatch.setattr(settings, 'vpn_admission_path', str(attestation_path))
    monkeypatch.setattr(settings, 'vpn_operational_policy_path', str(attestation_path))


def test_valid_private_attestation_is_accepted(tmp_path):
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    path = tmp_path / 'attestation.json'
    _write_attestation(path, _attestation(now))

    admitted = require_vpn_admission(path, now=now)

    assert admitted.expires_at_utc == now + timedelta(hours=1)


def test_operational_policy_accepts_connected_vpsus_without_claiming_egress(tmp_path):
    path = tmp_path / 'policy.json'
    _write_attestation(path, _operational_policy())
    expired = tmp_path / 'attestation.json'
    old_time = datetime(2026, 9, 14, 12, tzinfo=UTC)
    _write_attestation(expired, _attestation(old_time))

    admitted = require_operational_vpn_admission(
        path, command_runner=_connected_scutil, platform='darwin',
    )

    assert admitted.service == 'VPSUS'
    assert admitted.routes['chatgpt'] == 'vpn_exit'
    with pytest.raises(VPNAdmissionError, match='expired'):
        require_vpn_admission(expired, now=old_time + timedelta(days=2))


@pytest.mark.parametrize(('payload', 'code'), [
    (_operational_policy(status='pending'), 'operational_policy_unapproved'),
    (_operational_policy(services={'chatgpt': {'route': 'vpn_exit'}}),
     'operational_policy_services_invalid'),
    (_operational_policy(services={
        'chatgpt': {'route': 'direct_physical_connection'},
        'auto_ru': {'route': 'direct_physical_connection'},
        'avito': {'route': 'direct_physical_connection'},
    }), 'operational_policy_routes_invalid'),
])
def test_operational_policy_fails_closed_for_unapproved_or_changed_routes(
    tmp_path, payload, code,
):
    path = tmp_path / 'policy.json'
    _write_attestation(path, payload)
    with pytest.raises(VPNAdmissionError, match=code):
        require_operational_vpn_admission(path, command_runner=_connected_scutil, platform='darwin')


def test_operational_policy_requires_private_file_and_macos(tmp_path):
    path = tmp_path / 'policy.json'
    _write_attestation(path, _operational_policy(), mode=0o644)
    with pytest.raises(VPNAdmissionError, match='insecure_permissions'):
        require_operational_vpn_admission(path, command_runner=_connected_scutil, platform='darwin')
    with pytest.raises(VPNAdmissionError, match='macos_required'):
        require_operational_vpn_admission(path, command_runner=_connected_scutil, platform='linux')


@pytest.mark.parametrize('list_status,connection_status', [
    ('Disconnected', 'Disconnected'),
    ('Connected', 'Disconnected'),
    ('Disconnected', 'Connected'),
])
def test_operational_policy_rejects_disconnected_vpsus(
    tmp_path, list_status, connection_status,
):
    path = tmp_path / 'policy.json'
    _write_attestation(path, _operational_policy())

    def runner(command, **_kwargs):
        if command[-1] == 'list':
            return SimpleNamespace(
                returncode=0,
                stdout=(f'* ({list_status}) VPN (com.vpsus.vpsus) "VPSUS" '
                        '[VPN:com.vpsus.vpsus]\n'),
            )
        return SimpleNamespace(returncode=0, stdout=f'{connection_status}\n')

    with pytest.raises(VPNAdmissionError, match='vpsus_not_connected'):
        require_operational_vpn_admission(path, command_runner=runner, platform='darwin')


def test_operational_policy_rejects_unavailable_or_other_vpn(tmp_path):
    path = tmp_path / 'policy.json'
    _write_attestation(path, _operational_policy())

    def unavailable(_command, **_kwargs):
        raise OSError('scutil unavailable')

    with pytest.raises(VPNAdmissionError, match='vpn_status_unavailable'):
        require_operational_vpn_admission(path, command_runner=unavailable, platform='darwin')

    def other_vpn(command, **_kwargs):
        if command[-1] == 'list':
            return SimpleNamespace(
                returncode=0,
                stdout='* (Connected) VPN (com.other.vpn) "VPSUS" [VPN:com.other.vpn]\n',
            )
        return SimpleNamespace(returncode=0, stdout='Connected\n')

    with pytest.raises(VPNAdmissionError, match='vpsus_not_connected'):
        require_operational_vpn_admission(path, command_runner=other_vpn, platform='darwin')


@pytest.mark.parametrize(
    ('payload_factory', 'code'),
    [
        (lambda now: {'not': 'an attestation'}, 'unexpected_schema'),
        (
            lambda now: _attestation(
                now,
                expires_at_utc=(now - timedelta(seconds=1)).isoformat().replace('+00:00', 'Z'),
            ),
            'expired',
        ),
        (
            lambda now: {
                **_attestation(now),
                'services': {
                    **_attestation(now)['services'],
                    'avito': {
                        'route': 'direct_physical_connection',
                        'ipv4': 'verified',
                        'ipv6': 'unverified',
                    },
                },
            },
            'ip_family_not_verified',
        ),
    ],
)
def test_attestation_fails_closed_for_invalid_or_incomplete_content(tmp_path, payload_factory, code):
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    path = tmp_path / 'attestation.json'
    _write_attestation(path, payload_factory(now))

    with pytest.raises(VPNAdmissionError, match=code):
        require_vpn_admission(path, now=now)


def test_attestation_rejects_missing_or_group_readable_file(tmp_path):
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    missing = tmp_path / 'missing.json'
    with pytest.raises(VPNAdmissionError, match='missing'):
        require_vpn_admission(missing, now=now)

    insecure = tmp_path / 'insecure.json'
    _write_attestation(insecure, _attestation(now), mode=0o644)
    if os.name == 'posix':
        with pytest.raises(VPNAdmissionError, match='insecure_permissions'):
            require_vpn_admission(insecure, now=now)


def test_attestation_rejects_an_insecure_parent_directory(tmp_path):
    if os.name != 'posix':
        pytest.skip('owner-only POSIX directory contract')
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    path = tmp_path / 'insecure-parent' / 'attestation.json'
    _write_attestation(path, _attestation(now))
    os.chmod(path.parent, 0o755)

    with pytest.raises(VPNAdmissionError, match='insecure_directory_permissions'):
        require_vpn_admission(path, now=now)


def test_preparation_creates_an_owner_private_empty_directory(tmp_path):
    directory = tmp_path / 'vpn_admission'

    prepare_attestation_directory(directory)

    assert directory.is_dir()
    if os.name == 'posix':
        assert (directory.stat().st_mode & 0o777) == 0o700


def test_macos_acl_marker_parser_fails_closed_for_unknown_or_extended_acl_output():
    assert not _macos_extended_acl_present('-rw-------  1 owner  staff  0 Sep 14 12:00 record')
    assert _macos_extended_acl_present('-rw-------+ 1 owner  staff  0 Sep 14 12:00 record')
    assert _macos_extended_acl_present('')


def test_cycle_rejects_missing_admission_before_ledger_or_browser(monkeypatch, tmp_path):
    import app.service.cycle as cycle_module

    _local_browser_settings(monkeypatch, tmp_path / 'missing.json')
    events = []

    class Ledger:
        def __init__(self, _db):
            events.append('ledger')

    monkeypatch.setattr(cycle_module, 'CycleLedgerService', Ledger)
    monkeypatch.setattr(cycle_module, 'require_verified_macos_host_runner_context', lambda: None)

    with pytest.raises(ScanConfigurationError, match='VPN operational policy'):
        MonitoringCycleService('db', before_browser=lambda: events.append('browser')).run()

    assert events == []


def test_cycle_rejects_configuration_only_host_claim_before_ledger_or_browser(
    monkeypatch, tmp_path
):
    import app.service.cycle as cycle_module

    _local_browser_settings(monkeypatch, tmp_path / 'attestation.json')
    events = []
    monkeypatch.setattr(cycle_module, 'require_operational_vpn_admission', lambda _path: None)
    monkeypatch.setattr(
        cycle_module,
        'CycleLedgerService',
        lambda _db: events.append('ledger') or pytest.fail('ledger must not start'),
    )

    with pytest.raises(ScanConfigurationError, match='approved interactive host runner'):
        MonitoringCycleService('db', before_browser=lambda: events.append('browser')).run()

    assert events == []


@pytest.mark.parametrize('profile', ('cloud_no_vpn', 'local_no_vpn', 'local_vpn'))
def test_cycle_rejects_legacy_runtime_profiles_before_ledger_or_browser(monkeypatch, profile):
    import app.service.cycle as cycle_module

    events = []
    monkeypatch.setattr(settings, 'network_profile', profile)
    monkeypatch.setattr(settings, 'browser_cdp_url', 'http://127.0.0.1:19222')
    monkeypatch.setattr(
        cycle_module,
        'CycleLedgerService',
        lambda _db: events.append('ledger') or pytest.fail('ledger must not start'),
    )

    with pytest.raises(ScanConfigurationError, match='MacBook primary monitoring'):
        MonitoringCycleService('db', before_browser=lambda: events.append('browser')).run()

    assert events == []


@pytest.mark.parametrize('endpoint', ('scan', 'cycle', 'retry', 'dealer'))
def test_api_full_cycle_paths_reject_missing_admission_before_new_cycle_writes(
    monkeypatch, tmp_path, endpoint
):
    missing = tmp_path / 'missing.json'
    _local_browser_settings(monkeypatch, missing)
    monkeypatch.setattr(
        'app.service.cycle.require_verified_macos_host_runner_context',
        lambda: None,
    )
    engine = create_engine(f'sqlite:///{tmp_path / f"{endpoint}.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        if endpoint == 'retry':
            parent = MonitoringCycle(
                id='retry-parent',
                status='partial',
                network_profile='local_browser',
                app_version='test',
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
            session.add(parent)
            session.commit()

        with pytest.raises(HTTPException) as error:
            if endpoint == 'retry':
                retry_monitoring_cycle('retry-parent', _operator_request(), session)
            elif endpoint == 'cycle':
                trigger_cycle(_operator_request(), session)
            elif endpoint == 'dealer':
                discover_dealer_listings(_operator_request(), session)
            else:
                trigger_scan(_operator_request(), session)

        assert error.value.status_code == 422
        assert 'VPN operational policy' in error.value.detail
        expected_cycles = 1 if endpoint == 'retry' else 0
        assert session.query(MonitoringCycle).count() == expected_cycles
        assert session.query(ScanRun).count() == 0
        assert session.query(DealerDiscoveryRun).count() == 0
        assert session.query(ListingObservation).count() == 0
    finally:
        session.close()
        engine.dispose()


def test_api_cannot_use_a_valid_file_without_host_runner_admission(monkeypatch, tmp_path):
    now = datetime.now(UTC)
    path = tmp_path / 'attestation.json'
    _write_attestation(path, _attestation(now))
    _local_browser_settings(monkeypatch, path)
    monkeypatch.setattr(settings, 'local_browser_host_admission', False)
    engine = create_engine(f'sqlite:///{tmp_path / "container-bypass.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        with pytest.raises(HTTPException) as error:
            trigger_scan(_operator_request(), session)

        assert error.value.status_code == 422
        assert 'approved interactive host runner' in error.value.detail
        assert session.query(MonitoringCycle).count() == 0
        assert session.query(ScanRun).count() == 0
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize('profile', ('cloud_no_vpn', 'local_no_vpn', 'local_vpn'))
def test_dealer_api_rejects_legacy_runtime_profiles_before_browser_work(monkeypatch, tmp_path, profile):
    monkeypatch.setattr(settings, 'network_profile', profile)
    monkeypatch.setattr(
        'app.api.DealerDiscoveryService',
        lambda *_args: pytest.fail('dealer browser work must not start'),
    )
    engine = create_engine(f'sqlite:///{tmp_path / f"{profile}.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        with pytest.raises(HTTPException) as error:
            discover_dealer_listings(_operator_request(), session)

        assert error.value.status_code == 422
        assert 'MacBook primary monitoring' in error.value.detail
    finally:
        session.close()
        engine.dispose()


def test_macos_runner_checks_shared_admission_after_preflight_before_recovery():
    runner = (PROJECT_ROOT / 'scripts' / 'run_monitoring_host_macos.sh').read_text(
        encoding='utf-8'
    )

    assert 'VPN_ADMISSION_REFUSED code=' in (
        PROJECT_ROOT / 'src' / 'app' / 'service' / 'vpn_admission.py'
    ).read_text(encoding='utf-8')
    assert "if [[ \"$PREFLIGHT_ONLY\" -eq 0 ]]; then" in runner
    assert '--operational-policy "$VPN_POLICY_PATH"' in runner
    assert runner.index("RUNNER_PHASE='vpn_operational_admission'") < runner.index(
        "RUNNER_PHASE='recover_open_cycles'"
    )
    assert runner.index("RUNNER_PHASE='preflight_finished'") < runner.index(
        "RUNNER_PHASE='vpn_operational_admission'"
    )
    assert 'A1_MONITORING_HOST_RUNNER_CONTEXT=1' in runner
    full_runner = (PROJECT_ROOT / 'scripts' / 'run_full_monitoring_macos.sh').read_text(
        encoding='utf-8'
    )
    assert 'MONITORING_SYSTEM_REFUSED reason=extended_pipeline_not_accepted' in full_runner
    local_launcher = (PROJECT_ROOT / 'scripts' / 'local_scan.sh').read_text(encoding='utf-8')
    assert '--probe-url|--probe-url=*' in local_launcher
    assert '[ "$probe_only" -eq 0 ]' in local_launcher


def _load_local_scan_module():
    spec = importlib.util.spec_from_file_location('vpn_gate_local_scan_script', LOCAL_SCAN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_scan_refuses_full_cycle_before_chrome_or_db(monkeypatch, tmp_path):
    local_scan = _load_local_scan_module()
    calls = []
    monkeypatch.setattr(local_scan, 'DEFAULT_VPN_POLICY', tmp_path / 'missing.json')
    monkeypatch.setattr(local_scan, '_env_file_values', lambda _path: {'DB_PASSWORD': 'safe-pass'})
    monkeypatch.setattr(local_scan, '_configure_runtime', lambda *_args: 'http://127.0.0.1:19222')
    monkeypatch.setenv('A1_MONITORING_HOST_RUNNER_CONTEXT', '1')
    monkeypatch.setattr(local_scan, '_require_interactive_host_runner_context', lambda: None)
    monkeypatch.setattr(
        local_scan,
        '_ensure_local_chrome',
        lambda *_args: calls.append('chrome') or pytest.fail('Chrome must not start'),
    )
    monkeypatch.setattr(
        'app.db.get_db_context',
        lambda: calls.append('db') or pytest.fail('DB context must not open'),
    )
    monkeypatch.setattr('sys.argv', ['local_scan.py', '--evidence-dir', str(tmp_path / 'evidence')])

    with pytest.raises(VPNAdmissionError, match='missing'):
        local_scan.main()

    assert calls == []


def test_macos_full_cycle_requires_host_runner_context_and_windows_fails_closed(monkeypatch):
    local_scan = _load_local_scan_module()
    monkeypatch.setattr(local_scan.sys, 'platform', 'darwin')
    monkeypatch.delenv(local_scan.HOST_RUNNER_CONTEXT_ENV, raising=False)

    with pytest.raises(RuntimeError, match='run_monitoring_host_macos.sh'):
        local_scan._require_interactive_host_runner_context()

    monkeypatch.setenv(local_scan.HOST_RUNNER_CONTEXT_ENV, '1')
    monkeypatch.setattr(local_scan, '_require_verified_host_lock_context', lambda: None)
    local_scan._require_interactive_host_runner_context()
    assert local_scan.os.environ['LOCAL_BROWSER_HOST_ADMISSION'] == 'true'

    monkeypatch.setattr(local_scan.sys, 'platform', 'win32')
    monkeypatch.delenv(local_scan.HOST_RUNNER_CONTEXT_ENV, raising=False)
    with pytest.raises(RuntimeError, match='Windows full monitoring is not accepted'):
        local_scan._require_interactive_host_runner_context()


def test_macos_full_cycle_requires_a_verified_inherited_lock(monkeypatch):
    local_scan = _load_local_scan_module()
    monkeypatch.setattr(local_scan.sys, 'platform', 'darwin')
    monkeypatch.setenv(local_scan.HOST_RUNNER_CONTEXT_ENV, '1')
    monkeypatch.delenv('A1_MONITORING_HOST_LOCK_HELD', raising=False)
    monkeypatch.delenv('A1_MONITORING_HOST_LOCK_FD', raising=False)

    with pytest.raises(RuntimeError, match='verified MacBook host lock'):
        local_scan._require_interactive_host_runner_context()


def test_raw_python_probe_rejects_unaccepted_windows_before_chrome(monkeypatch):
    local_scan = _load_local_scan_module()
    monkeypatch.setattr(local_scan.sys, 'platform', 'win32')
    monkeypatch.setattr(
        local_scan,
        '_ensure_local_chrome',
        lambda *_args: pytest.fail('Windows probe must not open Chrome'),
    )
    monkeypatch.setattr(
        'sys.argv',
        ['local_scan.py', '--probe-url', 'https://auto.ru/moskva/cars/'],
    )

    with pytest.raises(RuntimeError, match='Windows monitoring is not accepted'):
        local_scan.main()


def test_raw_headless_probe_is_retired_from_the_production_surface():
    raw_probe = (PROJECT_ROOT / 'scripts' / 'probe_page.py').read_text(encoding='utf-8')

    assert 'PROBE_PAGE_REFUSED' in raw_probe
    assert 'async_playwright' not in raw_probe
    assert '.goto(' not in raw_probe


def test_macos_shell_launcher_checks_the_verified_lock_before_service_startup():
    local_launcher = (PROJECT_ROOT / 'scripts' / 'local_scan.sh').read_text(encoding='utf-8')

    assert local_launcher.index('verified_interactive_host_runner_required') < local_launcher.index(
        'docker-compose up -d db backup'
    )
    assert '--verify-inherited-fd "$A1_MONITORING_HOST_LOCK_FD"' in local_launcher


def test_probe_url_does_not_require_full_cycle_admission(monkeypatch, tmp_path):
    local_scan = _load_local_scan_module()

    async def probe(_url, _pages):
        return {
            'complete': True,
            'error': None,
            'page_count': 1,
            'hits': [],
            'diagnostics': {},
        }

    monkeypatch.setattr(local_scan, '_env_file_values', lambda _path: {})
    monkeypatch.setattr(local_scan, '_configure_runtime', lambda *_args: 'http://127.0.0.1:19222')
    monkeypatch.setattr(local_scan, '_ensure_local_chrome', lambda *_args: False)
    monkeypatch.setattr(local_scan, '_probe', probe)
    monkeypatch.setattr(
        local_scan,
        '_require_vpn_admission',
        lambda: pytest.fail('probe-url must not require a DB-cycle admission'),
    )
    monkeypatch.setattr(
        'sys.argv',
        ['local_scan.py', '--probe-url', 'https://auto.ru/moskva/cars/', '--evidence-dir', str(tmp_path)],
    )

    assert local_scan.main() == 0
