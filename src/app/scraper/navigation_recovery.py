"""Keep a visible marketplace scan on the failed page until its document recovers."""
from __future__ import annotations

import asyncio

from app.scraper.browser_session import read_page_content
from app.scraper.challenge_retry import _same_recovered_page, operator_challenge


def recoverable_navigation_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in (
        'target page, context or browser has been closed',
        'net::err_timed_out', 'net::err_connection_', 'net::err_network_changed',
        'net::err_internet_disconnected', 'page.goto: timeout',
        'connect_over_cdp:',
        'page is navigating and changing the content', 'execution context was destroyed',
    ))


class MainFrameDocumentWatcher:
    """Follow the freshest main-frame navigation document on one open page.

    Attached before a goto or read, the watcher also sees the response that
    arrives while a destroyed execution context is still propagating its
    exception. A new main-frame navigation request discards the previous
    response, so a stale HTTP 200 can never be accepted for the current
    document; XHR and subframe traffic never counts in either direction.
    """

    def __init__(self, page):
        self.page = page
        self.response = None
        self.request = None
        self.attached = False

    def attach(self) -> None:
        if not self.attached and hasattr(self.page, 'on'):
            self.page.on('request', self._navigation_requested)
            self.page.on('response', self._document_response)
            self.attached = True

    def detach(self) -> None:
        if self.attached and hasattr(self.page, 'remove_listener'):
            self.page.remove_listener('request', self._navigation_requested)
            self.page.remove_listener('response', self._document_response)
        self.attached = False

    def _navigation_requested(self, request) -> None:
        if request.is_navigation_request() and request.frame == self.page.main_frame:
            self.response = None
            self.request = request

    def _document_response(self, candidate) -> None:
        request = candidate.request
        if (request.is_navigation_request() and request.frame == self.page.main_frame
                and (self.request is None or request is self.request)):
            self.response = candidate


def watch_main_frame_documents(page) -> MainFrameDocumentWatcher:
    """Attach main-frame document tracking before a goto, scroll or read."""
    watcher = MainFrameDocumentWatcher(page)
    watcher.attach()
    return watcher


async def wait_for_navigation_recovery(page, *, url, reopen, progress, page_number,
                                       request_timeout, poll_seconds=2, source='avito',
                                       document_watcher=None, reject_destination=None):
    """No timed skip and no repeated reloads; a fresh document is mandatory.

    A closed tab gets one replacement navigation. An open tab stays untouched
    while the operator restores connectivity or refreshes it manually. HTTP
    errors alone are not successful recovery: a fresh successful document is
    accepted only after the same target URL and document repeat on consecutive
    reads, while a fresh challenge document goes straight back to the ordinary
    manual CAPTCHA handler. ``source`` labels every progress event and selects
    the marketplace's challenge markers; the ``'avito'`` default keeps the
    existing callers unchanged. ``document_watcher`` continues the main-frame
    tracking the caller attached before its goto or read, so a document that
    already arrived during the failure is seen immediately instead of hanging
    an open page; a watcher created here is detached on exit.
    """
    loop = asyncio.get_running_loop()
    next_notice = 0
    previous_observation = None
    watching = None
    response = None
    owned_watcher = None

    def emit(event, **values):
        if progress:
            progress({'event': event, 'source': source, 'page': page_number,
                      'url': url, **values})

    emit('navigation_operator_required', instruction=
         'Не закрывайте вкладку. После восстановления доступа обновите ЭТУ ЖЕ вкладку (Cmd+R).')
    if page is not None and not page.is_closed():
        if document_watcher is not None and document_watcher.page is page:
            watching = document_watcher
        else:
            watching = MainFrameDocumentWatcher(page)
            watching.attach()
            owned_watcher = watching
    try:
        while True:
            wait_reason = 'awaiting_document'
            try:
                if page is None or page.is_closed():
                    if watching is not None:
                        watching.detach()
                        watching = None
                        owned_watcher = None
                    page = await reopen()
                    response = None
                    previous_observation = None
                    watching = MainFrameDocumentWatcher(page)
                    watching.attach()
                    owned_watcher = watching
                    try:
                        response = await page.goto(url, timeout=request_timeout * 1000,
                                                   wait_until='domcontentloaded')
                    except Exception as exc:
                        if not recoverable_navigation_error(exc):
                            raise
                    if (response is not None and watching.response is None
                            and (watching.request is None or response.request is watching.request)):
                        watching.response = response
                response = watching.response
                if response is not None:
                    html = await read_page_content(page)
                    observation = (page.url, response, response.url, response.status)
                    if watching.response is not response:
                        previous_observation = None
                        wait_reason = 'document_changed'
                    elif not _same_recovered_page(url, page.url, response.url):
                        if (response.status == 200 and reject_destination
                                and reject_destination(url, page.url)):
                            if observation == previous_observation:
                                raise RuntimeError('Requested dealer condition redirected to another condition; inventory coverage unproven')
                            previous_observation = observation
                        else:
                            previous_observation = None
                        wait_reason = 'wrong_destination'
                    elif operator_challenge(html, page.url, source):
                        emit('navigation_operator_resolved', http_status=response.status)
                        return page, response
                    elif response.status == 200:
                        # One read can still land inside a document transition;
                        # accept the successful document only when it repeats.
                        if observation == previous_observation:
                            emit('navigation_operator_resolved', http_status=response.status)
                            return page, response
                        previous_observation = observation
                        wait_reason = 'stabilizing'
                    else:
                        previous_observation = None
                        wait_reason = 'http_denied'
            except Exception as exc:
                if not recoverable_navigation_error(exc):
                    raise
                previous_observation = None
                wait_reason = 'navigation_in_progress'
            if loop.time() >= next_notice:
                emit('navigation_operator_waiting', http_status=response.status if response else None,
                     wait_reason=wait_reason, final_url=page.url if page else None,
                     document_url=response.url if response else None)
                next_notice = loop.time() + 30
            await asyncio.sleep(poll_seconds)
    finally:
        if owned_watcher is not None:
            owned_watcher.detach()
