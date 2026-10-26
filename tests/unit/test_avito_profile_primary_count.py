import pytest

from app.scraper.challenge_retry import _same_recovered_page
from app.scraper.result_scope import avito_catalogue_result_count
from app.scraper.seller import seller_page_matches

SHORT='https://www.avito.ru/brands/a1auto/items?s=profile_search_show_all'
FULL='https://www.avito.ru/brands/a1auto/items/all/avtomobili?s=profile_search_show_all&sellerId=current'

def header(value):return '<div data-marker="item_list_with_filters/filters"><h5>Найдено '+str(value)+' объявления</h5></div>'

def test_own_primary_header_proves_count_without_counting_cards_or_sidebar():
 assert avito_catalogue_result_count(header(24),FULL)==24
 assert avito_catalogue_result_count(header(0),FULL)==0
 assert avito_catalogue_result_count('<h5>Найдено 24 объявления</h5>',FULL) is None
 assert avito_catalogue_result_count(header(24)+header(25),FULL) is None

@pytest.mark.parametrize('wrapper',['<aside>{}</aside>','<div hidden>{}</div>','<div style="display:none">{}</div>'])
def test_hidden_and_recommendation_counts_do_not_prove_inventory(wrapper):
 assert avito_catalogue_result_count(wrapper.format(header(24)),FULL) is None

@pytest.mark.parametrize('url',[FULL+'&price_from=1',FULL.replace('profile_search_show_all','closed'),FULL+'&p=2&foo=1'])
def test_filtered_or_closed_profile_does_not_prove_active_inventory(url):
 assert avito_catalogue_result_count(header(24),url) is None

def test_hydrated_canonical_profile_keeps_original_document_after_captcha():
 assert _same_recovered_page(SHORT,FULL,SHORT)
 assert seller_page_matches(SHORT,FULL)
 for bad in [FULL.replace('a1auto','another'),FULL.replace('profile_search_show_all','closed'),FULL+'&p=2',FULL+'&price_from=1']:
  assert not _same_recovered_page(SHORT,bad,SHORT)
 assert not seller_page_matches(SHORT,FULL.replace('a1auto','another'))

@pytest.mark.parametrize('declared,complete', [(24, True), (25, False)])
def test_direct_full_catalogue_uses_primary_header_and_requires_exact_coverage(tmp_path, monkeypatch, declared, complete):
    import asyncio
    from contextlib import asynccontextmanager

    from playwright.async_api import async_playwright
    from test_seller_active_counter import avito_card

    from app.config import settings
    from app.scraper.avito import AvitoAdapter
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'catalogue_lazy_load_enabled', False)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            async def route(request):
                if request.request.resource_type != 'document':
                    await request.abort()
                    return
                markup = '<meta charset="utf-8">' + header(declared) + ''.join(avito_card(1000000000 + i) for i in range(24))
                markup += '<script>history.replaceState({}, "", ' + repr(FULL) + ')</script>'
                await request.fulfill(content_type='text/html', body=markup)
            await page.route('**/*', route)
            @asynccontextmanager
            async def local_page(_playwright, source=None):
                yield page
            monkeypatch.setattr('app.scraper.avito.browser_page', local_page)
            try:
                return await AvitoAdapter().scan_filter(SHORT, 10, seller_catalogue=True)
            finally:
                await browser.close()
    result = asyncio.run(exercise())
    assert result.complete is complete and len(result.hits) == 24
    assert result.diagnostics['seller_primary_result_count'] == declared
