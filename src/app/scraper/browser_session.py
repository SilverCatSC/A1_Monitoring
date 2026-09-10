from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from playwright.async_api import Browser, Page, Playwright

from app.config import settings


@asynccontextmanager
async def browser_page(playwright: Playwright) -> AsyncIterator[Page]:
    """Open a page in managed Chromium or attach to a user's local Chrome session."""
    browser: Browser
    attached = bool(settings.browser_cdp_url)
    if attached:
        browser = await playwright.chromium.connect_over_cdp(settings.browser_cdp_url)
        if not browser.contexts or not browser.contexts[0].pages:
            raise RuntimeError(
                'Local Chrome has no controllable page. Close the worker Chrome and '
                'restart scripts/local_scan.sh.'
            )
        context = browser.contexts[0]
        page = context.pages[0]
        await page.bring_to_front()
    else:
        browser = await playwright.chromium.launch(headless=settings.playwright_headless)
        context = await browser.new_context()
        page = await context.new_page()
    await page.set_viewport_size({'width': 1920, 'height': 1080})
    try:
        yield page
    finally:
        if not attached:
            await page.close()
            await browser.close()
