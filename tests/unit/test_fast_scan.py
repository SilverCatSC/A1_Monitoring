import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.scraper.browser_session import read_page_content
from scripts.local_scan import PACING_PROFILES, _configure_runtime


def test_fast_runtime_reduces_programmed_waits_without_reducing_checks(tmp_path, monkeypatch):
    for key in PACING_PROFILES['fast']:
        monkeypatch.delenv(key, raising=False)
    for key in ('SCAN_PAGES_LIMIT', 'SCAN_ENABLED_ENGINES', 'CAPTCHA_OPERATOR_WAIT_SECONDS',
                'PLACEMENT_RECONCILIATION_ENABLED', 'DATABASE_DSN', 'NETWORK_PROFILE',
                'BROWSER_CDP_URL', 'PLAYWRIGHT_HEADLESS', 'EVIDENCE_DIR', 'VPN_OPERATIONAL_POLICY_PATH'):
        monkeypatch.delenv(key, raising=False)
    args = SimpleNamespace(cdp_port=19223, evidence_dir=str(tmp_path),
        engines='auto_ru,avito', pages=5, probe_url=None, pace='fast',
        captcha_wait_seconds=-1, diagnostic_no_vpn=True)
    _configure_runtime(args, {'DB_PASSWORD': 'test'})
    for key in PACING_PROFILES['fast']:
        assert float(os.environ[key]) == pytest.approx(float(PACING_PROFILES['cautious'][key]) / 5)
    assert os.environ['SCAN_PAGES_LIMIT'] == '5'
    assert os.environ['SCAN_ENABLED_ENGINES'] == 'auto_ru,avito'
    assert os.environ['CAPTCHA_OPERATOR_WAIT_SECONDS'] == '-1'
    assert os.environ['PLACEMENT_RECONCILIATION_ENABLED'] == 'true'


@pytest.mark.parametrize('message', [
    'Page.content: Unable to retrieve content because the page is navigating and changing the content.',
    'Execution context was destroyed, most likely because of a navigation.',
])
def test_content_waits_for_existing_navigation_without_reload(message):
    class Page:
        attempts = 0

        async def content(self):
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError(message)
            return '<html>same requested page</html>'

    page = Page()
    assert asyncio.run(read_page_content(page, timeout_seconds=1)) == '<html>same requested page</html>'
    assert page.attempts == 3


def test_content_navigation_wait_is_bounded_and_real_errors_are_preserved():
    class Page:
        async def content(self):
            raise RuntimeError('page is navigating and changing the content')

    with pytest.raises(RuntimeError, match='navigating'):
        asyncio.run(read_page_content(Page(), timeout_seconds=0.01))

    class Closed:
        async def content(self):
            raise RuntimeError('Target page, context or browser has been closed')

    with pytest.raises(RuntimeError, match='closed'):
        asyncio.run(read_page_content(Closed(), timeout_seconds=1))


@pytest.mark.parametrize('arguments,reason', [
    (['--pace', 'unknown'], 'invalid_pace'),
    (['--preflight', '--pace', 'fast'], 'preflight_does_not_accept_scan_parameters'),
])
def test_host_pace_validation_precedes_services(arguments, reason):
    root = Path(__file__).parents[2]
    result = subprocess.run(['bash', str(root / 'scripts/run_monitoring_host_macos.sh'), *arguments],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 64
    assert reason in result.stderr
    assert 'phase=services' not in result.stderr
