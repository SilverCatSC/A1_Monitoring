"""One ordinary CAPTCHA refresh and bounded same-page operator assistance."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from bs4 import BeautifulSoup

from app.config import settings

_CAPTCHA_MARKERS = (
    'captcha', 'showcaptcha', 'подтвердите, что вы не робот',
    'подтвердите, что запросы отправляли вы, а не робот',
    'verify you are human',
)


@dataclass(frozen=True)
class CaptchaRecovery:
    response: Any
    html: str
    refreshes: int
    outcome: str
    challenge_evidence: str | None = None


def operator_wait_seconds(source: str | None) -> int:
    """Human assistance is allowed only in the visible Mac Auto.ru browser."""
    if (source != 'auto_ru' or not settings.browser_cdp_url
            or settings.playwright_headless):
        return 0
    return settings.captcha_operator_wait_seconds


def _same_requested_page(requested_url: str | None, final_url: str) -> bool:
    if not requested_url:
        return False
    requested = urlsplit(requested_url)
    final = urlsplit(final_url)
    def path(url):
        return re.sub(r'/items(?=/all/)', '', url.path.rstrip('/'))

    def required_query(url):
        return Counter(
            (key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
            if key not in {'context', 'src', 'iid', 'yclid'} and not key.startswith('utm_')
        )

    return (
        requested.hostname is not None
        and (requested.hostname or '').removeprefix('www.') == (final.hostname or '').removeprefix('www.')
        and path(requested) == path(final)
        and required_query(requested) <= required_query(final)
    )


def explicit_captcha(html: str, final_url: str, http_status: int | None) -> bool:
    """Do not retry rate limits, access denials, or a generic blocked page."""
    if http_status is None or http_status >= 400:
        return False
    text = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True).lower()
    path = urlsplit(final_url).path.lower()
    return any(marker in path for marker in ('/captcha', '/showcaptcha')) or any(
        marker in text for marker in _CAPTCHA_MARKERS
    )


async def refresh_explicit_captcha(page, response, html: str, *, progress=None, source=None, url=None,
                                   wait_seconds: int = 0, poll_seconds: float = 2,
                                   capture_challenge=None) -> CaptchaRecovery:
    """Refresh once, then wait on the same page for a person if explicitly enabled.

    No CAPTCHA is clicked or solved by code. A disappeared challenge counts as
    resolved only after the requested host/path is stable twice. Otherwise the
    caller must retain an incomplete/blocked result.
    """
    status = response.status if response is not None else None
    if status is None:
        return CaptchaRecovery(response, html, 0, 'no_document_response')
    if not explicit_captcha(html, page.url, status):
        return CaptchaRecovery(response, html, 0, 'not_needed')
    if progress:
        progress({'event': 'captcha_refresh', 'source': source, 'url': url, 'attempt': 1})
    await asyncio.sleep(3)
    refreshed = await page.reload(wait_until='domcontentloaded')
    html = await page.content()
    status = refreshed.status if refreshed is not None else None
    if status is None:
        return CaptchaRecovery(refreshed, html, 1, 'no_document_response')
    if status >= 400:
        return CaptchaRecovery(refreshed, html, 1, 'http_blocked')
    if not explicit_captcha(html, page.url, status):
        outcome = 'cleared_by_refresh' if _same_requested_page(url, page.url) else 'wrong_destination'
        return CaptchaRecovery(refreshed, html, 1, outcome)
    if wait_seconds <= 0:
        return CaptchaRecovery(refreshed, html, 1, 'unresolved')

    challenge_evidence = await capture_challenge(page, refreshed) if capture_challenge else None
    if capture_challenge and not challenge_evidence:
        return CaptchaRecovery(refreshed, html, 1, 'challenge_evidence_missing')

    await page.bring_to_front()
    if progress:
        progress({'event': 'captcha_operator_required', 'source': source,
                  'url': url, 'wait_seconds': wait_seconds})
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    stable_url = None
    last_html = html
    try:
        while loop.time() < deadline:
            await asyncio.sleep(min(max(0.1, poll_seconds), max(0, deadline - loop.time())))
            last_html = await page.content()
            if explicit_captcha(last_html, page.url, 200):
                stable_url = None
                continue
            if _same_requested_page(url, page.url):
                if stable_url == page.url:
                    if progress:
                        progress({'event': 'captcha_operator_resolved', 'source': source, 'url': url})
                    return CaptchaRecovery(refreshed, last_html, 1, 'resolved_by_operator', challenge_evidence)
                stable_url = page.url
            else:
                stable_url = None
    except Exception:
        if progress:
            progress({'event': 'captcha_operator_unresolved', 'source': source,
                      'url': url, 'outcome': 'browser_error'})
        raise

    outcome = ('wrong_destination' if not explicit_captcha(last_html, page.url, 200)
               and not _same_requested_page(url, page.url) else 'operator_timeout')
    if progress:
        progress({'event': 'captcha_operator_unresolved', 'source': source,
                  'url': url, 'outcome': outcome})
    return CaptchaRecovery(refreshed, last_html, 1, outcome, challenge_evidence)
