"""One ordinary page refresh for an explicit CAPTCHA, never a block bypass."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

_CAPTCHA_MARKERS = (
    'captcha', 'showcaptcha', 'подтвердите, что вы не робот',
    'подтвердите, что запросы отправляли вы, а не робот',
    'verify you are human',
)


def explicit_captcha(html: str, final_url: str, http_status: int | None) -> bool:
    """Do not retry rate limits, access denials, or a generic blocked page."""
    if http_status is None or http_status >= 400:
        return False
    text = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True).lower()
    path = urlsplit(final_url).path.lower()
    return '/captcha' in path or any(marker in text for marker in _CAPTCHA_MARKERS)


async def refresh_explicit_captcha(page, response, html: str, *, progress=None, source=None, url=None):
    """Refresh once; return the final page for normal evidence and classification."""
    status = response.status if response is not None else None
    if not explicit_captcha(html, page.url, status):
        return response, html, 0
    if progress:
        progress({'event': 'captcha_refresh', 'source': source, 'url': url, 'attempt': 1})
    await asyncio.sleep(3)
    refreshed = await page.reload(wait_until='domcontentloaded')
    return refreshed, await page.content(), 1
