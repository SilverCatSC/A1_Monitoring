"""Acceptance checks for false-empty and hidden-counter gaps in the GLM candidate."""
import pytest

from app.scraper.auto_ru import _declared_offer_count
from app.scraper.base import classify_result_page


@pytest.mark.parametrize('widget', [
    '<div hidden class="search-no-results">Nothing</div>',
    '<aside class="items-not-found">Saved items</aside>',
    '<script>const classes=["search-no-results"]</script>',
    '<style>.search-no-results {color:red}</style>',
    '<div data-marker="recommendations">ничего не найдено</div>',
    '<h2>Рекомендуем</h2><div>ничего не найдено</div>',
])
def test_supplement_empty_does_not_prove_primary_empty(widget):
    html = '<div data-marker="catalog-serp">Unknown new layout</div>' + widget
    assert classify_result_page(html, 0)[0] == 'unrecognized'


def test_visible_challenge_in_aside_still_blocks_cards():
    html = '<main>Catalogue</main><aside>Подтвердите, что вы не робот</aside>'
    assert classify_result_page(html, 1)[0] == 'blocked'


def test_hidden_cloudflare_component_does_not_block_results():
    assert classify_result_page('<main>Catalogue</main><div hidden class="cf-chl-widget"></div>', 1)[0] == 'results'


def test_genuine_empty_in_primary_root_is_supported():
    html = '<div data-marker="catalog-serp"><div class="search-no-results">Ничего не найдено</div></div>'
    assert classify_result_page(html, 0)[0] == 'empty'


def test_one_empty_root_does_not_prove_other_unknown_root_empty():
    html = ('<div data-marker="catalog-serp">ничего не найдено</div>'
            '<div data-marker="catalog-serp">Unknown new layout</div>')
    assert classify_result_page(html, 0)[0] == 'unrecognized'


def test_recommendation_inside_primary_root_is_not_empty():
    html = ('<div data-marker="catalog-serp">Unknown new layout'
            '<h2>Похожие автомобили</h2><div class="search-no-results">ничего не найдено</div></div>')
    assert classify_result_page(html, 0)[0] == 'unrecognized'


def test_hidden_active_radius_does_not_override_visible_count():
    html = ('<div hidden><div class="ListingGeoRadiusCounters__item_active">'
            '<span class="ListingGeoRadiusCounters__itemCount">9 предложений</span></div></div>'
            '<button>Показать 26 предложений</button>')
    assert _declared_offer_count(html) == 26


def test_recommendation_count_does_not_override_primary_filter():
    html = ('<div class="Recommendations"><button>Показать 9 предложений</button></div>'
            '<button>Показать 26 предложений</button>')
    assert _declared_offer_count(html) == 26


@pytest.mark.parametrize('source', ['auto_ru', 'avito'])
@pytest.mark.parametrize('outcome,status,expected', [
    ('no_document_response', None, 'technical_error'),
    ('challenge_evidence_missing', 200, 'technical_error'),
    ('wrong_destination', 200, 'technical_error'),
    ('http_blocked', 500, 'technical_error'),
    ('unresolved', 200, 'blocked'),
    ('http_blocked', 429, 'blocked'),
])
def test_lazy_recovery_failure_preserves_denial_boundary(tmp_path, monkeypatch, source, outcome, status, expected):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from playwright.async_api import async_playwright
    from test_lazy_load import card

    from app.config import settings
    from app.scraper.auto_ru import AutoRuAdapter
    from app.scraper.avito import AvitoAdapter
    from app.scraper.challenge_retry import CaptchaRecovery

    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds',
                'auto_ru_page_delay_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    root = 'class="ListingCars__items"' if source == 'auto_ru' else 'data-marker="catalog-serp"'
    html = (f'<div {root}>{card(source, 1)}'
            '<button>Показать ещё</button></div>')

    async def recovery(_page, _response, markup, **_kwargs):
        return CaptchaRecovery(None if status is None else SimpleNamespace(status=status), markup, 0, outcome)

    monkeypatch.setattr('app.scraper.lazy_load.refresh_explicit_captcha', recovery)

    async def exercise():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()

            async def fulfill(route):
                await route.fulfill(content_type='text/html; charset=utf-8', body=html)

            await page.route('**/*', fulfill)

            @asynccontextmanager
            async def local_page(_playwright, source=None):
                yield page

            monkeypatch.setattr(f'app.scraper.{source}.browser_page', local_page)
            adapter = AutoRuAdapter if source == 'auto_ru' else AvitoAdapter
            url = 'https://auto.ru/moskva/cars/hongqi/hq9/new/' if source == 'auto_ru' else 'https://www.avito.ru/moskva/avtomobili'
            try:
                return await adapter().scan_filter(url, 3)
            finally:
                await browser.close()

    result = asyncio.run(exercise())
    assert not result.complete
    assert result.diagnostics['page_1_state'] == expected
    assert result.error
