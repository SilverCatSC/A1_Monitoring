"""The 12-card brand preview is not the complete 29-card seller inventory."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from playwright.async_api import async_playwright
from test_seller_active_counter import avito_card, tab_bar

from app.config import settings
from app.scraper.avito import AvitoAdapter
from app.scraper.challenge_retry import _same_recovered_page
from app.scraper.result_scope import avito_catalogue_show_all_url

PREVIEW = 'https://www.avito.ru/brands/a1auto/all/avtomobili'
FULL = 'https://www.avito.ru/brands/a1auto/items?s=profile_search_show_all'


@pytest.mark.parametrize('href', [
    'https://example.org/brands/a1auto/items?s=profile_search_show_all',
    '/brands/another/items?s=profile_search_show_all',
    '/brands/a1auto/items?s=closed',
    '/brands/a1auto/items?s=profile_search_show_all&status=closed',
])
def test_only_same_seller_active_full_inventory_control_is_followed(href):
    markup = f'<a data-marker="item_list_with_filters/show_all_button" href="{href}">Показать все</a>'
    assert avito_catalogue_show_all_url(markup, PREVIEW) is None


def test_hidden_or_unmarked_show_all_links_are_ignored():
    link = f'<a data-marker="item_list_with_filters/show_all_button" href="{FULL}">Показать все</a>'
    assert avito_catalogue_show_all_url('<aside>' + link + '</aside>', PREVIEW) is None
    assert avito_catalogue_show_all_url(link.replace('<a ', '<a hidden '), PREVIEW) is None
    assert avito_catalogue_show_all_url(f'<a href="{FULL}">Показать все</a>', PREVIEW) is None


@pytest.mark.parametrize('canonical_redirect', [False, True])
def test_preview_is_expanded_once_and_all_unique_cards_are_collected(tmp_path, monkeypatch, canonical_redirect):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    monkeypatch.setattr(settings, 'catalogue_lazy_load_enabled', False)
    documents = []

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()

            async def route_request(route):
                if route.request.resource_type != 'document':
                    await route.abort()
                    return
                documents.append(route.request.url)
                count = 12 if route.request.url == PREVIEW else 29
                more = ('<a data-marker="item_list_with_filters/show_all_button" '
                        'href="/brands/a1auto/items?s=profile_search_show_all">Показать все</a>')
                markup = tab_bar(29, 41) + '<div data-marker="catalog-serp">'
                markup += ''.join(avito_card(1000000000 + i) for i in range(count))
                markup += '</div>' + more
                if route.request.url == FULL and canonical_redirect:
                    markup += "<script>history.replaceState({}, '', '/brands/a1auto/items/all/avtomobili?s=profile_search_show_all&sellerId=current_seller')</script>"
                await route.fulfill(content_type='text/html; charset=utf-8', body=markup)

            await page.route('**/*', route_request)

            @asynccontextmanager
            async def local_page(_playwright, source=None):
                yield page

            monkeypatch.setattr('app.scraper.avito.browser_page', local_page)
            try:
                return await AvitoAdapter().scan_filter(PREVIEW, 5, seller_catalogue=True)
            finally:
                await browser.close()

    result = asyncio.run(exercise())
    expected = [PREVIEW, FULL]
    assert documents == expected, (result.error, result.diagnostics)
    assert result.complete and result.exhausted
    assert len(result.hits) == len({hit.url for hit in result.hits}) == 29
    assert result.diagnostics['catalogue_show_all_url'] == FULL
    assert result.diagnostics['catalogue_preview_evidence']
    assert result.diagnostics['load_actions'] == 2
    assert result.diagnostics['catalogue_end_proven_by'] == 'seller_active_counter'


def test_captcha_recovery_accepts_only_same_brand_expanded_document():
    final = 'https://www.avito.ru/brands/a1auto/items/all/avtomobili?s=profile_search_show_all&sellerId=current_seller'
    assert _same_recovered_page(FULL, final, final)
    assert not _same_recovered_page(FULL, final.replace('a1auto', 'another'), final.replace('a1auto', 'another'))
    assert not _same_recovered_page(FULL + '&price_from=10', final, final)
    assert not _same_recovered_page(FULL.replace('profile_search_show_all', 'closed'), final, final)
