import base64
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request

import pytest

from scripts import build_live_agent_packet, doctor, local_api


def _authenticated_values():
    return {
        'AUTH_ENABLED': 'true',
        'ADMIN_USERNAME': 'local-admin',
        'ADMIN_PASSWORD': 'local-password-that-never-prints',
    }


class _Response:
    def __init__(self, body: bytes = b'', status: int = 200):
        self.body = body
        self.status = status

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_local_api_auth_header_is_in_memory_and_never_targets_remote_hosts():
    values = _authenticated_values()
    headers = local_api.local_api_auth_headers(values)
    request = local_api.local_api_request(
        'http://127.0.0.1:18000/api/v1/dashboard', headers=headers
    )

    expected = base64.b64encode(b'local-admin:local-password-that-never-prints').decode('ascii')
    assert request.get_header('Authorization') == f'Basic {expected}'
    assert local_api.local_api_auth_headers({'AUTH_ENABLED': 'false'}) == {}
    with pytest.raises(local_api.LocalApiAuthenticationError):
        local_api.local_api_auth_headers({'AUTH_ENABLED': 'true'})
    with pytest.raises(ValueError, match='loopback'):
        local_api.local_api_request('https://example.test:443/api/v1/dashboard', headers=headers)
    with pytest.raises(ValueError, match='explicit port'):
        local_api.local_api_request('http://localhost/api/v1/dashboard', headers=headers)


def test_local_api_refuses_redirects_when_a_header_is_available(monkeypatch):
    captured = {}

    class Opener:
        def open(self, request, *, timeout):
            captured['request'] = request
            captured['timeout'] = timeout
            return _Response()

    def fake_build_opener(handler):
        captured['handler'] = handler
        return Opener()

    monkeypatch.setattr(local_api, 'build_opener', fake_build_opener)
    request = local_api.local_api_request('http://localhost:18000/api/v1/ready')

    assert local_api.open_local_api(request, timeout=3) is not None
    assert isinstance(captured['handler'], local_api._RejectRedirect)
    assert captured['timeout'] == 3
    with pytest.raises(ValueError, match='loopback'):
        local_api.open_local_api(Request('https://example.test:443/'), timeout=3)


def test_doctor_checks_authenticated_dashboard_without_printing_credentials(monkeypatch, capsys):
    values = {
        **_authenticated_values(),
        'DB_PASSWORD': 'database-password-that-never-prints',
        'SOURCE_GOOGLE_SHEET_EXPORT_URL': 'https://example.test/sheet.csv',
        'SCHEDULER_ENABLED': 'false',
        'APP_BIND_PORT': '18000',
    }
    requests = []

    def fake_open(request, *, timeout):
        requests.append((request, timeout))
        body = b'{"database":"ok"}' if request.full_url.endswith('/ready') else b''
        return _Response(body=body)

    monkeypatch.setattr(doctor, 'env_values', lambda: values)
    monkeypatch.setattr(doctor, 'chrome_available', lambda: True)
    monkeypatch.setattr(doctor.shutil, 'which', lambda _: '/usr/bin/fake')
    monkeypatch.setattr(doctor.subprocess, 'run', lambda *_, **__: SimpleNamespace(returncode=0))
    monkeypatch.setattr(doctor, 'open_local_api', fake_open)
    monkeypatch.setattr(doctor.sys, 'argv', ['doctor.py', '--http'])

    assert doctor.main() == 0
    output = capsys.readouterr().out
    protected = [request for request, _ in requests if '/ready' not in request.full_url]
    expected_header = local_api.local_api_auth_headers(values)['Authorization']

    assert protected
    assert all(request.get_header('Authorization') == expected_header for request in protected)
    assert requests[0][0].get_header('Authorization') is None
    assert values['ADMIN_USERNAME'] not in output
    assert values['ADMIN_PASSWORD'] not in output
    assert expected_header not in output


def test_live_packet_fetch_uses_the_same_local_header(monkeypatch):
    headers = local_api.local_api_auth_headers(_authenticated_values())
    captured = {}

    def fake_open(request, *, timeout):
        captured['request'] = request
        captured['timeout'] = timeout
        return _Response(body=json.dumps({'id': 'cycle'}).encode('utf-8'))

    monkeypatch.setattr(build_live_agent_packet, 'open_local_api', fake_open)
    payload = build_live_agent_packet._get_json(
        'http://127.0.0.1:18000/api/v1/status/cycles/cycle', headers=headers
    )

    assert payload == {'id': 'cycle'}
    assert captured['request'].get_header('Authorization') == headers['Authorization']
    assert captured['timeout'] == 10


def test_authenticated_ui_and_macos_ai_wrappers_use_the_local_contract() -> None:
    root = Path(__file__).parents[2]
    ui_check = (root / 'scripts' / 'check_ui.py').read_text(encoding='utf-8')
    ai_review = (root / 'scripts' / 'run_ai_review_macos.sh').read_text(encoding='utf-8')

    assert 'local_api_credentials' in ui_check
    assert "context_options['http_credentials']" in ui_check
    assert "'origin': base" in ui_check
    assert "local_api_request(base + '/api/v1/ready')" in ui_check
    assert 'scripts/local_api.py' in ai_review
    assert 'curl -fsS --max-time 10 "http://127.0.0.1:18000/api/v1/status/cycles/$CYCLE_ID"' not in ai_review
