import asyncio
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import async_playwright
from test_lazy_load import card

from app.config import settings
from app.scraper.avito import AvitoAdapter
from app.scraper.navigation_recovery import wait_for_navigation_recovery
from app.service.scan_progress import ScanProgressTracker


@pytest.mark.parametrize('failure', ['timeout', 'closed_context', 'connect_failure'])
def test_avito_resumes_same_page_and_keeps_previous_hits(tmp_path, monkeypatch, failure):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds',
                'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    monkeypatch.setattr('app.scraper.avito.operator_wait_seconds', lambda source: -1)
    events, documents, gotos = [], [], []

    async def exercise():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel='chrome', headless=True)
            pages = []
            faulted = False
            connect_failed = False
            required = asyncio.Event()

            async def route_page(route):
                number = int(parse_qs(urlsplit(route.request.url).query).get('p', ['1'])[0])
                documents.append(number)
                await route.fulfill(content_type='text/html', body=(
                    f'<div data-marker="catalog-serp">{card("avito", number)}</div>'
                    f'<div data-marker="pagination-button"><span class="item_current-x" '
                    f'data-marker="pagination-button/page({number})">{number}</span>'
                    f'<a data-marker="pagination-button/nextPage" href="?p={number+1}">Далее</a></div>'
                ))

            class PageProxy:
                def __init__(self, page):
                    self.page = page

                def __getattr__(self, name):
                    return getattr(self.page, name)

                async def goto(self, url, **kwargs):
                    nonlocal faulted
                    number = int(parse_qs(urlsplit(url).query).get('p', ['1'])[0])
                    gotos.append(number)
                    if number == 2 and not faulted and failure != 'connect_failure':
                        faulted = True
                        if failure == 'closed_context':
                            await self.page.context.close()
                            raise RuntimeError('Target page, context or browser has been closed')
                        raise RuntimeError('Page.goto: Timeout 25000ms exceeded')
                    return await self.page.goto(url, **kwargs)

            @asynccontextmanager
            async def local_page(_playwright, source=None):
                nonlocal connect_failed
                if failure == 'connect_failure' and not connect_failed:
                    connect_failed = True
                    raise RuntimeError('BrowserType.connect_over_cdp: connect ECONNREFUSED')
                page = await browser.new_page()
                await page.route('**/*', route_page)
                pages.append(page)
                yield PageProxy(page)

            monkeypatch.setattr('app.scraper.avito.browser_page', local_page)

            async def recovery(*args, **kwargs):
                return await wait_for_navigation_recovery(*args, **kwargs, poll_seconds=0.01)

            monkeypatch.setattr('app.scraper.avito.wait_for_navigation_recovery', recovery)

            def progress(event):
                events.append(event)
                if event['event'] == 'navigation_operator_required':
                    required.set()

            async def operator():
                await required.wait()
                await asyncio.sleep(0.1)
                if failure == 'timeout':
                    assert documents == [1]  # no automatic reloads while waiting
                    await pages[0].goto('https://www.avito.ru/moskva/avtomobili?p=2')

            task = asyncio.create_task(operator())
            adapter = AvitoAdapter()
            adapter.progress_callback = progress
            try:
                result = await asyncio.wait_for(adapter.scan_filter(
                    'https://www.avito.ru/moskva/avtomobili', max_pages=3), timeout=10)
                await task
                return result
            finally:
                task.cancel()
                await browser.close()

    result = asyncio.run(exercise())
    assert documents == [1, 2, 3]
    assert gotos == ([1, 2, 2, 3] if failure == 'closed_context' else [1, 2, 3])
    assert result.complete and result.error is None
    assert result.page_count == 3
    assert [hit.page_number for hit in result.hits] == [1, 2, 3]
    assert result.diagnostics['load_actions'] == 3
    assert not any(event['event'] == 'page_failed' for event in events)
    assert any(event['event'] == 'navigation_operator_resolved' for event in events)


def test_navigation_wait_is_visible_without_finishing_filter(tmp_path):
    tracker = ScanProgressTracker(str(tmp_path))
    tracker({'event': 'cycle_started', 'total_filters': 16})
    tracker({'event': 'filter_finished', 'overall_index': 9})
    tracker({'event': 'navigation_operator_required', 'source': 'avito', 'page': 2})
    tracker({'event': 'navigation_operator_required', 'source': 'avito', 'page': 2})
    tracker({'event': 'navigation_operator_waiting', 'source': 'avito', 'page': 2})
    assert tracker.state['status'] == 'waiting_navigation'
    assert tracker.state['completed_filters'] == 9
    assert tracker.state['current']['page'] == 2
    tracker({'event': 'navigation_operator_resolved', 'source': 'avito', 'page': 2})
    assert tracker.state['status'] == 'running'


def test_recovery_rejects_xhr_wrong_scope_and_denied_document():
    from types import SimpleNamespace

    async def exercise():
        callbacks = {}
        url = 'https://www.avito.ru/moskva/avtomobili?radius=0'
        frame = object()

        class Page:
            main_frame = frame

            def is_closed(self):
                return False

            def on(self, name, callback):
                callbacks[name] = callback

            def remove_listener(self, name, callback):
                callbacks.pop(name, None)

            async def content(self):
                return '<h1>Объявления</h1>'

        page = Page()
        page.url = url

        async def reopen():
            raise AssertionError('Open page must not be recreated')

        task = asyncio.create_task(wait_for_navigation_recovery(
            page, url=url, reopen=reopen, progress=None, page_number=2,
            request_timeout=1, poll_seconds=0.01))
        try:
            await asyncio.sleep(0.03)
            for navigation, status, response_url in (
                (False, 200, url), (True, 429, url), (True, 200, url.replace('radius=0', 'radius=1')),
            ):
                callbacks['response'](SimpleNamespace(status=status, url=response_url,
                    request=SimpleNamespace(frame=frame, is_navigation_request=lambda navigation=navigation: navigation)))
                await asyncio.sleep(0.03)
                assert not task.done()
            callbacks['response'](SimpleNamespace(status=200, url=url,
                request=SimpleNamespace(frame=frame, is_navigation_request=lambda: True)))
            recovered, response = await asyncio.wait_for(task, timeout=1)
            assert recovered is page and response.status == 200
            assert not callbacks
        finally:
            task.cancel()

    asyncio.run(exercise())
