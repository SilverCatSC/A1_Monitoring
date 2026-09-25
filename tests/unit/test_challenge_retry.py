import asyncio

from app.scraper.challenge_retry import explicit_captcha, refresh_explicit_captcha


class _Response:
    def __init__(self, status):
        self.status = status


class _Page:
    url = 'https://auto.ru/cars/'

    def __init__(self, html):
        self.html = html
        self.reloads = 0

    async def reload(self, **_kwargs):
        self.reloads += 1
        self.html = '<h1>Обычная выдача</h1>'
        return _Response(200)

    async def content(self):
        return self.html


def test_only_explicit_successful_captcha_is_refreshable():
    assert explicit_captcha('<h1>Подтвердите, что вы не робот</h1>', 'https://auto.ru/cars/', 200)
    assert not explicit_captcha('<h1>Подтвердите, что вы не робот</h1>', 'https://auto.ru/cars/', 429)
    assert not explicit_captcha('<h1>Доступ ограничен</h1>', 'https://auto.ru/cars/', 200)


def test_captcha_refreshes_once_and_returns_final_page(monkeypatch):
    async def no_wait(_seconds):
        pass

    monkeypatch.setattr('app.scraper.challenge_retry.asyncio.sleep', no_wait)
    page = _Page('<h1>Подтвердите, что вы не робот</h1>')
    events = []
    response, html, count = asyncio.run(refresh_explicit_captcha(
        page, _Response(200), page.html, progress=events.append,
        source='auto_ru', url=page.url,
    ))
    assert (response.status, html, count, page.reloads) == (
        200, '<h1>Обычная выдача</h1>', 1, 1,
    )
    assert events[0]['event'] == 'captcha_refresh'


def test_http_429_is_not_refreshed():
    page = _Page('<h1>Подтвердите, что вы не робот</h1>')
    response, _, count = asyncio.run(refresh_explicit_captcha(page, _Response(429), page.html))
    assert (response.status, count, page.reloads) == (429, 0, 0)
