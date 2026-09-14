import asyncio
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import async_playwright

from app.config import settings
from app.models import EngineType
from app.scraper.auto_ru import AutoRuAdapter, _all_offers_are_visible
from app.scraper.avito import AvitoAdapter
from app.scraper.base import ListingHit, capture_listing_card_evidence
from app.scraper.result_scope import pagination_state
from app.scraper.seller import direct_page_status


def avito_card(key, city='moskva'):
    return f'<div data-marker="item" data-item-id="{key}"><a data-marker="item-title" href="https://www.avito.ru/{city}/avtomobili/car_{key}">Машина</a><span>1 000 000 ₽</span></div>'


def test_avito_excludes_other_city_and_supplements_preserving_visible_slot():
    html = ('<div data-marker="catalog-serp">' + avito_card('1000000001', 'krasnogorsk')
            + avito_card('1000000002') + '<h2>1 объявление есть в другом городе</h2>'
            + avito_card('1000000003') + '</div>')
    hits = AvitoAdapter()._extract(html, 2, moscow_only=True)
    assert len(hits) == 1
    assert hits[0].position == 2 and hits[0].page_number == 2
    assert '1000000002' in hits[0].url


def test_auto_ru_counts_only_primary_group_not_sidebar_or_recommendations():
    def card(key):
        return f'<div class="ListingItemUniversal-abc"><a href="https://auto.ru/cars/used/sale/mercedes/vle/{key}-x/">VLE</a></div>'
    html = '<aside>' + card('1000000001') + '</aside><div class="CardGroupOffersList__items">'
    html += card('1000000002') + '</div><h2>Похожие автомобили</h2>' + card('1000000003')
    hits = AutoRuAdapter()._extract(html, 1)
    assert len(hits) == 1 and '1000000002' in hits[0].url
    assert not _all_offers_are_visible(6, 9)


@pytest.mark.parametrize('source,html,expected', [
    ('avito', '<span data-marker="page-title/count">130</span><ul data-marker="pagination-button"><span class="item_current-abc" data-marker="pagination-button/page(2)">2</span><a data-marker="pagination-button/page(3)" href="?p=3">3</a><a data-marker="pagination-button/nextPage" href="?p=3"></a></ul>', (2, 130, False)),
    ('avito', '<ul data-marker="pagination-button"><a data-marker="pagination-button/page(1)" href="/">1</a><span class="item_current-abc" data-marker="pagination-button/page(2)">2</span></ul>', (2, None, True)),
    ('auto_ru', '<div data-seo="listing-pagination"><a class="ListingPagination__page Button_checked">1</a><a class="ListingPagination__page" href="?page=2">2</a><a class="ListingPagination__next" href="?page=2">Следующая</a></div>', (1, None, False)),
])
def test_observed_pagination_markers(source, html, expected):
    state = pagination_state(html, source)
    assert (state['current'], state['total'], state['last']) == expected


@pytest.mark.parametrize('html,expected', [
    ('<h1>Mercedes-Benz V-Класс</h1><div><span>Этот автомобиль уже продан</span><p>Объявление доступно только по прямой ссылке</p></div>', 'removed'),
    ('<h1>Mercedes-Benz V-Класс</h1><div>Объявление снято с публикации.</div>', 'removed'),
    ('<h1>Mercedes-Benz V-Класс</h1><div itemprop="description">Объявление снято с публикации.</div>', 'unknown'),
    ('<h1>Mercedes-Benz V-Класс</h1><h2>Похожие объявления</h2><div>Этот автомобиль уже продан</div>', 'unknown'),
])
def test_visible_status_banners_are_not_confused_with_descriptions(html, expected):
    url = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1130128385-x/'
    assert direct_page_status(html, EngineType.AUTO_RU, url, url, 200)['state'] == expected


@pytest.mark.parametrize('scenario', ['short', 'repeat', 'wrong_page'])
def test_browser_traversal_stops_at_real_end_or_rejects_duplicate_pages(tmp_path, monkeypatch, scenario):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    requests = []
    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            async def route_page(route):
                number = int(parse_qs(urlsplit(route.request.url).query).get('p', ['1'])[0])
                requests.append(number)
                html = '<div data-marker="catalog-serp">' + avito_card('1000000001') + '</div>'
                if scenario == 'short':
                    html = '<span data-marker="page-title/count">1</span>' + html
                elif scenario == 'wrong_page':
                    html += '<ul data-marker="pagination-button"><span data-marker="pagination-button/page(1)" class="item_current-test">1</span><a data-marker="pagination-button/page(2)" href="?p=2">2</a><a data-marker="pagination-button/nextPage" href="?p=2"></a></ul>'
                await route.fulfill(content_type='text/html', body=html)
            await page.route('**/*', route_page)
            @asynccontextmanager
            async def local_page(_playwright):
                yield page
            monkeypatch.setattr('app.scraper.avito.browser_page', local_page)
            try:
                return await AvitoAdapter().scan_filter('https://www.avito.ru/moskva/avtomobili/', 3)
            finally:
                await browser.close()
    result = asyncio.run(exercise())
    assert len(result.hits) == 1
    if scenario == 'short':
        assert result.complete and result.exhausted and requests == [1]
    else:
        assert not result.complete and requests == [1, 2]
        assert 'pagination' in result.error


def test_expected_hit_without_exact_card_evidence_fails_closed(tmp_path, monkeypatch):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))

    async def no_card_evidence(*_args, **_kwargs):
        return None

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()

            async def route_page(route):
                html = (
                    '<span data-marker="page-title/count">1</span>'
                    '<div data-marker="catalog-serp">'
                    + avito_card('1000000001')
                    + '</div>'
                )
                await route.fulfill(content_type='text/html', body=html)

            await page.route('**/*', route_page)

            @asynccontextmanager
            async def local_page(_playwright):
                yield page

            monkeypatch.setattr('app.scraper.avito.browser_page', local_page)
            monkeypatch.setattr('app.scraper.avito.capture_listing_card_evidence', no_card_evidence)
            try:
                return await AvitoAdapter().scan_filter(
                    'https://www.avito.ru/moskva/avtomobili/',
                    max_pages=1,
                    target_keys={'avito:1000000001'},
                )
            finally:
                await browser.close()

    result = asyncio.run(exercise())
    assert result.complete is False
    assert result.hits == []
    assert result.diagnostics['page_1_state'] == 'evidence_missing'
    assert 'evidence capture failed' in result.error


def test_screenshot_ignores_hidden_copy_and_captures_visible_card(tmp_path):
    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            url = 'https://auto.ru/cars/used/sale/mercedes/vle/1000000001-x/'
            await page.set_content(f'<div class="ListingItem" style="display:none"><a href="{url}">Скрытый дубль</a></div><div class="ListingItem" style="padding:20px"><a href="{url}">VLE</a><p>23 850 000 ₽</p></div>')
            hit = ListingHit(external_id='1000000001', title='VLE', url=url, page_number=1, position=1, price=None, raw={})
            try:
                path = await capture_listing_card_evidence(page, source=EngineType.AUTO_RU,
                    search_url='https://auto.ru/moskva/cars/all/', page_number=1, hit=hit, evidence_dir=str(tmp_path))
                assert path and (tmp_path / path).stat().st_size > 100
                assert 'card_evidence_error' not in hit.raw
            finally:
                await browser.close()
    asyncio.run(exercise())
