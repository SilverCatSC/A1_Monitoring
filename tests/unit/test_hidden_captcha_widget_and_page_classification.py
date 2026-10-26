"""Hidden solved widgets and sidebar notes must not re-block or empty a visible result page."""
import asyncio

import pytest
from playwright.async_api import async_playwright

from app.scraper.base import classify_result_page
from app.scraper.challenge_retry import refresh_explicit_captcha

URL = 'https://www.avito.ru/brands/a1auto/all/avtomobili'
HIDDEN_GATE = '<div style="display:none"><h1>Подтвердите, что вы не робот</h1></div>'
VISIBLE_CATALOGUE = (
    '<span data-marker="page-title/count">1</span>'
    '<div data-marker="catalog-serp"><div data-marker="item" data-item-id="1000000001">'
    '<a data-marker="item-title" href="https://www.avito.ru/moskva/avtomobili/car_1000000001">'
    'Машина</a></div></div>'
)


def test_hidden_challenge_markup_does_not_reblock_visible_results():
    state, reason = classify_result_page(VISIBLE_CATALOGUE + HIDDEN_GATE, 1)
    assert state == 'results' and reason is None


def test_hidden_empty_marker_does_not_create_an_empty_result():
    html = ('<div data-marker="catalog-serp"><div data-marker="item" data-item-id="1"></div></div>'
            '<div hidden>ничего не найдено</div>')
    assert classify_result_page(html, 0)[0] == 'unrecognized'


def test_sidebar_favourites_empty_note_is_not_an_empty_result():
    html = ('<div data-marker="catalog-serp"></div>'
            '<aside data-marker="favourites-sidebar">В избранном ничего не найдено</aside>')
    assert classify_result_page(html, 0)[0] == 'unrecognized'


def test_genuine_visible_empty_page_stays_empty():
    assert classify_result_page('<main><h1>По вашему запросу ничего не найдено</h1></main>', 0) == (
        'empty', 'ничего не найдено'
    )


def test_visible_challenge_still_blocks_even_with_cards_present():
    state, reason = classify_result_page(
        '<div data-marker="catalog-serp"><h1>Подтвердите, что вы не робот</h1></div>', 1,
    )
    assert state == 'blocked' and reason == 'подтвердите, что вы не робот'


def test_structural_class_markers_still_classify_from_markup():
    assert classify_result_page('<div class="search-no-results"></div>', 0)[0] == 'empty'
    assert classify_result_page('<div class="cf-chl-bypass"></div>', 0)[0] == 'blocked'


@pytest.mark.parametrize('wait_seconds,expected_outcome,expected_refreshes', [
    (-1, 'not_needed', 0),  # manual wait enabled: no legacy reload for a hidden widget
    (0, 'cleared_by_refresh', 1),  # wait disabled: the legacy one-refresh path is kept
])
def test_hidden_solved_widget_with_manual_wait_does_not_reload(monkeypatch, wait_seconds,
                                                               expected_outcome, expected_refreshes):
    real_sleep = asyncio.sleep

    async def skip_legacy_refresh(seconds):
        if seconds >= 3:
            return
        await real_sleep(seconds)

    monkeypatch.setattr('app.scraper.challenge_retry.asyncio.sleep', skip_legacy_refresh)
    documents = []

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()

            async def route_page(route):
                if route.request.resource_type == 'document':
                    documents.append(route.request.url)
                    # The first document still carries the solved hidden widget;
                    # a manual refresh receives the clean catalogue without it.
                    body = (VISIBLE_CATALOGUE + HIDDEN_GATE) if len(documents) == 1 else VISIBLE_CATALOGUE
                    await route.fulfill(status=200, content_type='text/html; charset=utf-8', body=body)
                else:
                    await route.fulfill(status=200, content_type='text/plain', body='')

            await page.route('**/*', route_page)
            response = await page.goto(URL)
            html = await page.content()
            try:
                return await asyncio.wait_for(
                    refresh_explicit_captcha(
                        page, response, html, source='avito', url=URL,
                        wait_seconds=wait_seconds, poll_seconds=0.1,
                    ),
                    timeout=5,
                )
            finally:
                await browser.close()

    result = asyncio.run(exercise())
    assert result.outcome == expected_outcome
    assert result.refreshes == expected_refreshes
    assert len(documents) == 1 + expected_refreshes


def test_manual_wait_with_hidden_widget_keeps_downstream_classification_consistent(monkeypatch):
    """After the not_needed recovery the same raw HTML must classify as results, not blocked."""
    documents = []

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()

            async def route_page(route):
                documents.append(route.request.url)
                await route.fulfill(status=200, content_type='text/html; charset=utf-8',
                                    body=VISIBLE_CATALOGUE + HIDDEN_GATE)

            await page.route('**/*', route_page)
            response = await page.goto(URL)
            html = await page.content()
            try:
                recovery = await refresh_explicit_captcha(
                    page, response, html, source='avito', url=URL, wait_seconds=1,
                    poll_seconds=0.1,
                )
                return recovery
            finally:
                await browser.close()

    recovery = asyncio.run(exercise())
    assert recovery.outcome == 'not_needed'
    assert documents == [URL]  # no reload, no extra navigation
    assert classify_result_page(recovery.html, 1)[0] == 'results'
