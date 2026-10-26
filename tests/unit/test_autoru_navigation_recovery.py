"""Auto.ru navigation recovery over fake pages: stable documents, labels, cleanup.

The live 'Execution context was destroyed' failure is not reproducible here;
these tests cover the recoverable goto/scroll/read boundary with scripted
pages and responses only, without a browser, network or live scans.
"""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_lazy_load import card

from app.config import settings
from app.scraper.auto_ru import AutoRuAdapter
from app.scraper.navigation_recovery import (
    wait_for_navigation_recovery,
    watch_main_frame_documents,
)

EXECUTION_CONTEXT_DESTROYED = (
    'Page.evaluate: Execution context was destroyed, most likely because of a navigation'
)
SEARCH_URL = 'https://auto.ru/cars/hongqi/hq9/new/'
CHALLENGE_HTML = '<h1>Подтвердите, что вы не робот</h1>'


def catalogue(number):
    return f'<div class="ListingCars__items">{card("auto_ru", number)}</div>'


class FakeResponse:
    def __init__(self, url, status, *, frame, navigation=True):
        self.url = url
        self.status = status
        self.request = SimpleNamespace(
            frame=frame,
            is_navigation_request=lambda: navigation,
        )


class FakeTab:
    """Scripted marketplace tab: goto, scroll, reads, listeners and evidence.

    ``goto`` emits main-frame request/response events like a real page, so a
    watcher attached before it sees the document. A scripted scroll/read fault
    first emits the main-frame request of the racing navigation (optionally
    its already-arrived document via ``racing_document``) and only then raises.
    """

    def __init__(self, url='', html=''):
        self.url = url
        self.html = html
        self.closed = False
        self.main_frame = object()
        self.listeners = {}
        self.pending_request = None
        self.reads = 0
        self.gotos = []
        self.scroll_faults = 0
        self.close_after_scroll_fault = False
        self.read_error = None
        self.goto_error = None
        self.racing_document = None
        self.screenshots = []

    def is_closed(self):
        return self.closed

    def on(self, name, callback):
        self.listeners.setdefault(name, []).append(callback)

    def remove_listener(self, name, callback):
        callbacks = self.listeners.get(name, [])
        if callback in callbacks:
            callbacks.remove(callback)

    def deliver(self, response):
        for callback in list(self.listeners.get('response', ())):
            callback(response)

    def deliver_request(self, request):
        if request.is_navigation_request() and request.frame == self.main_frame:
            self.pending_request = request
        for callback in list(self.listeners.get('request', ())):
            callback(request)

    def navigation_request(self):
        return SimpleNamespace(frame=self.main_frame, is_navigation_request=lambda: True)

    def document(self, status=200):
        response = FakeResponse(self.url, status, frame=self.main_frame)
        if self.pending_request is not None:
            response.request = self.pending_request
        return response

    async def goto(self, url, **kwargs):
        self.gotos.append(url)
        if self.goto_error is not None:
            raise self.goto_error
        self.url = url
        self.deliver_request(self.navigation_request())
        response = self.document()
        self.deliver(response)
        return response

    async def evaluate(self, script):
        if script == 'window.scrollTo(0, 0)' and self.scroll_faults:
            self.scroll_faults -= 1
            self.deliver_request(self.navigation_request())
            if self.racing_document is not None:
                html, status = self.racing_document
                self.html = html
                self.read_error = None
                self.deliver(self.document(status=status))
            if self.close_after_scroll_fault:
                self.closed = True
            raise RuntimeError(EXECUTION_CONTEXT_DESTROYED)
        return None

    async def content(self):
        self.reads += 1
        if self.read_error is not None:
            self.deliver_request(self.navigation_request())
            raise self.read_error
        return self.html

    async def bring_to_front(self):
        pass

    async def screenshot(self, *, path, full_page=False):
        Path(path).write_bytes(b'fake-auto-ru-screenshot')
        self.screenshots.append(path)


async def wait_until(predicate, timeout=5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, 'timed out waiting for the scan to progress'
        await asyncio.sleep(0.01)


async def fail_reopen():
    raise AssertionError('an open managed tab must not be recreated')


def waiting_for_operator(events):
    return any(event['event'] == 'navigation_operator_required' for event in events)


# --- main-frame document tracking helper --------------------------------------

def test_watcher_counts_only_fresh_main_frame_navigation_documents():
    url = 'https://auto.ru/cars/hongqi/hq9/new/?output_type=list'
    tab = FakeTab(url, '')
    watcher = watch_main_frame_documents(tab)
    try:
        tab.deliver(FakeResponse(url, 200, frame=object()))
        assert watcher.response is None, 'a subframe response must not count'
        tab.deliver(FakeResponse(url, 200, frame=tab.main_frame, navigation=False))
        assert watcher.response is None, 'an XHR response must not count'
        tab.deliver(tab.document())
        assert watcher.response is not None
        tab.deliver_request(SimpleNamespace(frame=tab.main_frame,
                                            is_navigation_request=lambda: False))
        assert watcher.response is not None, 'an XHR request must not reset the document'
        tab.deliver_request(SimpleNamespace(frame=object(), is_navigation_request=lambda: True))
        assert watcher.response is not None, 'a subframe navigation must not reset the document'
        tab.deliver_request(tab.navigation_request())
        assert watcher.response is None, 'a new main-frame request discards the stale response'
    finally:
        watcher.detach()
    assert not any(tab.listeners.values())


def test_document_arrived_before_recovery_entry_is_accepted_without_new_events():
    async def exercise():
        url = 'https://auto.ru/cars/hongqi/hq9/new/?output_type=list'
        tab = FakeTab(url, 'старый документ')
        watcher = watch_main_frame_documents(tab)
        try:
            tab.deliver(tab.document())
            assert watcher.response is not None
            tab.deliver_request(tab.navigation_request())
            assert watcher.response is None, 'the old HTTP 200 must be discarded'
            fresh = tab.document()
            tab.html = catalogue(5)
            tab.deliver(fresh)  # the fresh document arrived BEFORE the recovery wait
            recovered, response = await asyncio.wait_for(wait_for_navigation_recovery(
                tab, url=url, reopen=fail_reopen, progress=None, page_number=1,
                request_timeout=1, poll_seconds=0.05, source='auto_ru',
                document_watcher=watcher), timeout=3)
        finally:
            watcher.detach()
        assert recovered is tab
        assert response is fresh
        assert not any(tab.listeners.values())

    asyncio.run(exercise())


# --- wait_for_navigation_recovery: unit behaviour with source='auto_ru' ------

def test_recovery_labels_events_auto_ru_and_requires_main_frame_document():
    async def exercise():
        url = 'https://auto.ru/cars/hongqi/hq9/new/?output_type=list'
        tab = FakeTab(url, catalogue(1))
        events = []
        task = asyncio.create_task(wait_for_navigation_recovery(
            tab, url=url, reopen=fail_reopen, progress=events.append, page_number=1,
            request_timeout=1, poll_seconds=0.05, source='auto_ru'))
        try:
            await asyncio.sleep(0.03)
            tab.deliver(FakeResponse(url, 200, frame=object()))
            await asyncio.sleep(0.15)
            assert not task.done(), 'a subframe document must not recover the page'
            tab.deliver(FakeResponse(url, 200, frame=tab.main_frame, navigation=False))
            await asyncio.sleep(0.15)
            assert not task.done(), 'an XHR response must not recover the page'
            tab.deliver(tab.document())
            recovered, response = await asyncio.wait_for(task, timeout=2)
        finally:
            task.cancel()
        assert recovered is tab
        assert response.status == 200
        assert any(e['event'] == 'navigation_operator_required' for e in events)
        assert any(e['event'] == 'navigation_operator_resolved' for e in events)
        assert all(e['source'] == 'auto_ru' and e['page'] == 1 and e['url'] == url
                   for e in events)
        assert not any(tab.listeners.values())

    asyncio.run(exercise())


def test_successful_document_needs_two_stable_reads():
    async def exercise():
        url = 'https://auto.ru/cars/hongqi/hq9/new/?output_type=list'
        tab = FakeTab(url, 'первый документ')

        async def reads_reached(count):
            deadline = asyncio.get_running_loop().time() + 5
            while tab.reads < count:
                assert asyncio.get_running_loop().time() < deadline, 'recovery stopped reading'
                await asyncio.sleep(0.01)

        task = asyncio.create_task(wait_for_navigation_recovery(
            tab, url=url, reopen=fail_reopen, progress=None, page_number=1,
            request_timeout=1, poll_seconds=0.5, source='auto_ru'))
        try:
            await asyncio.sleep(0.1)
            first = tab.document()
            tab.deliver(first)
            await reads_reached(1)
            assert not task.done(), 'a single observation must not recover the page'
            replaced = FakeResponse(url + '&replacement=1', 200, frame=tab.main_frame)
            tab.deliver(replaced)
            await reads_reached(2)
            assert not task.done(), 'a document replaced between reads needs a new stable pair'
            tab.deliver(first)
            recovered, response = await asyncio.wait_for(task, timeout=5)
        finally:
            task.cancel()
        assert recovered is tab
        assert response is first

    asyncio.run(exercise())


def test_http_error_document_never_counts_as_successful_recovery():
    async def exercise():
        url = 'https://auto.ru/cars/hongqi/hq9/new/?output_type=list'
        tab = FakeTab(url, '<h1>403 Forbidden</h1>')
        task = asyncio.create_task(wait_for_navigation_recovery(
            tab, url=url, reopen=fail_reopen, progress=None, page_number=1,
            request_timeout=1, poll_seconds=0.05, source='auto_ru'))
        try:
            await asyncio.sleep(0.03)
            tab.deliver(FakeResponse(url, 403, frame=tab.main_frame))
            for _ in range(20):
                await asyncio.sleep(0.05)
                assert not task.done(), 'an HTTP 403 document must not recover the page'
            allowed = tab.document()
            tab.deliver(allowed)
            recovered, response = await asyncio.wait_for(task, timeout=2)
        finally:
            task.cancel()
        assert recovered is tab
        assert response is allowed

    asyncio.run(exercise())


def test_fresh_challenge_document_is_handed_to_the_manual_captcha_handler():
    async def exercise():
        url = 'https://auto.ru/cars/hongqi/hq9/new/?output_type=list'
        tab = FakeTab(url, CHALLENGE_HTML)
        events = []
        task = asyncio.create_task(wait_for_navigation_recovery(
            tab, url=url, reopen=fail_reopen, progress=events.append, page_number=1,
            request_timeout=1, poll_seconds=0.05, source='auto_ru'))
        try:
            await asyncio.sleep(0.03)
            tab.deliver(tab.document())
            recovered, response = await asyncio.wait_for(task, timeout=2)
        finally:
            task.cancel()
        assert recovered is tab
        assert response.status == 200
        assert tab.html == CHALLENGE_HTML  # the human handler receives the challenge untouched
        assert any(e['event'] == 'navigation_operator_resolved' for e in events)

    asyncio.run(exercise())


def test_closed_tab_gets_exactly_one_replacement_navigation():
    async def exercise():
        url = 'https://auto.ru/cars/hongqi/hq9/new/?output_type=list'
        dead = FakeTab(url, 'старый документ')
        dead.closed = True
        replacement = FakeTab(url, catalogue(2))
        reopened = []

        async def reopen():
            reopened.append(replacement)
            return replacement

        recovered, response = await wait_for_navigation_recovery(
            dead, url=url, reopen=reopen, progress=None, page_number=1,
            request_timeout=1, poll_seconds=0.05, source='auto_ru')
        assert reopened == [replacement]
        assert recovered is replacement
        assert replacement.gotos == [url]
        assert response.status == 200
        assert not any(replacement.listeners.values())

    asyncio.run(exercise())


def test_nonrecoverable_failure_inside_recovery_surfaces():
    async def exercise():
        dead = FakeTab('https://auto.ru/cars/all/', '')
        dead.closed = True

        async def reopen():
            raise RuntimeError('Connection to browser lost')

        with pytest.raises(RuntimeError, match='Connection to browser lost'):
            await wait_for_navigation_recovery(
                dead, url=dead.url, reopen=reopen, progress=None, page_number=1,
                request_timeout=1, poll_seconds=0.01, source='auto_ru')

    asyncio.run(exercise())


def test_recovery_defaults_to_avito_source_labels():
    async def exercise():
        url = 'https://www.avito.ru/moskva/avtomobili'
        tab = FakeTab(url, '<div data-marker="catalog-serp">список</div>')
        events = []
        task = asyncio.create_task(wait_for_navigation_recovery(
            tab, url=url, reopen=fail_reopen, progress=events.append, page_number=2,
            request_timeout=1, poll_seconds=0.05))
        try:
            await asyncio.sleep(0.03)
            tab.deliver(tab.document())
            await asyncio.wait_for(task, timeout=2)
        finally:
            task.cancel()
        assert events
        assert all(e['source'] == 'avito' for e in events)

    asyncio.run(exercise())


# --- AutoRuAdapter.scan_filter: recoverable navigation exception boundary ----

class AutoRuScanHarness:
    """Adapter wiring over scripted fake tabs; no browser, network or scans."""

    def __init__(self, tmp_path, monkeypatch):
        self.events = []
        self.entered = []
        self.exited = []
        self.tabs = []
        self.scripted = []
        self.open_fault = None
        for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds',
                    'auto_ru_page_delay_seconds'):
            monkeypatch.setattr(settings, key, 0)
        monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
        monkeypatch.setattr('app.scraper.auto_ru.operator_wait_seconds', lambda source: -1)
        harness = self

        @asynccontextmanager
        async def local_browser_page(_playwright, source=None):
            assert source == 'auto_ru'
            if harness.open_fault is not None:
                fault, harness.open_fault = harness.open_fault, None
                raise fault
            harness.entered.append(source)
            assert harness.scripted, 'no scripted tab for the managed page'
            tab = harness.scripted.pop(0)
            harness.tabs.append(tab)
            try:
                yield tab
            finally:
                harness.exited.append(source)

        monkeypatch.setattr('app.scraper.auto_ru.browser_page', local_browser_page)

        @asynccontextmanager
        async def local_playwright():
            yield object()

        monkeypatch.setattr('app.scraper.auto_ru.async_playwright', local_playwright)

        async def quick_recovery(*args, **kwargs):
            return await wait_for_navigation_recovery(*args, **kwargs, poll_seconds=0.01)

        monkeypatch.setattr('app.scraper.auto_ru.wait_for_navigation_recovery', quick_recovery)

    def adapter(self):
        adapter = AutoRuAdapter()
        adapter.progress_callback = self.events.append
        return adapter


@pytest.mark.parametrize('failure, race', [
    ('scroll', 'late'),
    ('read', 'late'),
    ('scroll', 'early'),
])
def test_execution_context_failure_recovers_and_processes_recovered_document(
        tmp_path, monkeypatch, failure, race):
    async def exercise():
        harness = AutoRuScanHarness(tmp_path, monkeypatch)
        if failure == 'read':
            monkeypatch.setattr(settings, 'request_timeout_seconds', 0.05)
        tab = FakeTab('', catalogue(1))
        if failure == 'scroll':
            tab.scroll_faults = 1
        else:
            tab.read_error = RuntimeError(
                'Page.content: Execution context was destroyed, most likely because of a navigation')
        if race == 'early':
            # Regression: the fresh main-frame document completes while the
            # destroyed execution context is still propagating, before the
            # recovery wait starts; the open page must not hang.
            tab.racing_document = (catalogue(7), 200)
        harness.scripted.append(tab)
        scan = asyncio.create_task(harness.adapter().scan_filter(SEARCH_URL, max_pages=1))
        await wait_until(lambda: waiting_for_operator(harness.events))
        if race == 'late':
            tab.read_error = None
            tab.html = catalogue(7)
            tab.deliver(tab.document())
        return harness, tab, await asyncio.wait_for(scan, timeout=10)

    harness, tab, result = asyncio.run(exercise())
    assert result.error is None
    assert result.complete and result.page_count == 1
    assert [hit.url for hit in result.hits] == [
        'https://auto.ru/cars/new/group/hongqi/hq9/1/2/1133335007-car/'
    ]
    assert result.diagnostics['page_1_http_status'] == 200
    assert result.diagnostics['page_1_listing_keys'] == ['auto_ru:1133335007']
    assert len(tab.gotos) == 1  # the recovered document is processed, not re-requested
    assert harness.entered == harness.exited == ['auto_ru']
    assert all(event['source'] == 'auto_ru' for event in harness.events)
    assert any(event['event'] == 'navigation_operator_required' for event in harness.events)
    assert any(event['event'] == 'navigation_operator_resolved' for event in harness.events)
    assert any(event['event'] == 'page_finished' for event in harness.events)
    assert not any(event['event'] == 'page_failed' for event in harness.events)
    assert not any(tab.listeners.values())


def test_recovered_challenge_document_goes_to_the_human_captcha_handler(
        tmp_path, monkeypatch):
    async def exercise():
        harness = AutoRuScanHarness(tmp_path, monkeypatch)
        tab = FakeTab('', catalogue(1))
        tab.scroll_faults = 1
        harness.scripted.append(tab)
        scan = asyncio.create_task(harness.adapter().scan_filter(SEARCH_URL, max_pages=1))
        await wait_until(lambda: waiting_for_operator(harness.events))
        tab.html = CHALLENGE_HTML
        tab.deliver(tab.document())
        await wait_until(lambda: any(event['event'] == 'captcha_operator_required'
                                     for event in harness.events))
        events = list(harness.events)
        scan.cancel()
        with pytest.raises(asyncio.CancelledError):
            await scan
        return harness, tab, events

    harness, tab, events = asyncio.run(exercise())
    assert any(event['event'] == 'navigation_operator_required' for event in events)
    assert any(event['event'] == 'navigation_operator_resolved' for event in events)
    captcha = [event for event in events if event['event'] == 'captcha_operator_required']
    assert captcha and captcha[0]['source'] == 'auto_ru'
    assert all(event['source'] == 'auto_ru' for event in events)
    assert tab.screenshots  # challenge evidence was captured for the operator
    assert harness.entered == harness.exited == ['auto_ru']
    assert not any(tab.listeners.values())


def test_nonrecoverable_page_error_produces_auditable_technical_result(
        tmp_path, monkeypatch):
    async def exercise():
        harness = AutoRuScanHarness(tmp_path, monkeypatch)
        tab = FakeTab('', catalogue(1))
        tab.goto_error = RuntimeError('Page.goto: Navigation failed because page crashed!')
        harness.scripted.append(tab)
        result = await asyncio.wait_for(
            harness.adapter().scan_filter(SEARCH_URL, max_pages=1), timeout=10)
        return harness, result

    harness, result = asyncio.run(exercise())
    assert result.error and 'Page.goto: Navigation failed because page crashed!' in result.error
    assert result.diagnostics['page_1_state'] == 'technical_error'
    assert not result.complete and result.hits == []
    failed = [event for event in harness.events if event['event'] == 'page_failed']
    assert failed and failed[0]['source'] == 'auto_ru'
    assert not any(event['event'] == 'navigation_operator_required' for event in harness.events)
    assert harness.entered == harness.exited == ['auto_ru']


def test_nonrecoverable_failure_during_recovery_is_an_auditable_technical_result(
        tmp_path, monkeypatch):
    async def exercise():
        harness = AutoRuScanHarness(tmp_path, monkeypatch)
        tab = FakeTab('', catalogue(1))
        tab.scroll_faults = 1
        harness.scripted.append(tab)
        scan = asyncio.create_task(harness.adapter().scan_filter(SEARCH_URL, max_pages=1))
        await wait_until(lambda: waiting_for_operator(harness.events))
        tab.read_error = RuntimeError('Connection to browser lost')
        tab.deliver(tab.document())
        result = await asyncio.wait_for(scan, timeout=10)
        return harness, result

    harness, result = asyncio.run(exercise())
    assert result.error and 'Connection to browser lost' in result.error
    assert result.diagnostics['page_1_state'] == 'technical_error'
    assert not result.complete
    assert any(event['event'] == 'navigation_operator_required' for event in harness.events)
    assert any(event['event'] == 'page_failed' for event in harness.events)
    assert harness.entered == harness.exited == ['auto_ru']


def test_closed_managed_tab_is_reopened_once_and_the_scan_continues(tmp_path, monkeypatch):
    async def exercise():
        harness = AutoRuScanHarness(tmp_path, monkeypatch)
        dead = FakeTab('', catalogue(1))
        dead.scroll_faults = 1
        dead.close_after_scroll_fault = True
        replacement = FakeTab('', catalogue(9))
        harness.scripted.extend([dead, replacement])
        result = await asyncio.wait_for(
            harness.adapter().scan_filter(SEARCH_URL, max_pages=1), timeout=10)
        return harness, dead, replacement, result

    harness, dead, replacement, result = asyncio.run(exercise())
    assert result.error is None and result.complete and result.page_count == 1
    assert [hit.url for hit in result.hits] == [
        'https://auto.ru/cars/new/group/hongqi/hq9/1/2/1133335009-car/'
    ]
    assert dead.closed
    assert replacement.gotos == dead.gotos  # reopened at the same target address
    assert harness.entered == harness.exited == ['auto_ru', 'auto_ru']
    assert all(event['source'] == 'auto_ru' for event in harness.events)
    assert any(event['event'] == 'navigation_operator_resolved' for event in harness.events)


def test_recoverable_initial_open_failure_waits_for_the_recovered_document(
        tmp_path, monkeypatch):
    async def exercise():
        harness = AutoRuScanHarness(tmp_path, monkeypatch)
        harness.open_fault = RuntimeError('BrowserType.connect_over_cdp: connect ECONNREFUSED')
        tab = FakeTab('', catalogue(3))
        harness.scripted.append(tab)
        result = await asyncio.wait_for(
            harness.adapter().scan_filter(SEARCH_URL, max_pages=1), timeout=10)
        return harness, tab, result

    harness, tab, result = asyncio.run(exercise())
    assert result.error is None and result.complete
    assert harness.tabs == [tab]
    assert len(tab.gotos) == 1
    assert harness.entered == harness.exited == ['auto_ru']
    assert all(event['source'] == 'auto_ru' for event in harness.events)
    assert any(event['event'] == 'navigation_operator_required' for event in harness.events)
    assert any(event['event'] == 'navigation_operator_resolved' for event in harness.events)


def test_late_response_of_old_request_cannot_restore_stale_document():
    tab = FakeTab(SEARCH_URL, catalogue(1))
    watcher = watch_main_frame_documents(tab)
    try:
        tab.deliver_request(tab.navigation_request())
        old = tab.document()
        tab.deliver(old)
        assert watcher.response is old
        tab.deliver_request(tab.navigation_request())
        assert watcher.response is None
        tab.deliver(old)
        assert watcher.response is None
        fresh = tab.document()
        tab.deliver(fresh)
        assert watcher.response is fresh
    finally:
        watcher.detach()
    assert not any(tab.listeners.values())


def test_request_starting_during_read_cannot_resolve_old_document():
    async def exercise():
        tab = FakeTab(SEARCH_URL, catalogue(1))
        watcher = watch_main_frame_documents(tab)
        tab.deliver_request(tab.navigation_request())
        tab.deliver(tab.document())
        reads = 0

        async def content():
            nonlocal reads
            reads += 1
            if reads == 2:
                tab.deliver_request(tab.navigation_request())
            return tab.html

        tab.content = content
        task = asyncio.create_task(wait_for_navigation_recovery(
            tab, url=SEARCH_URL, reopen=fail_reopen, progress=None, page_number=1,
            request_timeout=1, poll_seconds=0.01, source='auto_ru', document_watcher=watcher))
        try:
            await wait_until(lambda: reads >= 2)
            await asyncio.sleep(0.03)
            assert not task.done(), 'an in-flight navigation must not reuse the old 200'
            fresh = tab.document()
            tab.deliver(fresh)
            recovered, response = await asyncio.wait_for(task, timeout=2)
            assert recovered is tab and response is fresh
        finally:
            task.cancel()
            watcher.detach()
    asyncio.run(exercise())


def test_repeated_initial_open_error_stays_cancellable_without_skipping():
    async def exercise():
        attempts = 0
        events = []
        async def failing_reopen():
            nonlocal attempts
            attempts += 1
            raise RuntimeError('BrowserType.connect_over_cdp: connect ECONNREFUSED')
        task = asyncio.create_task(wait_for_navigation_recovery(
            None, url=SEARCH_URL, reopen=failing_reopen, progress=events.append,
            page_number=1, request_timeout=1, poll_seconds=0.01, source='auto_ru'))
        try:
            await wait_until(lambda: attempts >= 2)
            assert not task.done()
            assert any(e['event'] == 'navigation_operator_waiting' for e in events)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(exercise())
