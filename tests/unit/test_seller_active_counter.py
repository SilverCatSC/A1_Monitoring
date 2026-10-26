"""Only the seller's own active-ads counter may prove the catalogue end."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from playwright.async_api import async_playwright

from app.config import settings
from app.scraper.avito import AvitoAdapter
from app.scraper.result_scope import seller_catalogue_counters

SELLER_URL = 'https://www.avito.ru/brands/a1auto/all/avtomobili'


def avito_card(key):
    return (
        f'<div data-marker="item" data-item-id="{key}">'
        f'<a data-marker="item-title" href="https://www.avito.ru/moskva/avtomobili/mercedes_v-klass_{key}">'
        f'Mercedes-Benz V-класс</a><span>9 900 000 ₽</span></div>'
    )


def tab_bar(active, completed):
    tabs = ''
    if active is not None:
        tabs += f'<div class="tabs-tab tabs-tab-active"><span>Активные</span><sup>{active}</sup></div>'
    if completed is not None:
        tabs += f'<div class="tabs-tab"><span>Завершённые</span><sup>{completed}</sup></div>'
    return f'<div class="tabs">{tabs}</div>' if tabs else ''


def test_profile_tab_counters_are_recognised():
    assert seller_catalogue_counters(tab_bar(8, 40)) == {'active': 8, 'completed': 40}


def test_completed_tab_alone_is_recognised_but_proves_nothing():
    assert seller_catalogue_counters(tab_bar(None, 40)) == {'active': None, 'completed': 40}


def test_global_search_total_favourites_and_recommendations_are_invisible():
    html = (
        '<span data-marker="page-title/count">130</span>'
        '<div class="profile-favourite">В избранном у 120 человек</div>'
        '<section data-marker="item-recommend"><h2>Похожие 25</h2></section>'
        '<aside><div class="tabs-tab">Активные 64</div></aside>'
    )
    assert seller_catalogue_counters(html) is None


def test_hidden_duplicate_counter_is_ignored():
    html = ('<div hidden>Активные 40</div>'
            '<template><div class="tabs-tab">Активные 12</div></template>'
            + tab_bar(8, 40))
    assert seller_catalogue_counters(html) == {'active': 8, 'completed': 40}


def test_disagreeing_visible_active_counters_fail_closed():
    html = tab_bar(8, 40) + '<div class="tabs-tab"><span>Активные</span><sup>12</sup></div>'
    assert seller_catalogue_counters(html) is None


@pytest.mark.parametrize('active,completed,card_count,proven', [
    (8, 40, 8, True),    # счётчик совпал с уникальными ID → конец доказан
    (8, 40, 7, False),   # собрано меньше счётчика → неполон
    (40, 8, 8, False),   # счётчик чужой/полного профиля при фильтрованном виде → неполон
    (None, 40, 8, False),  # только «Завершённые» → не доказывает
    (None, None, 8, False),  # счётчика нет → incomplete
])
def test_seller_counter_proves_end_only_on_exact_unique_id_match(
    tmp_path, monkeypatch, active, completed, card_count, proven
):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))

    def markup():
        return (
            '<div data-marker="catalog-serp">'
            + ''.join(avito_card(1000000000 + index) for index in range(card_count))
            + '</div>'
            + tab_bar(active, completed)
        )

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()

            async def route_page(route):
                await route.fulfill(content_type='text/html; charset=utf-8', body=markup())

            await page.route('**/*', route_page)

            @asynccontextmanager
            async def local_page(_playwright, source=None):
                yield page

            monkeypatch.setattr('app.scraper.avito.browser_page', local_page)
            try:
                return await AvitoAdapter().scan_filter(SELLER_URL, 5, seller_catalogue=True)
            finally:
                await browser.close()

    result = asyncio.run(exercise())
    assert len(result.hits) == card_count
    assert result.exhausted is proven
    assert result.complete is proven
    if proven:
        assert result.diagnostics['catalogue_end_proven_by'] == 'seller_active_counter'
        assert result.diagnostics['seller_active_counter'] == active
        assert result.diagnostics['seller_completed_counter'] == completed
    else:
        assert result.diagnostics['catalogue_navigation_unproven'] is True
        assert result.error == 'Catalogue end not proven; no observed next-page link'


def test_hidden_duplicate_counter_does_not_block_the_real_one(tmp_path, monkeypatch):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    html = ('<div hidden>Активные 40</div>'
            '<div data-marker="catalog-serp">'
            + ''.join(avito_card(1000000000 + index) for index in range(8))
            + '</div>'
            + tab_bar(8, 40))

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()

            async def route_page(route):
                await route.fulfill(content_type='text/html; charset=utf-8', body=html)

            await page.route('**/*', route_page)

            @asynccontextmanager
            async def local_page(_playwright, source=None):
                yield page

            monkeypatch.setattr('app.scraper.avito.browser_page', local_page)
            try:
                return await AvitoAdapter().scan_filter(SELLER_URL, 5, seller_catalogue=True)
            finally:
                await browser.close()

    result = asyncio.run(exercise())
    assert result.complete and result.exhausted
    assert len(result.hits) == 8
    assert result.diagnostics['catalogue_end_proven_by'] == 'seller_active_counter'
    assert result.diagnostics['seller_active_counter'] == 8


def test_arbitrary_active_label_is_not_a_profile_tab():
    assert seller_catalogue_counters('<div>Активные 8</div>') is None


def test_completed_selected_or_unselected_active_does_not_prove_end():
    html = tab_bar(8, 40).replace('tabs-tab-active', 'tabs-tab-unselected')
    assert seller_catalogue_counters(html)['active'] is None


def test_inline_hidden_counter_is_not_visible():
    html = '<div style="display: none"><div class="tabs-tab">Активные 12</div></div>' + tab_bar(8, 40)
    assert seller_catalogue_counters(html)['active'] == 8
    assert seller_catalogue_counters('<div style="visibility: hidden">' + tab_bar(8, 40) + '</div>') is None


@pytest.mark.parametrize('url', [
    SELLER_URL + '?q=v-class', SELLER_URL + '?f=encoded-filter',
    SELLER_URL + '?s=profile_search',
    'https://www.avito.ru/moskva/avtomobili/mercedes-benz/v-klass-ASg',
])
def test_filtered_view_cannot_prove_full_profile(url):
    assert seller_catalogue_counters(tab_bar(8, 40), url) is None
