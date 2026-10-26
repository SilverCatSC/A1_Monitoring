"""Documented pagination always takes priority over load-more within the shared ceiling."""
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

from app.config import settings
from app.scraper.avito import AvitoAdapter


def card(key):
    return (f'<div data-marker="item" data-item-id="{key}">'
            f'<a data-marker="item-title" href="https://www.avito.ru/moskva/avtomobili/car_{key}">'
            f'Машина {key}</a></div>')


def pager(current, total_pages=9):
    pages = []
    for number in range(1, total_pages + 1):
        if number == current:
            pages.append(f'<span class="item_current-x" data-marker="pagination-button/page({number})">'
                         f'{number}</span>')
        else:
            pages.append(f'<a data-marker="pagination-button/page({number})" href="?p={number}">{number}</a>')
    pages.append(f'<a data-marker="pagination-button/nextPage" href="?p={current + 1}"></a>')
    return '<ul data-marker="pagination-button">' + ''.join(pages) + '</ul>'


def page_html(current, cards):
    return (
        '<span data-marker="page-title/count">100</span>'
        f'<div id="primary" data-marker="catalog-serp">{cards}'
        '<button id="more" onclick="loadMore()">Показать ещё</button></div>'
        + pager(current) +
        '<script>async function loadMore() { await fetch("/more"); }</script>'
    )


def run_avito_scan(tmp_path, monkeypatch, url, max_pages, target_keys=None, seller_catalogue=False):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    documents, xhrs = [], []

    async def exercise():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()

            async def route_page(route):
                number = int(parse_qs(urlsplit(route.request.url).query).get('p', ['1'])[0])
                if route.request.resource_type == 'document':
                    documents.append(number)
                    await route.fulfill(
                        content_type='text/html; charset=utf-8',
                        body=page_html(number, card(1000000000 + number)),
                    )
                else:
                    xhrs.append(route.request.url)
                    await route.fulfill(content_type='text/html; charset=utf-8', body='')

            await page.route('**/*', route_page)

            @asynccontextmanager
            async def local_page(_playwright, source=None):
                yield page

            monkeypatch.setattr('app.scraper.avito.browser_page', local_page)
            try:
                return await AvitoAdapter().scan_filter(
                    url, max_pages, target_keys=target_keys, seller_catalogue=seller_catalogue,
                )
            finally:
                await browser.close()

    return asyncio.run(exercise()), documents, xhrs


def test_pager_steals_no_budget_from_load_more_when_pages_are_reserved(tmp_path, monkeypatch):
    result, documents, xhrs = run_avito_scan(
        tmp_path, monkeypatch, 'https://www.avito.ru/moskva/avtomobili', max_pages=3,
    )
    # All three document slots were used by pagination; the visible load-more
    # button never consumed a page action.
    assert documents == [1, 2, 3]
    assert xhrs == []
    assert result.error is None
    assert result.complete and result.page_count == 3
    assert not result.exhausted  # the catalogue continues; coverage of the tail is not claimed
    assert result.diagnostics['page_1_lazy_budget_reserved'] == 2
    assert result.diagnostics['page_2_lazy_budget_reserved'] == 1
    assert 'load_budget_exhausted' not in result.diagnostics


def test_targets_found_early_stop_pagination_without_extra_actions(tmp_path, monkeypatch):
    result, documents, xhrs = run_avito_scan(
        tmp_path, monkeypatch, 'https://www.avito.ru/moskva/avtomobili', max_pages=3,
        target_keys={'avito:1000000001'},
    )
    assert documents == [1]
    assert xhrs == []
    assert result.complete and result.diagnostics['target_coverage_complete']
    assert not result.exhausted
    assert result.diagnostics['page_1_lazy_budget_reserved'] == 2


def test_strict_seller_catalogue_keeps_pagination_and_does_not_claim_completeness(tmp_path, monkeypatch):
    url = 'https://www.avito.ru/brands/a1auto/all/avtomobili'
    result, documents, xhrs = run_avito_scan(
        tmp_path, monkeypatch, url, max_pages=2, seller_catalogue=True,
    )
    assert documents == [1, 2]
    assert xhrs == []
    # The scan itself finished its planned budget...
    assert result.complete and result.error is None
    # ...but the catalogue end is not proven, so strict seller reconciliation
    # (result.complete and result.exhausted) must refuse to deactivate.
    assert not result.exhausted
    assert not (result.complete and result.exhausted)
