import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.service.targeted_search import save_targeted_search, targeted_filter, targeted_keys

KEY = 'avito_voyah_dream_all'


@pytest.mark.parametrize('value', ['', 'avito_voyah_dream_all,', 'auto_sprinter_all',
                                  'avito_sprinter_all,avito_sprinter_all'])
def test_targeted_keys_reject_unknown_or_duplicate_scope(value):
    with pytest.raises(ValueError):
        targeted_keys(value)


def result():
    return {'complete': True, 'error': None, 'page_count': 1, 'hits': [],
            'diagnostics': {'page_1_final_url': targeted_filter(KEY).url}}


def test_targeted_evidence_is_separate_private_and_never_overwritten(tmp_path):
    path = tmp_path / 'host_run/search_result.json'
    record = save_targeted_search(KEY, result(), pages=5, output_path=path, evidence_dir=tmp_path / 'evidence')
    assert record['result']['complete']
    assert record['pages_limit'] == 5
    assert not record['writes_monitoring_cycle']
    assert 'cycle_id' not in record
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())['kind'] == 'targeted_search'
    with pytest.raises(FileExistsError):
        save_targeted_search(KEY, result(), pages=5, output_path=path, evidence_dir=tmp_path / 'evidence')


def test_optional_zero_local_priority_is_not_a_lost_model_or_radius(tmp_path):
    payload = result()
    payload['diagnostics']['page_1_final_url'] = targeted_filter(KEY).url.replace('&localPriority=0', '')
    record = save_targeted_search(KEY, payload, pages=5, output_path=tmp_path / 'result.json', evidence_dir=tmp_path)
    assert record['result']['complete']
    assert record['scope_errors'] == []


@pytest.mark.parametrize('url', [
    'https://www.avito.ru/moskva/avtomobili?radius=0&searchRadius=0&localPriority=0',
    'https://www.avito.ru/all/avtomobili/voyah/dream?radius=0&searchRadius=0&localPriority=0',
    'https://www.avito.ru/moskva/avtomobili/voyah/dream?radius=100&searchRadius=100&localPriority=0',
    'https://www.avito.ru/moskva/avtomobili/voyah/dream',
    targeted_filter(KEY).url.replace('localPriority=0', 'localPriority=1'),
])
def test_redirect_losing_model_or_city_scope_cannot_be_complete(tmp_path, url):
    payload = deepcopy(result())
    payload['diagnostics']['page_1_final_url'] = url
    record = save_targeted_search(KEY, payload, pages=5, output_path=tmp_path / 'result.json', evidence_dir=tmp_path)
    assert not record['result']['complete']
    assert record['result']['error'] == 'targeted_filter_scope_unverified'
    assert payload['complete']  # The original adapter result remains unchanged.


def test_blocked_probe_remains_incomplete_and_keeps_its_original_error(tmp_path):
    payload = {'complete': False, 'error': 'HTTP 429', 'page_count': 0, 'hits': [], 'diagnostics': {}}
    record = save_targeted_search(KEY, payload, pages=5, output_path=tmp_path / 'result.json', evidence_dir=tmp_path)
    assert record['result']['error'] == 'HTTP 429'
    assert not record['result']['complete']


def test_voyah_redirect_stops_before_lazy_load_and_second_page(tmp_path, monkeypatch):
    from playwright.async_api import async_playwright

    from app.config import settings
    from app.scraper.avito import AvitoAdapter

    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    requests = []

    async def unexpected_lazy_load(*_args, **_kwargs):
        pytest.fail('must not lazy-load a broader catalogue after a model redirect')

    monkeypatch.setattr('app.scraper.avito.load_more_results', unexpected_lazy_load)

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            await page.set_content(
                '<div data-marker="catalog-serp"><div data-marker="item" data-item-id="1234567890">'
                '<a data-marker="item-title" href="https://www.avito.ru/moskva/avtomobili/car_1234567890">'
                'Другая машина</a></div></div>')

            class RedirectedPage:
                # Model the final address without navigating to any real website.
                url = 'https://www.avito.ru/moskva/avtomobili?radius=0&searchRadius=0'

                def __getattr__(self, name):
                    return getattr(page, name)

                async def goto(self, url, **_kwargs):
                    requests.append(url)
                    return SimpleNamespace(status=200)

            @asynccontextmanager
            async def fixture_page(_playwright, source=None):
                yield RedirectedPage()

            monkeypatch.setattr('app.scraper.avito.browser_page', fixture_page)
            try:
                return await AvitoAdapter().scan_filter(targeted_filter(KEY).url, max_pages=5)
            finally:
                await browser.close()

    scan = asyncio.run(exercise())
    assert not scan.complete and not scan.hits and scan.page_count == 0
    assert 'search filter scope lost' in scan.error
    assert scan.diagnostics['page_1_state'] == 'filter_scope_mismatch'
    provisional = scan.diagnostics['page_1_rejected_scope_snapshot']['provisional_hits']
    assert len(provisional) == 1 and provisional[0]['position'] == 1
    assert requests == [targeted_filter(KEY).url]
