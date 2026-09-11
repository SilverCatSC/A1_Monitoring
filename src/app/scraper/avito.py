from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import async_playwright

from app.config import settings
from app.models import EngineType
from app.scraper.base import (
    ListingHit,
    ScanResult,
    all_target_listings_found,
    canonical_listing_key,
    capture_listing_card_evidence,
    capture_page_evidence,
    classify_result_page,
    is_marketplace_listing_url,
)
from app.scraper.browser_session import browser_page
from app.scraper.geography import verify_geography
from app.scraper.pacing import choose_pause
from app.scraper.result_scope import pagination_state, primary_cards
from app.scraper.seller import catalogue_html, seller_page_matches


class AvitoAdapter:
    source = EngineType.AVITO

    def __init__(
        self,
        request_timeout: int = 20,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> None:
        self.request_timeout = request_timeout
        self.progress_callback = progress_callback

    def _progress(self, event: str, **payload) -> None:
        if self.progress_callback:
            try:
                self.progress_callback({'event': event, 'source': self.source.value, **payload})
            except Exception:
                pass

    async def scan_filter(
        self,
        search_url: str,
        max_pages: int = 3,
        target_keys: set[str] | None = None,
        seller_catalogue: bool = False,
    ) -> ScanResult:
        start = datetime.now(UTC)
        hits: list[ListingHit] = []
        pages_scanned = 0
        exhausted = False
        targets_satisfied = False
        error: str | None = None
        diagnostics = {'engine': 'avito', 'start_url': search_url}
        seen_pages: set[frozenset[str]] = set()

        async with async_playwright() as p:
            async with browser_page(p) as page:
                for page_number in range(1, max_pages + 1):
                    url = _page_url(search_url, page_number)
                    wait_seconds = choose_pause(
                        settings.scan_page_pause_min_seconds,
                        settings.scan_page_pause_max_seconds,
                    )
                    if wait_seconds:
                        self._progress(
                            'page_wait',
                            page=page_number,
                            pages_total=max_pages,
                            wait_seconds=round(wait_seconds, 1),
                        )
                        await asyncio.sleep(wait_seconds)
                    self._progress(
                        'page_started', page=page_number, pages_total=max_pages, url=url
                    )
                    diagnostics[f'page_{page_number}'] = 0
                    try:
                        response = await page.goto(
                            url,
                            timeout=self.request_timeout * 1000,
                            wait_until='domcontentloaded',
                        )
                        http_status = response.status if response is not None else None
                        diagnostics[f'page_{page_number}_http_status'] = http_status or 0
                        if http_status is None or http_status < 400:
                            await page.mouse.wheel(0, 1600)
                            await asyncio.sleep(settings.avito_page_delay_seconds)
                        html = await page.content()
                        diagnostics[f'page_{page_number}_requested_url'] = url
                        diagnostics[f'page_{page_number}_final_url'] = page.url
                        evidence_path = await capture_page_evidence(
                            page,
                            source=self.source,
                            search_url=search_url,
                            page_number=page_number,
                            evidence_dir=settings.evidence_dir,
                        )
                        if evidence_path:
                            diagnostics[f'page_{page_number}_evidence'] = evidence_path
                        if http_status is not None and http_status >= 400:
                            error = f'page {page_number}: RuntimeError: HTTP {http_status}'
                            diagnostics[f'page_{page_number}_state'] = 'technical_error'
                            break
                        if seller_catalogue and not seller_page_matches(search_url, page.url):
                            error = 'seller page redirected outside the approved seller catalogue'
                            break
                        if not seller_catalogue:
                            pre_geo_state, pre_geo_reason = classify_result_page(html, 0)
                            if pre_geo_state == 'blocked':
                                diagnostics[f'page_{page_number}_state'] = 'blocked'
                                error = f'blocked page {page_number}: {pre_geo_reason}'
                                break
                            geography = await verify_geography(page, self.source)
                            diagnostics[f'page_{page_number}_geography'] = geography
                            if geography['state'] != 'verified':
                                error = 'geography_unverified: ' + geography['reason']
                                break
                            if geography.get('corrected'):
                                html = await page.content()
                                diagnostics[f'page_{page_number}_final_url'] = page.url
                                diagnostics[f'page_{page_number}_evidence'] = await capture_page_evidence(
                                    page, source=self.source, search_url=search_url,
                                    page_number=page_number, evidence_dir=settings.evidence_dir)
                        parsed = self._extract(catalogue_html(html) if seller_catalogue else html, page_number,
                                               moscow_only=not seller_catalogue and '/moskva/' in urlsplit(search_url).path)
                        pagination = pagination_state(html, self.source.value)
                        diagnostics[f'page_{page_number}_pagination'] = pagination
                        if pagination['current'] is not None and pagination['current'] != page_number:
                            error = f'pagination mismatch: requested {page_number}, displayed {pagination["current"]}'
                            diagnostics[f'page_{page_number}_state'] = 'pagination_mismatch'
                            break
                        page_keys = frozenset(canonical_listing_key(self.source, hit.url) for hit in parsed)
                        diagnostics[f'page_{page_number}_listing_keys'] = sorted(page_keys)
                        if page_keys and page_keys in seen_pages:
                            error = 'pagination repeated a page; traversal is not proven'
                            diagnostics[f'page_{page_number}_state'] = 'pagination_repeat'
                            break
                        seen_pages.add(page_keys)
                        page_state, reason = classify_result_page(html, len(parsed))
                        card_evidence = 0
                        if page_state == 'results' and target_keys:
                            for hit in parsed:
                                if canonical_listing_key(self.source, hit.url) not in target_keys:
                                    continue
                                card_path = await capture_listing_card_evidence(
                                    page,
                                    source=self.source,
                                    search_url=search_url,
                                    page_number=page_number,
                                    hit=hit,
                                    evidence_dir=settings.evidence_dir,
                                )
                                if card_path:
                                    hit.raw['card_evidence'] = card_path
                                    card_evidence += 1
                        diagnostics[f'page_{page_number}'] = len(parsed)
                        diagnostics[f'page_{page_number}_state'] = page_state
                        pages_scanned += 1
                        candidate_hits = hits + parsed if page_state == 'results' else hits
                        targets_satisfied = all_target_listings_found(
                            self.source, target_keys, candidate_hits
                        )
                        self._progress(
                            'page_finished',
                            page=page_number,
                            pages_total=max_pages,
                            cards=len(parsed),
                            target_cards=card_evidence,
                            state=page_state,
                            evidence=evidence_path,
                            targets_satisfied=targets_satisfied,
                        )
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
                        if targets_satisfied:
                            diagnostics['completion_reason'] = 'all_target_listings_found'
                            diagnostics['target_keys_found'] = sorted(target_keys or ())
                            break
                        if pagination['last'] or (page_number == 1 and pagination['total'] is not None
                                                  and len(page_keys) == pagination['total'] and not pagination['has_next']):
                            exhausted = True
                            break
                    except Exception as exc:
                        error = f'page {page_number}: {type(exc).__name__}: {exc}'
                        diagnostics[f'page_{page_number}_state'] = 'technical_error'
                        self._progress(
                            'page_failed',
                            page=page_number,
                            pages_total=max_pages,
                            error=error,
                        )
                        break

        return ScanResult(
            filter_id='',
            page_count=pages_scanned,
            hits=hits,
            diagnostics=diagnostics,
            scanned_at=start,
            requested_pages=max_pages,
            complete=error is None and (
                pages_scanned == max_pages or exhausted or targets_satisfied
            ),
            exhausted=exhausted,
            error=error,
        )

    def _extract(self, html: str, page_number: int, *, moscow_only=False) -> list[ListingHit]:
        cards = primary_cards(html,
            '[data-marker^="item_list_with_filters/item"][data-item-id], '
            '[data-marker="catalog-serp"] article, article[data-marker="item"], '
            'div.js-catalog-item-enum[data-item-id], div[data-marker="item"]',
            root_selector='[data-marker="catalog-serp"]',
        )
        results: list[ListingHit] = []
        seen = set()
        position = 0
        for card in cards:
            link = card.select_one('a[data-marker="item-title"]') or card.select_one(
                'a[href*="/avtomobili/"]'
            )
            if not link or not link.get('href'):
                continue
            raw_url = link['href']
            if raw_url.startswith('//'):
                raw_url = 'https:' + raw_url
            if raw_url.startswith('/'):
                raw_url = 'https://www.avito.ru' + raw_url
            key = canonical_listing_key(self.source, raw_url)
            if not is_marketplace_listing_url(self.source, raw_url) or key in seen:
                continue
            seen.add(key)
            position += 1
            if moscow_only and not urlsplit(raw_url).path.startswith('/moskva/'):
                continue
            title = (
                card.get('data-item-name')
                or link.get_text(' ', strip=True)
                or card.get_text(' ', strip=True)[:255]
            )
            price_text = card.get_text(' ', strip=True)
            price_match = re.search(r'(?<!\d)(\d{1,3}(?:[\s\u00a0]\d{3})+)\s*₽', price_text)
            price = (
                float(re.sub(r'\D', '', price_match.group(1)))
                if price_match and re.sub(r'\D', '', price_match.group(1))
                else None
            )
            results.append(
                ListingHit(
                    external_id=raw_url,
                    title=title,
                    url=raw_url,
                    page_number=page_number,
                    position=position,
                    price=price,
                    raw={'raw_text': card.get_text(' ', strip=True), 'html': str(card)[:2048]},
                )
            )
        return results


def _page_url(base_url: str, page_number: int) -> str:
    if page_number <= 1:
        return base_url
    parts = urlsplit(base_url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != 'p']
    query.append(('p', str(page_number)))
    return urlunsplit(parts._replace(query=urlencode(query)))
