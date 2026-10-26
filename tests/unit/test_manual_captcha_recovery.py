"""Manual assistance with real browser navigation responses; all requests are routed locally."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from playwright.async_api import async_playwright

from app.config import settings
from app.scraper.avito import AvitoAdapter
from app.scraper.challenge_retry import refresh_explicit_captcha

GATE = '<h1>Доступ ограничен: проблема с IP</h1><button>Продолжить</button>'
URL = 'https://www.avito.ru/brands/a1auto/all/avtomobili'


@pytest.mark.parametrize('recovery', ['navigation', 'xhr_only', 'wrong_destination', 'still_denied', 'hidden_solved_widget'])
def test_avito_429_manual_wait_requires_target_document_http_200(monkeypatch, recovery):
    events = []

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            requests = []
            operator_requested = asyncio.Event()

            async def route_page(route):
                requests.append(route.request.url)
                first = len(requests) == 1
                await route.fulfill(
                    status=429 if first or recovery == 'still_denied' else 200,
                    content_type='text/html; charset=utf-8', body=GATE if first else ('<h1>Каталог</h1>' + (f'<div style="display:none">{GATE}</div>' if recovery == 'hidden_solved_widget' else '')),
                )

            await page.route('**/*', route_page)
            response = await page.goto(URL)
            html = await page.content()

            def progress(event):
                events.append(event)
                if event['event'] == 'captcha_operator_required':
                    operator_requested.set()

            async def person():
                await operator_requested.wait()
                if recovery == 'xhr_only':
                    await page.evaluate("async () => { await fetch('/asset'); document.body.innerHTML = '<h1>Каталог</h1>'; }")
                else:
                    await page.goto('https://www.avito.ru/' if recovery == 'wrong_destination' else URL)

            task = asyncio.create_task(person())
            try:
                result = await asyncio.wait_for(refresh_explicit_captcha(
                    page, response, html, progress=progress, source='avito', url=URL,
                    wait_seconds=-1 if recovery == 'navigation' else 0.4, poll_seconds=0.1,
                ), timeout=5)
                await task
                assert len(requests) == 2  # Only the initial request and the simulated person's action.
                return result
            finally:
                task.cancel()
                await browser.close()

    result = asyncio.run(exercise())
    assert result.refreshes == 0
    if recovery in {'navigation', 'hidden_solved_widget'}:
        assert result.outcome == 'resolved_by_operator'
        assert result.response.status == 200
        if recovery == 'navigation':
            assert events[0]['wait_seconds'] == -1
    else:
        assert result.outcome in {'operator_timeout', 'wrong_destination'}
        assert events[-1]['event'] == 'captcha_operator_unresolved'
        if recovery in {'xhr_only', 'still_denied'}:
            assert result.response.status == 429


@pytest.mark.parametrize('repeated_gate', [False, True])
def test_avito_scan_continues_after_manual_gate_in_same_page(tmp_path, monkeypatch, repeated_gate):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    monkeypatch.setattr('app.scraper.avito.operator_wait_seconds', lambda _source: -1)
    visits = []
    events = []

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            operator_requested = asyncio.Event()

            async def route_page(route):
                visits.append(route.request.url)
                if len(visits) == 1 or (repeated_gate and len(visits) == 3):
                    status, html = 429, GATE
                else:
                    status, html = 200, (
                        '<span data-marker="page-title/count">1</span><div data-marker="catalog-serp">'
                        '<div data-marker="item" data-item-id="1000000001">'
                        '<a data-marker="item-title" href="https://www.avito.ru/moskva/avtomobili/car_1000000001">'
                        'Машина</a></div></div>'
                    )
                await route.fulfill(status=status, content_type='text/html; charset=utf-8', body=html)

            await page.route('**/*', route_page)

            @asynccontextmanager
            async def local_page(_playwright, source=None):
                yield page

            monkeypatch.setattr('app.scraper.avito.browser_page', local_page)
            if repeated_gate:
                original_wait = AvitoAdapter._wait_for_recovered_results
                injected = False

                async def wait_with_new_gate(adapter, current_page, number):
                    nonlocal injected
                    if not injected:
                        injected = True
                        await current_page.goto(URL)
                        return await current_page.content()
                    return await original_wait(adapter, current_page, number)

                monkeypatch.setattr(AvitoAdapter, '_wait_for_recovered_results', wait_with_new_gate)

            def progress(event):
                events.append(event)
                if event['event'] == 'captcha_operator_required':
                    operator_requested.set()

            async def person():
                for _ in range(2 if repeated_gate else 1):
                    await operator_requested.wait()
                    operator_requested.clear()
                    await page.goto(URL)

            task = asyncio.create_task(person())
            try:
                result = await asyncio.wait_for(
                    AvitoAdapter(progress_callback=progress).scan_filter(URL, max_pages=3), timeout=10,
                )
                await task
                return result
            finally:
                task.cancel()
                await browser.close()

    result = asyncio.run(exercise())
    assert result.complete and result.exhausted and result.error is None
    assert result.page_count == 1 and len(result.hits) == 1
    assert visits == [URL] * (4 if repeated_gate else 2)
    assert result.diagnostics['page_1_http_status'] == 200
    assert result.diagnostics['page_1_captcha_outcome'] == 'resolved_by_operator'
    assert result.diagnostics['page_1_captcha_refreshes'] == 0
    assert result.diagnostics['page_1_captcha_evidence']
    assert any(event['event'] == 'captcha_operator_resolved' for event in events)


def test_avito_manual_gate_can_return_clean_brand_url(monkeypatch):
    requested = URL + '?src=search_seller_info&iid=8047929828&sellerId=e4036231f69cc5cfc365db2f79600230'
    events = []

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            operator_requested = asyncio.Event()
            visits = []

            async def route_page(route):
                visits.append(route.request.url)
                await route.fulfill(
                    status=429 if len(visits) == 1 else 200,
                    content_type='text/html; charset=utf-8',
                    body=GATE if len(visits) == 1 else '<h1>A1 Авто</h1>',
                )

            await page.route('**/*', route_page)
            response = await page.goto(requested)
            html = await page.content()

            def progress(event):
                events.append(event)
                if event['event'] == 'captcha_operator_required':
                    operator_requested.set()

            async def person():
                await operator_requested.wait()
                await page.goto(URL)

            task = asyncio.create_task(person())
            try:
                result = await asyncio.wait_for(refresh_explicit_captcha(
                    page, response, html, source='avito', url=requested,
                    wait_seconds=-1, poll_seconds=0.1, progress=progress,
                ), timeout=5)
                await task
                assert visits == [requested, URL]
                return result
            finally:
                task.cancel()
                await browser.close()

    result = asyncio.run(exercise())
    assert result.outcome == 'resolved_by_operator'
    assert result.response.status == 200
    assert result.refreshes == 0
    assert events[-1]['event'] == 'captcha_operator_resolved'
