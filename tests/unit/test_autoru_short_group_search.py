"""Short group searches must stop before empty/recommended later pages."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from playwright.async_api import async_playwright

from app.config import settings
from app.scraper.auto_ru import AutoRuAdapter, _preorder_only


def offer(number):
    return ('<div class="ListingItem"><a href="https://auto.ru/cars/new/group/mercedes/vle/1/2/'
            f'{1133334954 + number}-car/">Mercedes 2026</a><span>10 000 000 ₽</span></div>')


@pytest.mark.parametrize('count', [0, 1, 8, 20])
def test_group_heading_proves_end_without_opening_second_page(tmp_path, monkeypatch, count):
    monkeypatch.setattr(settings, 'scan_page_pause_min_seconds', 0)
    monkeypatch.setattr(settings, 'scan_page_pause_max_seconds', 0)
    monkeypatch.setattr(settings, 'auto_ru_page_delay_seconds', 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    documents = []
    heading = 'Одно предложение' if count == 1 else f'{count} предложений от 10 000 000 ₽'
    html = ('<h2>Похожие автомобили</h2><button>Показать 105 предложений</button>'
            f'<h2>{heading}</h2><div class="CardGroupOffersList__items">'
            + ''.join(offer(i) for i in range(count)) + '</div>')
    if count == 0:
        html = ('<div class="EmptyGroupResetFilters__title">Доступен предзаказ</div>'
                '<button>Дилеры с предзаказом</button>'
                '<div class="EmptyGroupRelated__title-AaaHb">Похожие на этот <!-- -->Mercedes-Benz</div>'
                '<div class="ListingCars__items">' + offer(0) + offer(1) + '</div>')

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            async def route(route):
                if route.request.resource_type == 'document':
                    documents.append(route.request.url)
                    await route.fulfill(content_type='text/html; charset=utf-8', body=html)
                else:
                    await route.abort()
            await page.route('**/*', route)
            @asynccontextmanager
            async def local_page(*args, **kwargs):
                yield page
            monkeypatch.setattr('app.scraper.auto_ru.browser_page', local_page)
            try:
                return await AutoRuAdapter().scan_filter('https://auto.ru/moskva/cars/new/group/mercedes/vle/1-2/', max_pages=5)
            finally:
                await browser.close()
    result = asyncio.run(exercise())
    assert len(documents) == 1
    assert result.complete and result.exhausted and result.error is None
    assert len(result.hits) == count


def test_other_city_cards_after_supplement_heading_are_excluded():
    html = offer(0) + '<h2>Ещё 4 в других городах</h2>' + offer(1)
    hits = AutoRuAdapter()._extract(html, 1)
    assert len(hits) == 1


def test_preorder_recommendations_outside_primary_root_are_not_vle_results():
    html = ('<h2>Доступен предзаказ</h2><button>Дилеры с предзаказом</button>'
            '<h2>Похожие на этот Mercedes-Benz</h2>'
            '<div class="ListingCars__items">' + offer(0) + offer(1) + '</div>')
    assert AutoRuAdapter()._extract(html, 1) == []
    assert _preorder_only(html, 0)
    assert not _preorder_only(html, 1)
    assert not _preorder_only(html.replace('Доступен предзаказ', 'О модели'), 0)


def test_primary_offers_before_recommendations_are_retained():
    html = ('<div class="CardGroupOffersList__items">' + offer(0) + '</div>'
            '<h2>Похожие на этот Mercedes-Benz</h2>'
            '<div class="ListingCars__items">' + offer(1) + offer(2) + '</div>')
    # The larger recommendation root must not displace a smaller primary root.
    assert len(AutoRuAdapter()._extract(html, 1)) == 1
