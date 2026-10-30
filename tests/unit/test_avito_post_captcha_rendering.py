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


def test_recovered_dealer_preview_reaches_show_all_before_demanding_advert_cards(monkeypatch):
    html = '''<div role="tablist"><button role="tab" aria-selected="true">Активные 30</button>
      <button role="tab">Завершённые 40</button></div>
      <a data-marker="item_list_with_filters/show_all_button"
         href="/brands/a1auto/items?s=profile_search_show_all">Показать все</a>'''
    url = 'https://www.avito.ru/brands/a1auto/all/avtomobili'
    reads = []
    async def content(page):
        reads.append(True)
        assert len(reads) <= 2, 'ready dealer preview must not wait indefinitely for search cards'
        return html
    async def no_wait(*args):
        pass
    monkeypatch.setattr('app.scraper.avito.read_page_content', content)
    monkeypatch.setattr('app.scraper.avito.asyncio.sleep', no_wait)
    page = SimpleNamespace(url=url, mouse=SimpleNamespace(wheel=no_wait))
    assert asyncio.run(AvitoAdapter()._wait_for_recovered_results(
        page, None, seller_catalogue=True, catalogue_boundary_url=url)) == html
    assert len(reads) == 2



@pytest.mark.parametrize('invalid', ['counter_only', 'wrong_brand', 'closed_inventory'])
def test_preview_shortcut_does_not_accept_counter_or_unapproved_inventory(monkeypatch, invalid):
    boundary = 'https://www.avito.ru/brands/a1auto/all/avtomobili'
    current_url = boundary + '?status=closed' if invalid == 'closed_inventory' else boundary
    href = '/brands/other/items?s=profile_search_show_all' if invalid == 'wrong_brand' else '/brands/a1auto/items?s=profile_search_show_all'
    html = '<div role="tablist"><button role="tab" aria-selected="true">Активные 30</button><button role="tab">Завершённые 40</button></div>'
    if invalid != 'counter_only':
        html += f'<a data-marker="item_list_with_filters/show_all_button" href="{href}">Показать все</a>'
    snapshots = iter([html, 'cards', 'cards'])
    reads = []
    async def content(page):
        value = next(snapshots)
        if value == 'cards':
            page.url = boundary
        reads.append(value)
        return value
    async def no_wait(*args):
        pass
    monkeypatch.setattr('app.scraper.avito.read_page_content', content)
    monkeypatch.setattr('app.scraper.avito.asyncio.sleep', no_wait)
    monkeypatch.setattr('app.scraper.avito.classify_result_page', lambda body, count: ('results' if count else 'unrecognized', None))
    monkeypatch.setattr(AvitoAdapter, '_extract', lambda self, body, page: [SimpleNamespace(external_id='1', url='https://www.avito.ru/moskva/avtomobili/car_1234567890')] if body == 'cards' else [])
    monkeypatch.setattr('app.scraper.avito.catalogue_html', lambda body: body)
    page = SimpleNamespace(url=current_url, mouse=SimpleNamespace(wheel=no_wait))
    assert asyncio.run(AvitoAdapter()._wait_for_recovered_results(page, None, seller_catalogue=True, catalogue_boundary_url=boundary)) == 'cards'
    assert len(reads) == 3
