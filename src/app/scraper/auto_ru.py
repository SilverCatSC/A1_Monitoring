from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from playwright.async_api import Browser, Page, async_playwright

from app.config import settings
from app.models import EngineType
from app.scraper.base import (
    ListingHit,
    ScanResult,
    canonical_listing_key,
    capture_page_evidence,
    classify_result_page,
)


class AutoRuAdapter:
    source = EngineType.AUTO_RU

    def __init__(self, request_timeout: int = 20) -> None:
        self.request_timeout = request_timeout

    async def scan_filter(self, search_url: str, max_pages: int = 3) -> ScanResult:
        start = datetime.now(UTC)
        hits: list[ListingHit] = []
        pages_scanned = 0
        exhausted = False
        error: str | None = None
        diagnostics: dict[str, int | str] = {'engine': 'auto_ru', 'start_url': search_url}
        async with async_playwright() as p:
            browser: Browser = await p.chromium.launch(headless=settings.playwright_headless)
            page: Page = await browser.new_page()
            await page.set_viewport_size({'width': 1920, 'height': 1080})

            try:
                for page_number in range(1, max_pages + 1):
                    url = _page_url(search_url, page_number)
                    diagnostics[f'page_{page_number}'] = 0
                    try:
                        response = await page.goto(
                            url,
                            timeout=self.request_timeout * 1000,
                            wait_until='domcontentloaded',
                        )
                        if response is not None and response.status >= 400:
                            raise RuntimeError(f'HTTP {response.status}')
                        await page.mouse.wheel(0, 1600)
                        await asyncio.sleep(0.7)
                        html = await page.content()
                        evidence_path = await capture_page_evidence(
                            page,
                            source=self.source,
                            search_url=search_url,
                            page_number=page_number,
                            evidence_dir=settings.evidence_dir,
                        )
                        if evidence_path:
                            diagnostics[f'page_{page_number}_evidence'] = evidence_path
                        parsed = self._extract(html, page_number)
                        page_state, reason = classify_result_page(html, len(parsed))
                        diagnostics[f'page_{page_number}'] = len(parsed)
                        diagnostics[f'page_{page_number}_state'] = page_state
                        pages_scanned += 1
                        if page_state == 'blocked':
                            error = f'blocked page {page_number}: {reason}'
                            break
                        if page_state == 'unrecognized':
                            error = f'parser uncertainty on page {page_number}: {reason}'
                            break
                        if page_state == 'empty':
                            exhausted = True
                            break
                        hits.extend(parsed)
                    except Exception as exc:
                        error = f'page {page_number}: {type(exc).__name__}: {exc}'
                        diagnostics[f'page_{page_number}_state'] = 'technical_error'
                        break
            finally:
                await browser.close()

        return ScanResult(
            filter_id='',
            page_count=pages_scanned,
            hits=hits,
            diagnostics=diagnostics,
            scanned_at=start,
            requested_pages=max_pages,
            complete=error is None and (pages_scanned == max_pages or exhausted),
            exhausted=exhausted,
            error=error,
        )

    def _extract(self, html: str, page_number: int) -> list[ListingHit]:
        soup = BeautifulSoup(html, 'html.parser')
        results: list[ListingHit] = []
        seen: set[str] = set()
        links = soup.select(
            'a.ListingItemTitle__link[href*="/cars/"][href*="/sale/"], '
            'a[href*="/cars/used/sale/"], a[href*="/cars/new/sale/"]'
        )
        for link in links:
            if not link.get('href'):
                continue
            raw_url = link['href']
            if raw_url.startswith('//'):
                raw_url = 'https:' + raw_url
            if raw_url.startswith('/'):
                raw_url = 'https://auto.ru' + raw_url
            key = canonical_listing_key(EngineType.AUTO_RU, raw_url)
            if not key or key in seen:
                continue
            seen.add(key)

            card = None
            for ancestor in link.parents:
                classes = ancestor.get('class') or []
                if any(
                    class_name == 'ListingItem'
                    or class_name == 'OfferSnippet'
                    or (
                        class_name.startswith('ListingItemUniversal-')
                        and '__' not in class_name
                    )
                    for class_name in classes
                ):
                    card = ancestor
                    break
            card = card or link.parent
            card_text = card.get_text(' ', strip=True)
            title = link.get_text(' ', strip=True)[:255] or card_text[:255]
            price_match = re.search(r'(?<!\d)(\d{1,3}(?:[\s\u00a0]\d{3})+)\s*₽', card_text)
            price = (
                float(re.sub(r'\D', '', price_match.group(1)))
                if price_match and re.sub(r'\D', '', price_match.group(1))
                else None
            )
            results.append(
                ListingHit(
                    external_id=str(raw_url),
                    title=title,
                    url=raw_url,
                    page_number=page_number,
                    position=len(results) + 1,
                    price=price,
                    raw={'raw_text': card_text, 'html': str(card)[:2048]},
                )
            )
        return results


def _page_url(base_url: str, page_number: int) -> str:
    if page_number <= 1:
        return base_url
    sep = '&' if '?' in base_url else '?'
    params = urlencode({'page': page_number})
    return f'{base_url}{sep}{params}'
