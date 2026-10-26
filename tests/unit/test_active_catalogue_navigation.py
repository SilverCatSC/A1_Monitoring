"""Dealer inventory must not guess nonexistent pages or follow another seller."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from playwright.async_api import async_playwright
from test_lazy_load import card

from app.config import settings
from app.scraper.auto_ru import AutoRuAdapter
from app.scraper.avito import AvitoAdapter


@pytest.mark.parametrize('source', ['auto_ru', 'avito'])
@pytest.mark.parametrize('navigation', ['missing', 'observed', 'other_seller'])
def test_seller_only_follows_observed_catalogue_navigation(tmp_path, monkeypatch, source, navigation):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds',
                'auto_ru_page_delay_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    url = ('https://auto.ru/diler/cars/all/a1_avto_moskva/' if source == 'auto_ru'
           else 'https://www.avito.ru/brands/a1auto/all/avtomobili')
    other = url.replace('a1_avto_moskva', 'other').replace('a1auto', 'other')
    next_url = (other if navigation == 'other_seller' else url) + '?from_navigation=1&cursor=two'
    documents = []

    def markup(number):
        root = ('class="ListingCars__items"' if source == 'auto_ru'
                else 'data-marker="catalog-serp"')
        body = f'<div {root}>{card(source,number)}</div>'
        if number == 1 and navigation == 'missing':
            return body + '<h2>Отзывы</h2><p>Нет элементов навигации каталога</p>'
        next_link = (f'<a class="ListingPagination__next" href="{next_url}">Следующая</a>'
                     if source == 'auto_ru' else
                     f'<a data-marker="pagination-button/nextPage" href="{next_url}">Следующая</a>')
        if source == 'auto_ru':
            pager = (f'<div data-seo="listing-pagination"><a class="ListingPagination__page Button_checked">{number}</a>'
                     + (next_link if number == 1 else '') + '</div>')
        else:
            pager = (f'<div data-marker="pagination-button"><span class="item_current-x" data-marker="pagination-button/page({number})">{number}</span>'
                     + (next_link if number == 1 else '') + '</div>')
        return body + pager

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()

            async def route_request(route):
                if route.request.resource_type != 'document':
                    await route.abort()
                    return
                documents.append(route.request.url)
                await route.fulfill(content_type='text/html; charset=utf-8', body=markup(len(documents)))

            await page.route('**/*', route_request)

            @asynccontextmanager
            async def local_page(_playwright, source=None):
                yield page

            monkeypatch.setattr(f'app.scraper.{source}.browser_page', local_page)
            adapter = AutoRuAdapter if source == 'auto_ru' else AvitoAdapter
            try:
                return await adapter().scan_filter(url, 5, seller_catalogue=True)
            finally:
                await browser.close()

    result = asyncio.run(exercise())
    if navigation == 'observed':
        assert documents == [url, next_url]
        assert result.complete and result.exhausted
        assert len(result.hits) == 2
    else:
        assert documents == [url]
        assert not result.complete and not result.exhausted
        assert result.diagnostics['catalogue_navigation_unproven'] is True
        assert len(result.hits) == 1  # Positive evidence survives incomplete coverage.
