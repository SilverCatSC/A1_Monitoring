import asyncio
from types import SimpleNamespace

import pytest

from app.scraper.avito import AvitoAdapter


@pytest.mark.parametrize('terminal', ['results', 'empty', 'blocked'])
def test_redirect_waits_through_loading_and_stabilizes_cards_without_navigation(monkeypatch, terminal):
    # More than thirty polling intervals: a rendering timer must not skip this page.
    snapshots = iter(['loading'] * 35 + (
        ['one', 'two', 'two'] if terminal == 'results' else [terminal]
    ))
    reads = []
    scrolls = []

    async def content(_page):
        html = next(snapshots)
        reads.append(html)
        return html

    async def wheel(*args):
        scrolls.append(args)

    async def no_wait(_seconds):
        pass

    monkeypatch.setattr('app.scraper.avito.read_page_content', content)
    monkeypatch.setattr('app.scraper.avito.asyncio.sleep', no_wait)
    monkeypatch.setattr('app.scraper.avito.classify_result_page', lambda html, count: (
        html if html in {'empty', 'blocked'} else 'results' if count else 'unrecognized', None
    ))
    monkeypatch.setattr(AvitoAdapter, '_extract', lambda self, html, page: [
        SimpleNamespace(external_id=str(i), url=f'https://www.avito.ru/{i}')
        for i in range({'one': 1, 'two': 2}.get(html, 0))
    ])
    page = SimpleNamespace(mouse=SimpleNamespace(wheel=wheel))
    result = asyncio.run(AvitoAdapter()._wait_for_recovered_results(page, 2))
    assert result == ('two' if terminal == 'results' else terminal)
    assert len(reads) == (38 if terminal == 'results' else 36)
    assert scrolls == [(0, 1600)]
