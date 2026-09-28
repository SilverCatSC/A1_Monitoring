from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar, copy_context
from typing import AsyncIterator, Awaitable, TypeVar

from playwright.async_api import Browser, Page, Playwright, async_playwright

from app.config import settings

T = TypeVar('T')
_active_cycle: ContextVar[CycleBrowserSession | None] = ContextVar(
    'monitoring_browser_cycle', default=None,
)


class CycleBrowserSession:
    """One event loop and one disposable private context per marketplace.

    The contexts exist only within one synchronous MonitoringCycleService.run.
    No cookies or storage state are written to disk or passed to the next run.
    """

    def __init__(self) -> None:
        self.runner = asyncio.Runner()
        self._token = None
        self._playwright = None
        self._browser = None
        self._contexts = {}
        self._attached = bool(settings.browser_cdp_url)

    def __enter__(self) -> CycleBrowserSession:
        self.runner.__enter__()
        self._token = _active_cycle.set(self)
        return self

    def run(self, awaitable: Awaitable[T]) -> T:
        return self.runner.run(awaitable, context=copy_context())

    async def _context(self, source: str):
        if source in self._contexts:
            return self._contexts[source]
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
        self._contexts[source] = context
        return context

    async def new_page(self, source: str) -> Page:
        context = await self._context(source)
        page = await context.new_page()
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


@asynccontextmanager
async def browser_page(playwright: Playwright, source: str | None = None) -> AsyncIterator[Page]:
    """Open one page within a cycle context, or a one-shot private context."""
    session = _active_cycle.get()
    if session is not None and source is not None:
        page = await session.new_page(source)
        try:
            yield page
        finally:
            try:
                await page.close()
            except Exception:
                pass
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
        await context.close()
        if not attached:
            await browser.close()
