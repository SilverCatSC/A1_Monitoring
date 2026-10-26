from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, copy_context
from typing import AsyncIterator, Awaitable, TypeVar

from playwright.async_api import Browser, Page, Playwright, async_playwright

from app.config import settings

T = TypeVar('T')
_active_cycle: ContextVar[CycleBrowserSession | None] = ContextVar(
    'monitoring_browser_cycle', default=None,
)
_browser_scope: ContextVar[str] = ContextVar('monitoring_browser_scope', default='inspection')


@contextmanager
def search_browser_session():
    """Keep search cookies/cache separate from dealer and direct-card visits.

    Search pages of one source share CAPTCHA state until this cycle ends.
    No storage is copied from the inspection or operator's normal context.
    """
    token = _browser_scope.set('search')
    try:
        yield
    finally:
        _browser_scope.reset(token)


class CycleBrowserSession:
    """One event loop and disposable private contexts per marketplace and scope.

    The contexts exist only within one synchronous MonitoringCycleService.run.
    No cookies or storage state are written to disk or passed to the next run.
    """

    def __init__(self) -> None:
        self.runner = asyncio.Runner()
        self._token = None
        self._playwright = None
        self._browser = None
        self._contexts = {}
        self._pages = {}
        self._attached = bool(settings.browser_cdp_url)

    def __enter__(self) -> CycleBrowserSession:
        self.runner.__enter__()
        self._token = _active_cycle.set(self)
        return self

    def run(self, awaitable: Awaitable[T]) -> T:
        return self.runner.run(awaitable, context=copy_context())

    async def _context(self, source: str):
        if (self._browser is not None and hasattr(self._browser, 'is_connected')
                and not self._browser.is_connected()):
            self._contexts.clear()
            self._pages.clear()
            await self._playwright.stop()
            self._playwright = None
            self._browser = None
        key = (_browser_scope.get(), source)
        if key in self._contexts:
            return self._contexts[key]
        if self._playwright is None:
            self._playwright = await async_playwright().start()
            if self._attached:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    settings.browser_cdp_url
                )
            else:
                self._browser = await self._playwright.chromium.launch(
                    headless=settings.playwright_headless
                )
        context = await self._browser.new_context()
        self._contexts[key] = context
        return context

    async def new_page(self, source: str) -> Page:
        context = await self._context(source)
        key = (_browser_scope.get(), source)
        page = self._pages.get(key)
        if page is None or page.is_closed():
            try:
                page = await context.new_page()
            except Exception as exc:
                if 'has been closed' not in str(exc).lower():
                    raise
                self._contexts.pop(key, None)
                self._pages.pop(key, None)
                if not self._browser.is_connected():
                    self._contexts.clear()
                    self._pages.clear()
                    await self._playwright.stop()
                    self._playwright = None
                    self._browser = None
                context = await self._context(source)
                page = await context.new_page()
            self._pages[key] = page
        if self._attached:
            await page.bring_to_front()
        await page.set_viewport_size({'width': 1920, 'height': 1080})
        return page

    async def _close(self) -> None:
        errors = []
        try:
            for context in self._contexts.values():
                try:
                    await context.close()
                except Exception as exc:
                    if 'has been closed' not in str(exc).lower():
                        errors.append(exc)
            if self._browser is not None and not self._attached:
                try:
                    await self._browser.close()
                except Exception as exc:
                    errors.append(exc)
        finally:
            if self._playwright is not None:
                await self._playwright.stop()
        if errors:
            raise RuntimeError('browser cycle context cleanup failed') from errors[0]

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        try:
            self.run(self._close())
        finally:
            if self._token is not None:
                _active_cycle.reset(self._token)
            self.runner.__exit__(_exc_type, _exc_value, _traceback)


def run_browser_task(awaitable: Awaitable[T]) -> T:
    """Use the cycle's live loop, or a one-shot loop outside a full cycle."""
    session = _active_cycle.get()
    return session.run(awaitable) if session is not None else asyncio.run(awaitable)


async def read_page_content(page: Page, *, timeout_seconds: float | None = None) -> str:
    """Wait through an in-flight document transition without reloading the page.

    A manual CAPTCHA submission may navigate while content() is being read.
    Retry only that transient error, keeping real access and browser failures.
    """
    timeout = settings.request_timeout_seconds if timeout_seconds is None else timeout_seconds
    deadline = asyncio.get_running_loop().time() + max(0, timeout)
    while True:
        try:
            return await page.content()
        except Exception as exc:
            message = str(exc).lower()
            if ('page is navigating and changing the content' not in message
                    and 'execution context was destroyed' not in message):
                raise
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise
            await asyncio.sleep(min(0.1, remaining))


@asynccontextmanager
async def browser_page(playwright: Playwright, source: str | None = None) -> AsyncIterator[Page]:
    """Open one page within a cycle context, or a one-shot private context."""
    session = _active_cycle.get()
    if session is not None and source is not None:
        page = await session.new_page(source)
        # Keep the window alive between sequential visits. Closing the last
        # page recreates a visible blank window on every following visit.
        # The cycle still disposes of every private context at its end.
        yield page
        return
    browser: Browser
    attached = bool(settings.browser_cdp_url)
    if attached:
        browser = await playwright.chromium.connect_over_cdp(settings.browser_cdp_url)
    else:
        browser = await playwright.chromium.launch(headless=settings.playwright_headless)
    context = await browser.new_context()
    try:
        page = await context.new_page()
        if attached:
            await page.bring_to_front()
        await page.set_viewport_size({'width': 1920, 'height': 1080})
        yield page
    finally:
        try:
            await context.close()
        except Exception as exc:
            if 'has been closed' not in str(exc).lower():
                raise
        if not attached:
            await browser.close()
