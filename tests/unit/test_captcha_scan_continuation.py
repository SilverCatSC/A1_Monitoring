import asyncio
from contextlib import asynccontextmanager

from app.scraper.auto_ru import AutoRuAdapter
from app.scraper.base import ListingHit


class _Response:
    status = 200


class _Playwright:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Page:
    def __init__(self, *, solve: bool):
        self.solve = solve
        self.url = 'about:blank'
        self.requested = ''
        self.visited = []
        self.reads_on_challenge = 0
        self.front_calls = 0
        self.reloads = 0

    async def goto(self, url, **_kwargs):
        self.visited.append(url)
        self.requested = url
        self.url = 'https://auto.ru/showcaptcha' if 'page=2' in url else url
        return _Response()

    async def evaluate(self, _script):
        return None

    async def reload(self, **_kwargs):
        self.reloads += 1
        return _Response()

    async def bring_to_front(self):
        self.front_calls += 1

    async def content(self):
        if self.url.endswith('/showcaptcha'):
            self.reads_on_challenge += 1
            if self.solve and self.reads_on_challenge >= 3:
                self.url = self.requested
            else:
                return '<h1>Подтвердите, что вы не робот</h1>'
        page = 2 if 'page=2' in self.url else 3 if 'page=3' in self.url else 1
        return f'<h1>RESULT {page}</h1>'


def _run_adapter(monkeypatch, *, solve: bool, wait_seconds: float):
    page = _Page(solve=solve)

    @asynccontextmanager
    async def page_context(_playwright):
        yield page

    async def no_wait(_seconds):
        pass

    async def evidence(*_args, **_kwargs):
        return 'page-evidence.png'

    monkeypatch.setattr('app.scraper.auto_ru.async_playwright', lambda: _Playwright())
    monkeypatch.setattr('app.scraper.auto_ru.browser_page', page_context)
    monkeypatch.setattr('app.scraper.auto_ru.capture_page_evidence', evidence)
    monkeypatch.setattr('app.scraper.auto_ru.operator_wait_seconds', lambda _source: wait_seconds)
    monkeypatch.setattr('app.scraper.auto_ru.choose_pause', lambda *_args: 0)
    monkeypatch.setattr('app.scraper.auto_ru.asyncio.sleep', no_wait)
    monkeypatch.setattr('app.scraper.auto_ru.pagination_state', lambda html, _source: {
        'current': int(html.split('RESULT ')[1].split('<')[0]),
        'last': 'RESULT 3' in html,
        'has_next': 'RESULT 3' not in html,
    })
    monkeypatch.setattr('app.scraper.auto_ru.classify_result_page', lambda _html, count: (
        'results' if count else 'unrecognized', None,
    ))
    monkeypatch.setattr(AutoRuAdapter, '_extract', lambda self, html, page_number: [
        ListingHit(external_id=str(page_number), title='Car',
                   url=f'https://auto.ru/cars/used/sale/test/{page_number}/',
                   page_number=page_number, position=1, price=None, raw={})
    ] if 'RESULT' in html else [])

    result = asyncio.run(AutoRuAdapter().scan_filter(
        'https://auto.ru/cars/used/?page=1', max_pages=3,
    ))
    return result, page


def test_captcha_on_page_two_continues_page_two_then_three_in_same_context(monkeypatch):
    result, page = _run_adapter(monkeypatch, solve=True, wait_seconds=1)
    assert result.complete is True
    assert result.page_count == 3
    assert [hit.page_number for hit in result.hits] == [1, 2, 3]
    assert len(page.visited) == 3
    assert page.reloads == 1
    assert page.front_calls == 1
    assert result.diagnostics['page_2_captcha_outcome'] == 'resolved_by_operator'
    assert result.diagnostics['page_2_captcha_evidence'] == 'page-evidence.png'


def test_unresolved_page_two_stops_without_claiming_complete_filter(monkeypatch):
    result, page = _run_adapter(monkeypatch, solve=False, wait_seconds=0)
    assert result.complete is False
    assert result.page_count == 1
    assert [hit.page_number for hit in result.hits] == [1]
    assert len(page.visited) == 2
    assert result.diagnostics['page_2_state'] == 'blocked'
    assert result.diagnostics['page_2_captcha_outcome'] == 'unresolved'
