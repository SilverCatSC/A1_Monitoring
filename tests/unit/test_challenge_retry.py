import asyncio

import pytest

from app.scraper.challenge_retry import (
    _same_requested_page,
    explicit_captcha,
    operator_wait_seconds,
    refresh_explicit_captcha,
)


class _Response:
    def __init__(self, status):
        self.status = status


class _Page:
    url = 'https://auto.ru/cars/'

    def __init__(self, html):
        self.html = html
        self.reloads = 0
        self.front_calls = 0

    async def reload(self, **_kwargs):
        self.reloads += 1
        self.html = '<h1>Обычная выдача</h1>'
        return _Response(200)

    async def content(self):
        return self.html

    async def bring_to_front(self):
        self.front_calls += 1


def test_only_explicit_successful_captcha_is_refreshable():
    assert explicit_captcha('<h1>Подтвердите, что вы не робот</h1>', 'https://auto.ru/cars/', 200)
    assert explicit_captcha('<h1>Я не робот</h1>', 'https://auto.ru/showcaptcha?retpath=1', 200)
    assert not explicit_captcha('<h1>Подтвердите, что вы не робот</h1>', 'https://auto.ru/cars/', 429)
    assert not explicit_captcha('<h1>Доступ ограничен</h1>', 'https://auto.ru/cars/', 200)
    assert _same_requested_page(
        'https://auto.ru/diler/cars/all/a1_avto_moskva/',
        'https://auto.ru/diler/cars/items/all/a1_avto_moskva/',
    )
    assert not _same_requested_page(
        'https://auto.ru/cars/used/?page=2&output_type=list',
        'https://auto.ru/cars/used/?page=1&output_type=list',
    )
    assert _same_requested_page(
        'https://auto.ru/cars/used/?page=2&context=tracking',
        'https://auto.ru/cars/used/?page=2',
    )


def test_captcha_refreshes_once_and_returns_final_page(monkeypatch):
    async def no_wait(_seconds):
        pass

    monkeypatch.setattr('app.scraper.challenge_retry.asyncio.sleep', no_wait)
    page = _Page('<h1>Подтвердите, что вы не робот</h1>')
    events = []
    result = asyncio.run(refresh_explicit_captcha(
        page, _Response(200), page.html, progress=events.append,
        source='auto_ru', url=page.url,
    ))
    assert (result.response.status, result.html, result.refreshes, result.outcome, page.reloads) == (
        200, '<h1>Обычная выдача</h1>', 1, 'cleared_by_refresh', 1,
    )
    assert events[0]['event'] == 'captcha_refresh'


def test_http_429_is_not_refreshed():
    page = _Page('<h1>Подтвердите, что вы не робот</h1>')
    result = asyncio.run(refresh_explicit_captcha(page, _Response(429), page.html))
    assert (result.response.status, result.refreshes, result.outcome, page.reloads) == (429, 0, 'not_needed', 0)


def test_missing_document_response_is_not_accepted_as_a_checked_page():
    page = _Page('<h1>Обычная выдача</h1>')
    result = asyncio.run(refresh_explicit_captcha(page, None, page.html))
    assert result.outcome == 'no_document_response'
    assert result.refreshes == 0


def test_http_429_after_single_refresh_never_becomes_cleared_captcha(monkeypatch):
    async def no_wait(_seconds):
        pass

    monkeypatch.setattr('app.scraper.challenge_retry.asyncio.sleep', no_wait)

    class RateLimitedPage(_Page):
        async def reload(self, **_kwargs):
            self.reloads += 1
            self.html = '<h1>Доступ ограничен</h1>'
            return _Response(429)

    page = RateLimitedPage('<h1>Подтвердите, что вы не робот</h1>')
    result = asyncio.run(refresh_explicit_captcha(
        page, _Response(200), page.html, source='auto_ru', url=page.url,
    ))
    assert result.outcome == 'http_blocked'
    assert page.reloads == 1


def test_visible_auto_ru_is_only_operator_wait_path(monkeypatch):
    from app.scraper.challenge_retry import settings

    monkeypatch.setattr(settings, 'browser_cdp_url', 'http://127.0.0.1:19223')
    monkeypatch.setattr(settings, 'playwright_headless', False)
    monkeypatch.setattr(settings, 'captcha_operator_wait_seconds', 180)
    assert operator_wait_seconds('auto_ru') == 180
    assert operator_wait_seconds('avito') == 0
    monkeypatch.setattr(settings, 'playwright_headless', True)
    assert operator_wait_seconds('auto_ru') == 0


def test_operator_can_clear_captcha_on_same_requested_page(monkeypatch):
    async def no_wait(_seconds):
        pass

    monkeypatch.setattr('app.scraper.challenge_retry.asyncio.sleep', no_wait)

    class SolvedPage(_Page):
        async def reload(self, **_kwargs):
            self.reloads += 1
            return _Response(200)

        async def content(self):
            self.reads = getattr(self, 'reads', 0) + 1
            if self.reads >= 3:
                self.html = '<h1>Обычная выдача</h1>'
            return self.html

    page = SolvedPage('<h1>Подтвердите, что вы не робот</h1>')
    events = []
    result = asyncio.run(refresh_explicit_captcha(
        page, _Response(200), page.html, progress=events.append,
        source='auto_ru', url=page.url, wait_seconds=1,
    ))
    assert result.outcome == 'resolved_by_operator'
    assert result.refreshes == 1
    assert page.reloads == 1
    assert page.front_calls == 1
    assert [event['event'] for event in events] == [
        'captcha_refresh', 'captcha_operator_required', 'captcha_operator_resolved',
    ]


def test_unresolved_captcha_stays_incomplete_after_bounded_wait(monkeypatch):
    real_sleep = asyncio.sleep

    async def skip_initial_wait(seconds):
        if seconds >= 3:
            return
        await real_sleep(seconds)

    monkeypatch.setattr('app.scraper.challenge_retry.asyncio.sleep', skip_initial_wait)

    class PersistentPage(_Page):
        async def reload(self, **_kwargs):
            self.reloads += 1
            return _Response(200)

    page = PersistentPage('<h1>Подтвердите, что вы не робот</h1>')
    events = []
    result = asyncio.run(refresh_explicit_captcha(
        page, _Response(200), page.html, progress=events.append,
        source='auto_ru', url=page.url, wait_seconds=0.03, poll_seconds=0.01,
    ))
    assert result.outcome == 'operator_timeout'
    assert result.refreshes == 1
    assert events[-1]['event'] == 'captcha_operator_unresolved'


def test_wrong_destination_after_refresh_is_not_accepted(monkeypatch):
    async def no_wait(_seconds):
        pass

    monkeypatch.setattr('app.scraper.challenge_retry.asyncio.sleep', no_wait)

    class RedirectedPage(_Page):
        async def reload(self, **_kwargs):
            self.reloads += 1
            self.url = 'https://auto.ru/'
            self.html = '<h1>Главная страница</h1>'
            return _Response(200)

    page = RedirectedPage('<h1>Подтвердите, что вы не робот</h1>')
    result = asyncio.run(refresh_explicit_captcha(
        page, _Response(200), page.html, source='auto_ru', url='https://auto.ru/cars/',
    ))
    assert result.outcome == 'wrong_destination'


def test_operator_wait_requires_challenge_evidence_when_capture_is_configured(monkeypatch):
    async def no_wait(_seconds):
        pass

    async def no_evidence(_page, _response):
        return None

    monkeypatch.setattr('app.scraper.challenge_retry.asyncio.sleep', no_wait)

    class PersistentPage(_Page):
        async def reload(self, **_kwargs):
            self.reloads += 1
            return _Response(200)

    page = PersistentPage('<h1>Подтвердите, что вы не робот</h1>')
    result = asyncio.run(refresh_explicit_captcha(
        page, _Response(200), page.html, source='auto_ru', url=page.url,
        wait_seconds=180, capture_challenge=no_evidence,
    ))
    assert result.outcome == 'challenge_evidence_missing'
    assert page.front_calls == 0


def test_browser_error_during_operator_wait_clears_waiting_progress(monkeypatch):
    async def no_wait(_seconds):
        pass

    monkeypatch.setattr('app.scraper.challenge_retry.asyncio.sleep', no_wait)

    class BrokenPage(_Page):
        async def reload(self, **_kwargs):
            return _Response(200)

        async def content(self):
            self.reads = getattr(self, 'reads', 0) + 1
            if self.reads > 1:
                raise RuntimeError('page closed')
            return self.html

    page = BrokenPage('<h1>Подтвердите, что вы не робот</h1>')
    events = []
    with pytest.raises(RuntimeError, match='page closed'):
        asyncio.run(refresh_explicit_captcha(
            page, _Response(200), page.html, progress=events.append,
            source='auto_ru', url=page.url, wait_seconds=1,
        ))
    assert [event['event'] for event in events][-2:] == [
        'captcha_operator_required', 'captcha_operator_unresolved',
    ]
