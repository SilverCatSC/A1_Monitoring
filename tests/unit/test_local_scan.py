import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[2] / 'scripts' / 'local_scan.py'
SPEC = importlib.util.spec_from_file_location('local_scan_script', SCRIPT)
assert SPEC and SPEC.loader
local_scan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local_scan)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def test_empty_running_chrome_gets_a_controllable_page(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        if isinstance(request, str):
            return _Response([])
        assert request.method == 'PUT'
        return _Response({'type': 'page', 'id': 'new-page'})

    monkeypatch.setattr(local_scan, 'urlopen', fake_urlopen)
    assert local_scan._ensure_cdp_page('http://127.0.0.1:19222') is True
    assert requests[1][0].full_url.endswith('/json/new?about%3Ablank')


def test_reused_chrome_is_prepared_before_scan(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(local_scan, '_cdp_ready', lambda _url: True)
    monkeypatch.setattr(local_scan, '_ensure_cdp_page', lambda url: calls.append(url))
    assert local_scan._ensure_local_chrome(
        'http://127.0.0.1:19222', Path(tmp_path)
    ) is False
    assert calls == ['http://127.0.0.1:19222']


def test_local_scan_defaults_to_cautious_pacing(monkeypatch, tmp_path):
    monkeypatch.setenv('PLACEMENT_RECONCILIATION_ENABLED', 'false')
    for key in local_scan.PACING_PROFILES['cautious']:
        monkeypatch.delenv(key, raising=False)
    args = SimpleNamespace(
        cdp_port=19222,
        evidence_dir=str(tmp_path / 'evidence'),
        engines='auto_ru,avito',
        pages=3,
        probe_url=None,
        pace='cautious',
    )

    assert local_scan._configure_runtime(args, {'DB_PASSWORD': 'safe-pass'}) == (
        'http://127.0.0.1:19222'
    )
    assert local_scan.os.environ['SCAN_FILTER_PAUSE_MIN_SECONDS'] == '12'
    assert local_scan.os.environ['SCAN_PAGE_PAUSE_MAX_SECONDS'] == '12'
    assert local_scan.os.environ['AUTO_RU_PAGE_DELAY_SECONDS'] == '5'
    assert local_scan.os.environ['PLACEMENT_RECONCILIATION_ENABLED'] == 'true'
    args.placement_identity = True
    local_scan._configure_runtime(args, {'DB_PASSWORD': 'safe-pass'})
    assert local_scan.os.environ['PLACEMENT_RECONCILIATION_ENABLED'] == 'true'
    args.placement_identity = False
    local_scan._configure_runtime(args, {'DB_PASSWORD': 'safe-pass'})
    assert local_scan.os.environ['PLACEMENT_RECONCILIATION_ENABLED'] == 'true'
    args.probe_url = 'https://auto.ru/cars/'
    local_scan._configure_runtime(args, {})
    assert local_scan.os.environ['PLACEMENT_RECONCILIATION_ENABLED'] == 'false'


def test_private_chrome_uses_a_separate_extension_free_endpoint():
    args = local_scan._parser().parse_args([])
    assert args.cdp_port == 19223
    assert args.browser_profile == str(local_scan.DEFAULT_PROFILE)
    assert args.browser_profile.endswith('local_chrome_isolated_profile')


def test_new_monitoring_chrome_disables_extensions(monkeypatch, tmp_path):
    commands = []
    ready = iter([False, True])
    monkeypatch.setattr(local_scan, '_cdp_ready', lambda _url: next(ready))
    monkeypatch.setattr(local_scan, '_ensure_cdp_page', lambda _url: False)
    monkeypatch.setattr(local_scan, '_chrome_executable', lambda: '/fake/chrome')
    monkeypatch.setattr(local_scan.subprocess, 'Popen', lambda command, **_kwargs: commands.append(command))
    assert local_scan._ensure_local_chrome('http://127.0.0.1:19223', tmp_path / 'profile') is True
    assert '--disable-extensions' in commands[0]
    assert '--remote-debugging-port=19223' in commands[0]


@pytest.mark.parametrize('completion_status, exit_code, marker', [
    ('completed', 0, 'LOCAL_SCAN_OK'),
    ('partial', 2, 'LOCAL_SCAN_PARTIAL'),
])
def test_local_scan_reports_cycle_id_before_its_final_status(
    monkeypatch, capsys, tmp_path, completion_status, exit_code, marker,
):
    cycle_result = {
        'scan': {'filters_scanned': 1, 'found': 0, 'missed_confirmed': 0, 'missed_uncertain': 0},
        'completion': {'status': completion_status, 'technical_errors': 0,
                       'search_skipped': completion_status == 'partial'},
        'cycle': {'id': 'cycle-1'},
        'seller_preflight': {},
        'direct_cards': {},
    }

    class Context:
        def __enter__(self):
            return 'db'

        def __exit__(self, *_):
            return None

    class Service:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self):
            return cycle_result

    monkeypatch.setattr(local_scan, '_env_file_values', lambda _: {'DB_PASSWORD': 'safe-pass'})
    monkeypatch.setattr(local_scan, '_configure_runtime', lambda *_: 'http://127.0.0.1:19222')
    monkeypatch.setattr(local_scan, '_prepare_vpn_admission_directory', lambda: None)
    monkeypatch.setattr(local_scan, '_require_vpn_admission', lambda: None)
    monkeypatch.setattr(local_scan, '_require_interactive_host_runner_context', lambda: None)
    monkeypatch.setattr(local_scan, '_ensure_local_chrome', lambda *_: False)
    monkeypatch.setenv('A1_MONITORING_HOST_RUNNER_CONTEXT', '1')
    monkeypatch.setattr('app.db.get_db_context', lambda: Context())
    monkeypatch.setattr('app.service.cycle.MonitoringCycleService', Service)
    monkeypatch.setattr(
        'sys.argv',
        ['local_scan.py', '--evidence-dir', str(tmp_path / 'evidence')],
    )

    assert local_scan.main() == exit_code
    output = capsys.readouterr().out
    assert output.index('LOCAL_CYCLE_ID cycle-1') < output.index(marker)


def test_local_scan_routes_an_explicit_retry_through_the_cycle_service(monkeypatch, tmp_path):
    events = []
    cycle_result = {
        'scan': {'filters_scanned': 1, 'found': 0, 'missed_confirmed': 0, 'missed_uncertain': 0},
        'completion': {
            'status': 'completed', 'technical_errors': 0, 'links_need_review': 0,
            'direct_cards_incomplete': 0,
        },
        'cycle': {'id': 'retry-cycle-1'},
        'seller_preflight': {},
        'direct_cards': {},
    }

    class Context:
        def __enter__(self):
            return 'db'

        def __exit__(self, *_):
            return None

    class Service:
        def __init__(self, *_args, **_kwargs):
            pass

        def retry(self, cycle_id):
            events.append(cycle_id)
            return cycle_result

        def run(self):
            pytest.fail('ordinary run must not replace an explicit retry')

    monkeypatch.setattr(local_scan, '_env_file_values', lambda _: {'DB_PASSWORD': 'safe-pass'})
    monkeypatch.setattr(local_scan, '_configure_runtime', lambda *_: 'http://127.0.0.1:19222')
    monkeypatch.setattr(local_scan, '_prepare_vpn_admission_directory', lambda: None)
    monkeypatch.setattr(local_scan, '_require_vpn_admission', lambda: None)
    monkeypatch.setattr(local_scan, '_require_interactive_host_runner_context', lambda: None)
    monkeypatch.setattr(local_scan, '_ensure_local_chrome', lambda *_: False)
    monkeypatch.setenv('A1_MONITORING_HOST_RUNNER_CONTEXT', '1')
    monkeypatch.setattr('app.db.get_db_context', lambda: Context())
    monkeypatch.setattr('app.service.cycle.MonitoringCycleService', Service)
    monkeypatch.setattr(
        'sys.argv',
        [
            'local_scan.py', '--retry-cycle', '11111111-1111-4111-8111-111111111111',
            '--evidence-dir', str(tmp_path / 'evidence'),
        ],
    )

    assert local_scan.main() == 0
    assert events == ['11111111-1111-4111-8111-111111111111']
