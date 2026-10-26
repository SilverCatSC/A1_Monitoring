"""Offline profile fixtures: the first 24 cards are not the inventory end.

Every browser request is fulfilled locally, including documents and XHRs.
"""
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import async_playwright
from test_seller_active_counter import avito_card, tab_bar

from app.config import settings
from app.scraper.avito import AvitoAdapter
from app.scraper.result_scope import pagination_next_url, pagination_state

URL = 'https://www.avito.ru/brands/a1auto/items?s=profile_search_show_all'


def cards(first, stop):
    return ''.join(avito_card(1000000000 + index) for index in range(first, stop))


def pager(current, *, next_href=None):
    # Profile pagination uses a different wrapper and aria-current, with no
    # separate next arrow. The numbered link is the observed continuation.
    return '<nav data-marker="pagination">' + ''.join(
        f'<span data-marker="pagination-button/page({number})" aria-current="page">{number}</span>'
        if number == current else
        f'<a data-marker="pagination-button/page({number})" href="{next_href or f"?p={number}"}">{number}</a>'
        for number in (1, 2)
    ) + '</nav>'


def test_native_numeric_pager_and_disabled_arrow():
    markup = pager(1) + '<a data-marker="pagination-button/nextPage" aria-disabled="true" href="?p=7"></a>'
    assert pagination_state(markup, 'avito') == {
        'current': 1, 'total': None, 'last': False, 'has_next': True,
    }
    assert pagination_next_url(markup, 'avito', URL) == URL.split('?')[0] + '?p=2'
    assert pagination_state(pager(2), 'avito')['last']
    assert pagination_next_url(pager(2), 'avito', URL + '&p=2') is None


def test_native_cursor_link_is_preserved_without_inventing_page_parameters():
    destination = URL + '&from_navigation=1&cursor=two'
    markup = ('<a data-marker="pagination-button/nextPage" '
              f'href="{destination}">Следующая</a>')
    assert pagination_next_url(markup, 'avito', URL) == destination
    assert pagination_next_url(markup, 'avito', destination) is None


@pytest.mark.parametrize('href', [
    '?s=closed&p=2', '?p=3', '?price_from=10&p=2', '/brands/another/items?p=2',
    'https://example.org/brands/a1auto/items?p=2',
])
def test_pagination_never_changes_seller_filters_or_skips_a_page(href):
    assert pagination_next_url(pager(1, next_href=href), 'avito', URL) is None


def test_hidden_and_recommended_pagers_are_not_continuations():
    for wrapper in ('<div hidden>{}</div>', '<aside>{}</aside>',
                    '<div style="display:none">{}</div>'):
        assert not pagination_state(wrapper.format(pager(1)), 'avito')['has_next']
        assert pagination_next_url(wrapper.format(pager(1)), 'avito', URL) is None


@pytest.mark.parametrize('scenario', [
    'numeric_pager', 'revealed_pager', 'append', 'revealed_button', 'virtualized',
    'infinite_scroll', 'missing_continuation', 'recommendations_only', 'blocked_page',
    'counter_conflict', 'false_last', 'closed_redirect', 'canonical_append',
    'canonical_history_append', 'canonical_history_other_seller',
    'canonical_history_closed', 'canonical_history_page', 'canonical_history_filter',
])
def test_reads_29_unique_cards_or_keeps_inventory_incomplete(tmp_path, monkeypatch, scenario):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'catalogue_lazy_load_enabled', True)
    monkeypatch.setattr(settings, 'catalogue_lazy_load_timeout_seconds', 1.5)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    documents, xhrs = [], []
    preview = 'https://www.avito.ru/brands/a1auto/all/avtomobili'

    async def exercise():
        async with async_playwright() as p:
            # Refuse all unhandled traffic, including redirect chains that
            # Chromium may follow without invoking a route handler again.
            browser = await p.chromium.launch(channel='chrome', headless=True,
                                              proxy={'server': 'http://127.0.0.1:1'})
            page = await browser.new_page(service_workers='block')

            async def route_request(route):
                if route.request.resource_type != 'document':
                    xhrs.append(route.request.url)
                    await route.fulfill(content_type='text/html; charset=utf-8', body=cards(23, 29))
                    return
                documents.append(route.request.url)
                number = int(parse_qs(urlsplit(route.request.url).query).get('p', ['1'])[0])
                if route.request.url == preview:
                    markup = tab_bar(29, 41) + cards(0, 12) + (
                        f'<a data-marker="item_list_with_filters/show_all_button" href="{URL}">Показать все</a>')
                elif number == 2 and scenario == 'blocked_page':
                    await route.fulfill(status=429, content_type='text/html', body='Доступ ограничен')
                    return
                elif number == 2:
                    markup = '<div data-marker="catalog-serp">' + cards(23, 29) + '</div>' + pager(2)
                    if scenario == 'counter_conflict':
                        markup += tab_bar(30, 41)
                    elif scenario == 'closed_redirect':
                        markup += "<script>history.replaceState({}, '', '?s=closed&p=2')</script>"
                else:
                    # The stale empty marker must not hide the hydrated grid.
                    markup = ('<div data-marker="catalog-serp"></div>'
                              '<div id="grid">' + cards(0, 24) + '</div><div id="footer"></div>')
                    button = '<button onclick="loadMore()">Показать ещё 5</button>'
                    if scenario in {'numeric_pager', 'blocked_page', 'counter_conflict', 'closed_redirect'}:
                        markup += pager(1)
                    elif scenario in {'append', 'virtualized', 'canonical_append'} or scenario.startswith('canonical_history_'):
                        markup = markup.replace('<div id="footer"></div>', '<div id="footer">' + button + '</div>')
                    elif scenario == 'false_last':
                        markup += '<nav data-marker="pagination"><span data-marker="pagination-button/page(1)" aria-current="page">1</span></nav>'
                    elif scenario == 'recommendations_only':
                        markup += '<aside><h2>Похожие объявления</h2>' + cards(24, 29) + button + '</aside>'
                    markup += '''<style>#grid > div {height: 180px}</style>
                        <script>
                        let revealed = false;
                        async function loadMore() {
                            const fragment = await (await fetch('/more')).text();
                            if (MODE === 'virtualized') grid.innerHTML = fragment;
                            else grid.insertAdjacentHTML('beforeend', fragment);
                            footer.innerHTML = '';
                            if (MODE === 'canonical_history_other_seller') history.replaceState({}, '', '/brands/another/items/all/avtomobili');
                            if (MODE === 'canonical_history_closed') history.replaceState({}, '', '?s=closed');
                            if (MODE === 'canonical_history_page') history.replaceState({}, '', '?p=2');
                            if (MODE === 'canonical_history_filter') history.replaceState({}, '', '?price_from=10');
                        }
                        window.addEventListener('scroll', () => {
                            if (revealed || window.scrollY + innerHeight < grid.offsetTop + grid.offsetHeight) return;
                            revealed = true;
                            if (MODE === 'revealed_button') footer.innerHTML = BUTTON;
                            if (MODE === 'revealed_pager') footer.innerHTML = PAGER;
                            if (MODE === 'infinite_scroll') loadMore();
                        });
                        </script>'''.replace('MODE', repr(scenario)).replace('BUTTON', repr(button)).replace('PAGER', repr(pager(1)))
                    if scenario.startswith('canonical_history_'):
                        markup += "<script>history.replaceState({}, '', '/brands/a1auto/items/all/avtomobili?s=profile_search_show_all&sellerId=current_seller')</script>"
                await route.fulfill(content_type='text/html; charset=utf-8', body=markup)

            await page.route('**/*', route_request)
            if scenario == 'canonical_append':
                original_goto = page.goto

                async def canonical_goto(url, **kwargs):
                    # Model goto's final successful document, without a real
                    # redirect chain or any request to the marketplace.
                    if url == URL:
                        url = ('https://www.avito.ru/brands/a1auto/items/all/avtomobili'
                               '?s=profile_search_show_all&sellerId=current_seller')
                    return await original_goto(url, **kwargs)

                monkeypatch.setattr(page, 'goto', canonical_goto)

            @asynccontextmanager
            async def local_page(_playwright, source=None):
                yield page

            monkeypatch.setattr('app.scraper.avito.browser_page', local_page)
            try:
                # Targets are intentionally all on the first page: an inventory
                # scan must still collect the other active publications.
                return await AvitoAdapter().scan_filter(
                    preview, max_pages=5, seller_catalogue=True,
                    target_keys={'avito:1000000000'}, evidence_keys=set(),
                )
            finally:
                await browser.close()

    result = asyncio.run(exercise())
    invalid_history = scenario.startswith('canonical_history_') and scenario != 'canonical_history_append'
    success = not invalid_history and scenario not in {'missing_continuation', 'recommendations_only', 'blocked_page', 'counter_conflict', 'false_last', 'closed_redirect'}
    assert result.complete is success, (result.error, result.diagnostics)
    assert result.exhausted is success
    if success:
        assert len(result.hits) == len({hit.url for hit in result.hits}) == 29
        assert result.diagnostics['seller_active_counter'] == 29
        assert 'target_coverage_complete' not in result.diagnostics
    else:
        assert result.error
        if invalid_history:
            navigation = result.diagnostics['page_1_lazy_navigation']
            assert navigation['document_url'] == URL
            assert not (navigation['scope_matches'] and navigation['page_matches'])
        elif scenario != 'counter_conflict':
            assert len({hit.url for hit in result.hits}) == 24
    if scenario in {'append', 'revealed_button', 'virtualized', 'infinite_scroll', 'canonical_append'} or scenario.startswith('canonical_history_'):
        assert len(documents) == 2 and len(xhrs) == 1
        if not invalid_history:
            assert result.diagnostics['page_1_lazy_batches'] == 1
    elif scenario in {'numeric_pager', 'revealed_pager', 'blocked_page', 'counter_conflict', 'closed_redirect'}:
        assert len(documents) == 3 and not xhrs
    else:
        assert not xhrs
