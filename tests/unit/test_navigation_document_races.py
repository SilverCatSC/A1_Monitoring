"""Avito must process a document that arrived before recovery was entered."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from test_autoru_navigation_recovery import FakeTab
from test_lazy_load import card

from app.config import settings
from app.scraper.avito import AvitoAdapter
from app.scraper.navigation_recovery import wait_for_navigation_recovery


def test_avito_pre_arrived_document_resumes_same_page_without_second_goto(tmp_path, monkeypatch):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'avito_page_delay_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    monkeypatch.setattr(settings, 'catalogue_lazy_load_enabled', False)
    monkeypatch.setattr('app.scraper.avito.operator_wait_seconds', lambda source: -1)
    events = []
    tab = FakeTab('', card('avito', 1))
    wheel_calls = 0

    async def wheel(*args):
        nonlocal wheel_calls
        wheel_calls += 1
        if wheel_calls == 1:
            tab.deliver_request(tab.navigation_request())
            tab.html = card('avito', 3)
            tab.deliver(tab.document())
            raise RuntimeError('Execution context was destroyed, most likely because of a navigation')

    async def count():
        return 1

    tab.mouse = SimpleNamespace(wheel=wheel)
    tab.locator = lambda *args: SimpleNamespace(count=count)

    @asynccontextmanager
    async def local_playwright():
        yield object()

    @asynccontextmanager
    async def local_page(playwright, source=None):
        assert source == 'avito'
        yield tab

    async def quick_recovery(*args, **kwargs):
        return await wait_for_navigation_recovery(*args, **kwargs, poll_seconds=0.01)

    monkeypatch.setattr('app.scraper.avito.async_playwright', local_playwright)
    monkeypatch.setattr('app.scraper.avito.browser_page', local_page)
    monkeypatch.setattr('app.scraper.avito.wait_for_navigation_recovery', quick_recovery)

    async def exercise():
        return await asyncio.wait_for(AvitoAdapter(progress_callback=events.append).scan_filter(
            'https://www.avito.ru/moskva/avtomobili', max_pages=1), timeout=3)

    result = asyncio.run(exercise())
    assert result.complete and result.error is None and result.page_count == 1
    assert [hit.url for hit in result.hits] == ['https://www.avito.ru/moskva/avtomobili/car_1000000003']
    assert len(tab.gotos) == 1
    assert result.diagnostics['load_actions'] == 1
    assert any(event['event'] == 'navigation_operator_resolved' for event in events)
    assert all(event['source'] == 'avito' for event in events)
    assert not any(tab.listeners.values())
