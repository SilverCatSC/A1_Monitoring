import asyncio

import pytest
from playwright.async_api import async_playwright

from app.scraper.challenge_retry import (
    _same_recovered_page,
    operator_challenge,
    refresh_explicit_captcha,
)

URL = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz/v-klass?f=chosen&localPriority=0&radius=0&searchRadius=0'
TRANSITION = '<h1>Доступ ограничен: проблема с IP</h1><p>Проверка пройдена, перенаправление...</p>'
CATALOGUE = '<h1>Mercedes-Benz V-класс</h1><div data-marker="catalog-serp">Объявления</div>'


def test_solved_redirect_without_continue_button_remains_a_gate():
    assert operator_challenge(TRANSITION, URL, 'avito')
    assert not operator_challenge(TRANSITION, URL, 'auto_ru')


@pytest.mark.parametrize('changed', ['radius=1', 'f=other', 'searchRadius=1', 'localPriority=1'])
def test_history_normalization_never_changes_search_scope(changed):
    key = changed.split('=')[0]
    final = URL.replace(key + '=' + ('chosen' if key == 'f' else '0'), changed)
    assert not _same_recovered_page(URL, final, URL)


@pytest.mark.parametrize('priority', ['0', '1'])
def test_removed_priority_requires_original_document_proof(priority):
    requested = URL.replace('localPriority=0', f'localPriority={priority}')
    final = requested.replace(f'&localPriority={priority}', '')
    assert _same_recovered_page(requested, final, requested)
    assert not _same_recovered_page(requested, final, final)


@pytest.mark.parametrize('priority', ['0', '1'])
@pytest.mark.parametrize('changed', ['radius=1', 'f=other', 'searchRadius=1'])
def test_removed_priority_does_not_hide_changed_filter(priority, changed):
    requested = URL.replace('localPriority=0', f'localPriority={priority}')
    key = changed.split('=')[0]
    final = requested.replace(f'&localPriority={priority}', '').replace(
        key + '=' + ('chosen' if key == 'f' else '0'), changed)
    assert not _same_recovered_page(requested, final, requested)


@pytest.mark.parametrize('status,gate,reason', [
    (200, TRANSITION, 'redirect_pending'),
    (429, TRANSITION, 'redirect_pending'),
    (200, '<h1>Не получилось загрузить профиль</h1><button>Обновить</button>', 'profile_load_failed'),
])
@pytest.mark.parametrize('priority', ['0', '1'])
def test_delayed_avito_redirect_is_waited_without_reload_or_early_success(status, gate, reason, priority):
    events = []
    url = URL.replace('localPriority=0', f'localPriority={priority}')

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            requests = []
            required = asyncio.Event()

            async def route(route):
                requests.append(route.request.url)
                await route.fulfill(status=status if len(requests) == 1 else 200,
                                    content_type='text/html; charset=utf-8',
                                    body=gate if len(requests) == 1 else CATALOGUE)

            await page.route('**/*', route)
            response = await page.goto(url)
            html = await page.content()

            def progress(event):
                events.append(event)
                if event['event'] == 'captcha_operator_required':
                    required.set()

            async def delayed_redirect():
                await required.wait()
                await asyncio.sleep(0.45)
                assert not any(e['event'] == 'captcha_operator_resolved' for e in events)
                await page.goto(url)
                await page.evaluate("history.replaceState(null, '', location.href.replace('&localPriority=' + "
                                    + repr(priority) + ", ''))")

            task = asyncio.create_task(delayed_redirect())
            try:
                result = await asyncio.wait_for(refresh_explicit_captcha(
                    page, response, html, source='avito', url=url, wait_seconds=-1,
                    poll_seconds=0.1, progress=progress,
                ), timeout=5)
                await task
                assert requests == [url, url]
                assert result.outcome == 'resolved_by_operator'
                assert result.html == await page.content()
                assert result.response.status == 200
                assert result.refreshes == 0
                assert any(e.get('wait_reason') == reason for e in events)
            finally:
                task.cancel()
                await browser.close()

    asyncio.run(exercise())


def test_stalled_solved_redirect_prompts_once_without_reload_or_false_recovery(monkeypatch):
    monkeypatch.setattr('app.scraper.challenge_retry.REDIRECT_STALL_NOTICE_SECONDS', 0.1)
    events, requests = [], []

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()

            async def route(route):
                requests.append(route.request.url)
                await route.fulfill(status=429, content_type='text/html; charset=utf-8', body=TRANSITION)

            await page.route('**/*', route)
            response = await page.goto(URL)
            try:
                result = await refresh_explicit_captcha(
                    page, response, await page.content(), source='avito', url=URL,
                    wait_seconds=0.35, poll_seconds=0.03, progress=events.append)
                assert result.outcome == 'operator_timeout'
                assert result.response.status == 429
                assert requests == [URL]
                hints = [e for e in events if e['event'] == 'captcha_manual_refresh_hint']
                assert len(hints) == 1 and hints[0]['wait_reason'] == 'redirect_stalled'
                assert not any(e['event'] == 'captcha_operator_resolved' for e in events)
            finally:
                await browser.close()

    asyncio.run(exercise())


def test_http439_without_captcha_markup_waits_for_fresh_successful_document():
    events, requests = [], []

    async def exercise():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            page = await browser.new_page()
            required = asyncio.Event()

            async def route(route):
                requests.append(route.request.url)
                await route.fulfill(status=439 if len(requests) == 1 else 200,
                                    content_type='text/html', body='<h1>Restricted</h1>' if len(requests)==1 else CATALOGUE)

            await page.route('**/*', route)
            response = await page.goto(URL)

            def progress(event):
                events.append(event)
                if event['event'] == 'captcha_operator_required':
                    required.set()

            async def operator():
                await required.wait()
                await asyncio.sleep(0.15)
                assert requests == [URL]
                assert not any(e['event'] == 'captcha_operator_resolved' for e in events)
                await page.goto(URL)

            task = asyncio.create_task(operator())
            try:
                result = await asyncio.wait_for(refresh_explicit_captcha(
                    page, response, await page.content(), source='avito', url=URL,
                    wait_seconds=-1, poll_seconds=0.03, progress=progress), timeout=5)
                await task
                assert result.outcome == 'resolved_by_operator'
                assert result.response.status == 200 and result.refreshes == 0
                assert requests == [URL, URL]
            finally:
                task.cancel()
                await browser.close()

    asyncio.run(exercise())
