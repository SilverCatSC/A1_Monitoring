"""An XHR/DOM-only recovery of an HTTP 429 gate is never accepted as a document."""
import asyncio

import pytest
from playwright.async_api import async_playwright

from app.scraper.challenge_retry import refresh_explicit_captcha
from app.service.scan_progress import ScanProgressTracker

GATE = '<h1>Доступ ограничен: проблема с IP</h1><button>Продолжить</button>'
URL = 'https://www.avito.ru/brands/a1auto/all/avtomobili'


@pytest.mark.parametrize('grace,expected_reason', [(0.2, 'manual_refresh_required'), (15, 'redirect_pending')])
def test_xhr_only_dom_recovery_keeps_waiting_and_instructs_manual_refresh(monkeypatch, grace, expected_reason):
    """The person's action renders the catalogue in place; the program must not take it as HTTP 200."""
    events = []
    monkeypatch.setattr('app.scraper.challenge_retry.settings.captcha_redirect_grace_seconds', grace)
    documents, xhrs = [], []

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            operator_requested = asyncio.Event()

            async def route_page(route):
                if route.request.resource_type == 'document':
                    documents.append(route.request.url)
                    await route.fulfill(status=429, content_type='text/html; charset=utf-8', body=GATE)
                else:
                    xhrs.append(route.request.url)
                    await route.fulfill(status=200, content_type='text/html; charset=utf-8',
                                        body='<h1>Каталог</h1>')

            await page.route('**/*', route_page)
            response = await page.goto(URL)
            html = await page.content()

            def progress(event):
                events.append(event)
                if event['event'] == 'captcha_operator_required':
                    operator_requested.set()

            async def person():
                # Human action represented without a main navigation: the gate
                # button issues an XHR and the DOM is rebuilt in place.
                await operator_requested.wait()
                await page.evaluate(
                    "async () => { await fetch('/asset'); "
                    "document.body.innerHTML = '<h1>Каталог продавца</h1>'; }"
                )

            task = asyncio.create_task(person())
            try:
                return await asyncio.wait_for(
                    refresh_explicit_captcha(
                        page, response, html, progress=progress, source='avito', url=URL,
                        wait_seconds=0.6, poll_seconds=0.1,
                    ),
                    timeout=5,
                )
            finally:
                task.cancel()
                await browser.close()

    result = asyncio.run(exercise())
    # No fresh document was fabricated, no automatic refresh happened and the
    # arbitrary XHR 200 response never replaced the denied document.
    assert documents == [URL]
    assert xhrs == ['https://www.avito.ru/asset']
    assert result.outcome == 'operator_timeout'
    assert result.response.status == 429
    assert result.response.url == URL

    waiting = [event for event in events if event['event'] == 'captcha_operator_waiting']
    assert waiting, 'the heartbeat state must be published'
    assert waiting[-1]['wait_reason'] == expected_reason
    assert waiting[-1]['http_status'] == 429
    assert waiting[-1]['final_url'] == URL

    hints = [event for event in events if event['event'] == 'captcha_manual_refresh_hint']
    if expected_reason == 'manual_refresh_required':
        assert hints and 'Cmd+R' in hints[-1]['instruction']
        assert hints[-1]['final_url'] == URL
    else:
        assert not hints, 'Do not interrupt a pending automatic redirect with a refresh hint'
    assert events[-1]['event'] == 'captcha_operator_unresolved'


def test_manual_refresh_hint_is_visible_in_terminal_and_progress(tmp_path, capsys):
    tracker = ScanProgressTracker(str(tmp_path))
    tracker({'event': 'captcha_operator_required', 'source': 'avito', 'url': URL, 'wait_seconds': -1})
    tracker({'event': 'captcha_operator_waiting', 'source': 'avito', 'url': URL, 'final_url': URL,
              'http_status': 429, 'wait_reason': 'manual_refresh_required'})
    tracker({'event': 'captcha_manual_refresh_hint', 'source': 'avito', 'url': URL,
             'final_url': URL, 'http_status': 429,
             'instruction': 'Если проверка пройдена и каталог виден — обновите ЭТУ ЖЕ вкладку (Cmd+R)'})
    payload_text = capsys.readouterr().out
    assert 'обновите ЭТУ ЖЕ вкладку (Cmd+R)' in payload_text
    assert payload_text.count('Cmd+R') >= 2  # both the waiting reason and the hint line
    assert tracker.state['current']['event'] == 'captcha_manual_refresh_hint'
    assert tracker.state['status'] == 'waiting_captcha'
