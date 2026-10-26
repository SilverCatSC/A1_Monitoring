"""Native lazy loading reduces document navigations without weakening evidence."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from playwright.async_api import async_playwright

from app.config import settings
from app.scraper.auto_ru import AutoRuAdapter
from app.scraper.avito import AvitoAdapter


def card(source, number):
    key = str(1133335000 + number) if source == 'auto_ru' else str(1000000000 + number)
    if source == 'auto_ru':
        return (f'<div class="ListingItemUniversal-test"><a class="ListingItemTitle__link" '
                f'href="https://auto.ru/cars/new/group/hongqi/hq9/1/2/{key}-car/">Car {number}</a></div>')
    return (f'<div data-marker="item" data-item-id="{key}"><a data-marker="item-title" '
            f'href="https://www.avito.ru/moskva/avtomobili/car_{key}">Car {number}</a></div>')


@pytest.mark.parametrize('source', ['auto_ru', 'avito'])
@pytest.mark.parametrize('scenario', ['append', 'append_adjacent', 'virtualized', 'no_progress', 'targets_already_found', 'budget'])
def test_native_lazy_load_uses_one_document_and_preserves_results(tmp_path, monkeypatch, source, scenario):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'auto_ru_page_delay_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'catalogue_lazy_load_timeout_seconds', 0.6)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    documents, xhrs, events = [], [], []
    total = 100 if scenario in {'targets_already_found', 'budget'} else 2
    prefix = 'auto_ru' if source == 'auto_ru' else 'avito'
    targets = {f'{prefix}:{1133335001 if source == "auto_ru" else 1000000001}'}
    if scenario in {'append', 'append_adjacent', 'virtualized'}:
        targets.add(f'{prefix}:{1133335002 if source == "auto_ru" else 1000000002}')
    if scenario in {'no_progress', 'budget'}:
        targets = None

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            root_attr = 'class="ListingCars__items"' if source == 'auto_ru' else 'data-marker="catalog-serp"'
            counter = f'<h1>{total} предложений</h1>' if source == 'auto_ru' else f'<span data-marker="page-title/count">{total}</span>'
            button = '<button id="more" onclick="loadMore()">Показать ещё</button>'
            controls = button if scenario != 'append_adjacent' else ''
            adjacent = f'<div>{button}</div>' if scenario == 'append_adjacent' else ''
            html = counter + f'<div id="primary" {root_attr}>{card(source,1)}{controls}</div>{adjacent}' + '''
                <script>async function loadMore() {
                  const fragment = await (await fetch('/more')).text();
                  if (MODE === 'virtualized') document.querySelector('#primary').innerHTML = fragment;
                  else if (MODE === 'append_adjacent') { document.querySelector('#primary').insertAdjacentHTML('beforeend', fragment); document.querySelector('#more').remove(); }
                  else if (MODE === 'budget') document.querySelector('#more').insertAdjacentHTML('beforebegin', fragment);
                  else { document.querySelector('#more').insertAdjacentHTML('beforebegin', fragment);
                         if (MODE !== 'no_progress') document.querySelector('#more').remove(); }
                }</script>
            '''.replace('MODE', f'"{scenario}"')

            async def route_page(route):
                if route.request.resource_type == 'document':
                    documents.append(route.request.url)
                    await route.fulfill(content_type='text/html; charset=utf-8', body=html)
                else:
                    xhrs.append(route.request.url)
                    await route.fulfill(content_type='text/html; charset=utf-8',
                                        body='' if scenario == 'no_progress' else card(source, len(xhrs)+1))

            await page.route('**/*', route_page)

            @asynccontextmanager
            async def local_page(_playwright, source=None):
                yield page

            monkeypatch.setattr(f'app.scraper.{source}.browser_page', local_page)
            adapter = AutoRuAdapter if source == 'auto_ru' else AvitoAdapter
            url = 'https://auto.ru/moskva/cars/hongqi/hq9/new/?geo_radius=0&rid=213' if source == 'auto_ru' else 'https://www.avito.ru/moskva/avtomobili'
            try:
                return await adapter(progress_callback=events.append).scan_filter(url, max_pages=3, target_keys=targets)
            finally:
                await browser.close()

    result = asyncio.run(exercise())
    assert len(documents) == 1
    if scenario == 'no_progress':
        assert not result.complete and len(xhrs) == 1
        assert 'no progress' in result.error
    elif scenario == 'budget':
        assert not result.complete and len(xhrs) == 2
        assert result.diagnostics['load_actions'] == 3
        assert result.diagnostics['load_budget_exhausted'] is True
    else:
        assert result.complete and result.error is None
        assert len(xhrs) == (0 if scenario == 'targets_already_found' else 1)
        assert len(result.hits) == (1 if scenario == 'targets_already_found' else 2)
        assert [hit.position for hit in result.hits] == list(range(1, len(result.hits)+1))
        assert all(hit.raw.get('card_evidence') for hit in result.hits)
        if scenario == 'targets_already_found':
            assert not result.exhausted
            assert result.diagnostics['target_coverage_complete']
        else:
            assert result.exhausted
            assert any(e['event'] == 'lazy_load_finished' for e in events)
