from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from playwright.async_api import Browser, Page, Playwright

from app.config import settings


@asynccontextmanager
async def browser_page(playwright: Playwright) -> AsyncIterator[Page]:
    """Open an isolated, ephemeral (incognito) context for every browser visit."""
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
