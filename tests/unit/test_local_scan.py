import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

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
