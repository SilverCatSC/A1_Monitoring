"""Both marketplaces must reach page five and keep deep results in reports."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import async_playwright
from test_lazy_load import card

from app.config import settings
from app.models import EngineType, ObservationState
from app.scraper.auto_ru import AutoRuAdapter
from app.scraper.avito import AvitoAdapter
from app.service.analytics import summarize
from app.service.report import _page_statistics


@pytest.mark.parametrize('source', ['auto_ru', 'avito'])
def test_find_target_on_fifth_page_and_stop(tmp_path, monkeypatch, source):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds',
                'auto_ru_page_delay_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    documents = []

    async def exercise():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()

            async def route_page(route):
                query = parse_qs(urlsplit(route.request.url).query)
                number = int(query.get('page' if source == 'auto_ru' else 'p', ['1'])[0])
                if route.request.resource_type != 'document':
                    await route.abort()
                    return
                documents.append(number)
                if source == 'auto_ru':
                    root = f'<div class="ListingCars__items">{card(source,number)}</div>'
                    pager = (f'<div data-seo="listing-pagination"><a class="ListingPagination__page Button_checked">{number}</a>'
                             f'<a class="ListingPagination__next" href="?page={number+1}">Следующая</a></div>')
                else:
                    root = f'<div data-marker="catalog-serp">{card(source,number)}</div>'
                    pager = (f'<div data-marker="pagination-button"><span class="item_current-x" data-marker="pagination-button/page({number})">{number}</span>'
                             f'<a data-marker="pagination-button/nextPage" href="?p={number+1}">Следующая</a></div>')
                await route.fulfill(content_type='text/html; charset=utf-8', body=root+pager)

            await page.route('**/*', route_page)

            @asynccontextmanager
            async def local_page(_playwright, source=None):
                yield page

            monkeypatch.setattr(f'app.scraper.{source}.browser_page', local_page)
            adapter = AutoRuAdapter if source == 'auto_ru' else AvitoAdapter
            url = 'https://auto.ru/moskva/cars/hongqi/hq9/new/' if source == 'auto_ru' else 'https://www.avito.ru/moskva/avtomobili'
            key = f'{source}:{1133335005 if source == "auto_ru" else 1000000005}'
            try:
                # Exercise the adapter's new default depth, rather than overriding it.
                return await adapter().scan_filter(url, target_keys={key})
            finally:
                await browser.close()

    result = asyncio.run(exercise())
    assert documents == [1, 2, 3, 4, 5]
    assert result.complete and not result.exhausted and result.error is None
    assert result.requested_pages == 5
    assert result.hits[-1].page_number == 5
    assert result.hits[-1].raw['card_evidence']


def test_reports_keep_pages_four_and_five_even_after_lowering_limit(monkeypatch):
    monkeypatch.setattr(settings, 'scan_pages_limit', 3)
    rows = [SimpleNamespace(page_number=p, absolute_position=p*20, filter_id='filter',
                            filter=SimpleNamespace(name='Filter'), source=EngineType.AVITO,
                            state=ObservationState.FOUND) for p in (4, 5)]
    stats = _page_statistics(rows)
    assert stats['overall']['page_counts'] == {1: 0, 2: 0, 3: 0, 4: 1, 5: 1}
    assert stats['overall']['page_shares'][5] == 50
    assert summarize(rows)['pages'] == [0, 0, 0, 1, 1]
