from __future__ import annotations

import asyncio
from datetime import datetime
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from playwright.async_api import Browser, Page, async_playwright

from app.config import settings
from app.models import EngineType
from app.scraper.base import ListingHit, ScanResult


class AutoRuAdapter:
    source = EngineType.AUTO_RU

    def __init__(self, request_timeout: int = 20) -> None:
        self.request_timeout = request_timeout

    async def scan_filter(self, search_url: str, max_pages: int = 3) -> ScanResult:
        start = datetime.utcnow()
        hits: list[ListingHit] = []
        pages_scanned = 0
        diagnostics: dict[str, int | str] = {'engine': 'auto_ru', 'start_url': search_url}
        async with async_playwright() as p:
            browser: Browser = await p.chromium.launch(headless=settings.playwright_headless)
            page: Page = await browser.new_page()
            await page.set_viewport_size({'width': 1920, 'height': 1080})

            try:
                for page_number in range(1, max_pages + 1):
                    url = _page_url(search_url, page_number)
                    diagnostics[f'page_{page_number}'] = 0
                    await page.goto(url, timeout=self.request_timeout * 1000)
                    await page.wait_for_load_state('networkidle')
                    html = await page.content()
                    parsed = self._extract(html, page_number)
                    diagnostics[f'page_{page_number}'] = len(parsed)
                    hits.extend(parsed)
                    pages_scanned += 1
                    await page.mouse.wheel(0, 1200)
                    await asyncio.sleep(0.5)
            finally:
                await browser.close()

        return ScanResult(
            filter_id='',
            page_count=pages_scanned,
            hits=hits,
            diagnostics=diagnostics,
            scanned_at=start,
        )

    def _extract(self, html: str, page_number: int) -> list[ListingHit]:
        soup = BeautifulSoup(html, 'html.parser')
        cards = soup.select('[data-bumper="SearchResults"] .ListingItem, .ListingItem, .OfferSnippet')
        results: list[ListingHit] = []
        for i, card in enumerate(cards, start=1):
            link = card.select_one('a')
            if not link or not link.get('href'):
                continue
            title = card.get_text(' ', strip=True)[:255]
            raw_url = link['href']
            if raw_url.startswith('//'):
                raw_url = 'https:' + raw_url
            if raw_url.startswith('/'):
                raw_url = 'https://auto.ru' + raw_url
            price_text = card.get_text(' ', strip=True).replace('\u00a0', '').replace(' ', '').lower()
            price = None
            for token in price_text.split():
                if token.isdigit() and int(token) > 0:
                    price = float(token)
                    break
            results.append(
                ListingHit(
                    external_id=str(raw_url),
                    title=title,
                    url=raw_url,
                    page_number=page_number,
                    position=i,
                    price=price,
                    raw={'raw_text': card.get_text(' ', strip=True), 'html': str(card)[:2048]},
                )
            )
        return results


def _page_url(base_url: str, page_number: int) -> str:
    if page_number <= 1:
        return base_url
    sep = '&' if '?' in base_url else '?'
    params = urlencode({'page': page_number})
    return f'{base_url}{sep}{params}'
