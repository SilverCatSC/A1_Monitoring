import asyncio
import hashlib

import pytest
from playwright.async_api import async_playwright

from app.models import EngineType
from app.scraper.seller import _direct_card_fields, expand_direct_description, wait_for_direct_card_content

ID = 'MBVC011220262508260030'
TEXT = ('Особенности конкретного автомобиля. ' * 150).rstrip() + '\nID: ' + ID


@pytest.mark.parametrize('source,container', [
    (EngineType.AUTO_RU, '<div class="CardDescription__textInner">{}</div>'),
    (EngineType.AVITO, '<div data-marker="item-view/item-description">{}</div>'),
])
def test_entire_description_and_late_identity_survive_extraction(source, container):
    html = '<h1>Car 2026</h1>' + container.format(TEXT)
    fields = _direct_card_fields(html, source)
    assert len(fields['description']) > 5000
    assert fields['description'] == TEXT
    assert fields['description_excerpt'] == TEXT
    assert fields['placement_id'] == ID
    assert fields['description_capture']['software_truncated'] is False
    assert fields['description_capture']['sha256'] == hashlib.sha256(TEXT.encode()).hexdigest()


def test_main_description_precedes_seo_and_recommendation_identity():
    html = ('<meta itemprop="description" content="Короткое SEO-описание">'
            '<div class="CardBenefits__item-description">Не описание машины</div>'
            '<h1>Car 2026</h1><div class="CardDescription">'
            '<h4 class="CardDescription__title">Комментарий продавца</h4>'
            '<div class="CardDescription__textInner">Полное описание без идентификатора.</div>'
            '<button class="CardDescription__cutLink">Читать дальше</button></div>'
            f'<aside><div class="CardDescription__textInner">ID: {ID}</div></aside>')
    fields = _direct_card_fields(html, EngineType.AUTO_RU)
    assert fields['description'] == 'Полное описание без идентификатора.'
    assert fields['placement_id'] is None


def test_tail_changes_prevent_false_readiness_after_first_1800_characters():
    prefix = 'Сохранённые характеристики ' * 100

    def document(tail):
        return ('<h1>Car 2026</h1><span itemprop="price">100</span>'
                f'<div itemprop="description">{prefix}{tail}</div>')

    class Page:
        url = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1234567890-ab/'
        documents = iter([document('Новый хвост'), document('Новый хвост')])
        reads = 0

        async def content(self):
            self.reads += 1
            return next(self.documents)

    page = Page()
    html, readiness = asyncio.run(wait_for_direct_card_content(
        page, EngineType.AUTO_RU, page.url, 200, document('Старый хвост'), poll_seconds=0.01))
    assert readiness['state'] == 'ready'
    assert page.reads == 2
    assert _direct_card_fields(html, EngineType.AUTO_RU)['description'].endswith('Новый хвост')


def test_expand_reads_lazy_tail_only_inside_current_advert_without_network():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            requests = []
            page.on('request', lambda request: requests.append(request.url))
            await page.set_content('''<aside><div class="CardDescription">
              <button class="CardDescription__cutLink" onclick="window.wrongClicked=true">Читать дальше</button>
              </div></aside><div class="CardDescription">
              <div class="CardDescription__textInner">Начало</div>
              <button id="own" class="CardDescription__cutLink">Читать дальше</button></div>''')
            await page.locator('#own').evaluate(
                '(button, full) => button.onclick = () => { '
                'document.querySelector(".CardDescription__textInner").textContent = full; button.remove(); }',
                TEXT)
            html, outcome = await expand_direct_description(page, await page.content())
            assert outcome == {'state': 'expanded', 'clicks': 1}
            assert _direct_card_fields(html, EngineType.AUTO_RU)['description'] == TEXT
            assert not await page.evaluate('Boolean(window.wrongClicked)')
            assert requests == []
            _, outcome = await expand_direct_description(page, html)
            assert outcome == {'state': 'not_needed', 'clicks': 0}
            await browser.close()
    asyncio.run(run())


@pytest.mark.parametrize('signal', ['label', 'aria', 'text'])
def test_visible_description_toggle_does_not_freeze_after_successful_expansion(signal):
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            requests = []
            page.on('request', lambda request: requests.append(request.url))
            try:
                await page.set_content('''<div class="CardDescription">
                  <div class="CardDescription__textInner">Начало</div>
                  <button id="own" class="CardDescription__cutLink">Читать дальше</button></div>''')
                await page.locator('#own').evaluate('''(button, {full, signal}) => {
                    button.onclick = () => setTimeout(() => {
                        document.querySelector('.CardDescription__textInner').textContent = full;
                        if (signal === 'label') button.textContent = 'Свернуть';
                        if (signal === 'aria') button.setAttribute('aria-expanded', 'true');
                    }, 50);
                }''', {'full': TEXT, 'signal': signal})
                # Test deadline detects a hang; production has no timeout/skip.
                html, outcome = await asyncio.wait_for(
                    expand_direct_description(page, await page.content()), timeout=5)
                assert await page.locator('#own').is_visible()
                assert outcome == {'state': 'expanded', 'clicks': 1}
                assert _direct_card_fields(html, EngineType.AUTO_RU)['description'] == TEXT
                assert _direct_card_fields(html, EngineType.AUTO_RU)['placement_id'] == ID
                assert requests == []
            finally:
                await browser.close()
    asyncio.run(run())
