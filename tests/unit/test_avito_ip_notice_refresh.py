"""Offline IP-notice recovery; no real browser or marketplace requests."""
import asyncio
from types import SimpleNamespace

import pytest

from app.scraper.challenge_retry import avito_ip_restriction, refresh_explicit_captcha, settings

URL = 'https://www.avito.ru/brands/a1auto/all/avtomobili'
NOTICE = '<h1>Доступ ограничен: проблема с IP</h1><button>Продолжить</button>'
RESULTS = '<h1>А1 АВТО</h1><div data-marker="catalog-serp">Объявления</div>'


@pytest.fixture
def clock(monkeypatch):
    current = SimpleNamespace(now=0.0)
    real_sleep = asyncio.sleep

    async def sleep(seconds):
        current.now += seconds
        await real_sleep(0)

    monkeypatch.setattr('app.scraper.challenge_retry.asyncio.sleep', sleep)
    monkeypatch.setattr('app.scraper.challenge_retry.asyncio.get_running_loop',
                        lambda: SimpleNamespace(time=lambda: current.now))
    monkeypatch.setattr(settings, 'avito_ip_refresh_attempts', 2)
    monkeypatch.setattr(settings, 'avito_ip_refresh_delay_seconds', 3)
    return current


class Page:
    def __init__(self, clock, *, clear_after=None, human_at=None, timeout_first=False, redirect=False):
        self.clock = clock
        self.url = URL
        self.html = NOTICE
        self.main_frame = object()
        self.listeners = []
        self.reload_times = []
        self.clear_after = clear_after
        self.human_at = human_at
        self.timeout_first = timeout_first
        self.redirect = redirect

    def response(self, status):
        request = SimpleNamespace(is_navigation_request=lambda: True, frame=self.main_frame)
        return SimpleNamespace(status=status, url=self.url, request=request)

    def on(self, event, callback):
        assert event == 'response'
        self.listeners.append(callback)

    def remove_listener(self, event, callback):
        self.listeners.remove(callback)

    async def bring_to_front(self):
        pass

    async def content(self):
        if self.human_at is not None and self.clock.now >= self.human_at and self.html == NOTICE:
            self.html = RESULTS
            for callback in self.listeners:
                callback(self.response(200))
        return self.html

    async def reload(self, **kwargs):
        assert kwargs['wait_until'] == 'domcontentloaded'
        self.reload_times.append(self.clock.now)
        if self.timeout_first and len(self.reload_times) == 1:
            raise TimeoutError('document still loading')
        if self.clear_after == len(self.reload_times):
            self.html = RESULTS
            if self.redirect:
                self.url = 'https://www.avito.ru/'
        response = self.response(200 if self.html == RESULTS else 429)
        for callback in self.listeners:
            callback(response)
        return response


def recover(page, *, wait=-1, events=None):
    return asyncio.run(refresh_explicit_captcha(page, page.response(429), page.html,
        source='avito', url=URL, wait_seconds=wait, poll_seconds=1,
        progress=events.append if events is not None else None))


@pytest.mark.parametrize('clear_after', [1, 2])
def test_delayed_refresh_recovers_only_same_page_http_200(clock, clear_after):
    page = Page(clock, clear_after=clear_after)
    result = recover(page)
    assert result.outcome == 'cleared_by_refresh'
    assert result.response.status == 200
    assert result.refreshes == clear_after
    assert page.reload_times[0] >= 3
    if clear_after == 2:
        assert page.reload_times[1] - page.reload_times[0] >= 3
    assert page.url == URL
    assert not page.listeners


def test_two_failed_refreshes_keep_waiting_for_manual_recovery_without_skip(clock):
    events = []
    page = Page(clock, human_at=40)
    result = recover(page, events=events)
    assert result.outcome == 'resolved_by_operator'
    assert len(page.reload_times) == 2
    assert clock.now >= 40
    assert any(e.get('wait_reason') == 'ip_refresh_exhausted' for e in events)
    assert result.html == RESULTS


def test_reload_timeout_preserves_wait_and_bounded_retry(clock):
    page = Page(clock, clear_after=2, timeout_first=True)
    result = recover(page)
    assert result.outcome == 'cleared_by_refresh'
    assert result.refreshes == 2
    assert result.response.status == 200


def test_wrong_destination_is_not_accepted_or_reloaded_again(clock):
    page = Page(clock, clear_after=1, redirect=True)
    result = recover(page, wait=15)
    assert result.outcome == 'wrong_destination'
    assert len(page.reload_times) == 1


@pytest.mark.parametrize('html', [
    '<h1>Доступ ограничен: проблема с IP</h1><p>Проверка пройдена, перенаправление...</p>',
    '<h1>Доступ ограничен: проблема с IP</h1><iframe src="/captcha"></iframe>',
    '<h1>Доступ ограничен: проблема с IP</h1><p>Подтвердите, что вы не робот</p>',
    '<h1>А1 АВТО</h1><p>В описании: доступ ограничен по IP</p>',
    '<h1 hidden>Доступ ограничен: проблема с IP</h1><h1>Каталог</h1>',
    '<h1>Доступ ограничен</h1>',
])
def test_only_explicit_ip_notice_is_refreshable(html):
    assert not avito_ip_restriction(html, 'avito')


def test_ip_notice_not_refreshed_with_wait_disabled(clock):
    page = Page(clock, clear_after=1)
    result = recover(page, wait=0)
    assert result.outcome == 'not_needed'
    assert not page.reload_times


def test_ip_refreshes_can_be_disabled_without_ending_manual_wait(clock, monkeypatch):
    monkeypatch.setattr(settings, 'avito_ip_refresh_attempts', 0)
    page = Page(clock, human_at=10)
    assert recover(page).outcome == 'resolved_by_operator'
    assert not page.reload_times


def test_human_recovery_during_pause_cancels_automatic_refresh(clock):
    page = Page(clock, human_at=2)
    assert recover(page).outcome == 'resolved_by_operator'
    assert not page.reload_times


def test_missing_document_and_subresource_200_do_not_confirm_recovery(clock):
    class NoDocumentPage(Page):
        async def reload(self, **kwargs):
            self.reload_times.append(self.clock.now)
            self.html = RESULTS
            asset = self.response(200)
            asset.request.is_navigation_request = lambda: False
            for callback in self.listeners:
                callback(asset)
            return None

    page = NoDocumentPage(clock)
    result = recover(page, wait=15)
    assert result.outcome == 'operator_timeout'
    assert result.response is None
    assert len(page.reload_times) == 1


def test_active_puzzle_appearing_during_pause_is_not_reloaded(clock):
    class PuzzlePage(Page):
        async def content(self):
            if 2 <= self.clock.now < 10:
                self.html = NOTICE + '<iframe src="/captcha"></iframe>'
            if self.clock.now >= 10:
                self.html = RESULTS
                for callback in self.listeners:
                    callback(self.response(200))
            return self.html

    page = PuzzlePage(clock)
    assert recover(page).outcome == 'resolved_by_operator'
    assert not page.reload_times
