import asyncio

from playwright.async_api import async_playwright

from app.models import EngineType
from app.scraper.geography import confirmed_control_text, url_matches_moscow, verify_geography


def test_visible_control_requires_explicit_zero_radius():
    assert confirmed_control_text('Москва + 0 км')
    assert not confirmed_control_text('Москва')
    assert not confirmed_control_text('Москва + 200 км')
    assert not confirmed_control_text('Москва 0 км, 200 км')
    assert not confirmed_control_text('Московская область 0 км')


def test_geography_rejects_duplicate_values_and_wrong_host():
    url = 'https://auto.ru/moskva/cars/?geo_radius=0&rid=213'
    assert url_matches_moscow(EngineType.AUTO_RU, url)
    assert not url_matches_moscow(EngineType.AUTO_RU, url + '&geo_radius=200')
    assert not url_matches_moscow(EngineType.AUTO_RU, url.replace('auto.ru', 'auto.ru.example.org'))
    assert not url_matches_moscow(EngineType.AUTO_RU, url.replace('/moskva/', '/all/'))


def test_avito_requires_all_geography_parameters():
    url = 'https://www.avito.ru/moskva/avtomobili?localPriority=0&radius=0&searchRadius=0'
    assert url_matches_moscow(EngineType.AVITO, url)
    assert not url_matches_moscow(EngineType.AVITO, url.replace('searchRadius=0', 'searchRadius=200'))


def test_auto_radius_is_applied_then_read_again(monkeypatch):
    async def evidence(*args, **kwargs):
        return 'geography-proof.png'
    monkeypatch.setattr('app.scraper.base.capture_page_evidence', evidence)

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            try:
                page = await browser.new_page()
                html = '''<button onclick="document.querySelector('section').hidden=false">Москва</button>
                  <section data-testid="header-geo-content" hidden>
                    <button value="213">Москва</button><div role="slider" aria-valuenow="200"></div>
                    <button onclick="document.querySelector('[role=slider]').setAttribute('aria-valuenow','0')">0</button>
                    <button onclick="document.querySelector('section').hidden=true; history.replaceState({},'', '?geo_radius=0&rid=213')">Сохранить</button>
                  </section>'''
                await page.route('**/*', lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=html))
                await page.goto('https://auto.ru/moskva/cars/?geo_radius=200&rid=213')
                result = await verify_geography(page, EngineType.AUTO_RU)
                assert result['state'] == 'verified'
                assert result['corrected'] is True
                assert result['radius_before'] == '200'
                assert result['evidence'] == 'geography-proof.png'
            finally:
                await browser.close()
    asyncio.run(exercise())
