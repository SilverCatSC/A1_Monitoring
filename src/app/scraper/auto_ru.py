from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from app.config import settings
from app.models import EngineType
from app.scraper.base import (
    ListingHit,
    ScanResult,
    canonical_listing_key,
    capture_listing_card_evidence,
    capture_page_evidence,
    classify_result_page,
    evidence_manifest_name,
    is_marketplace_listing_url,
)
from app.scraper.browser_session import browser_page
from app.scraper.challenge_retry import operator_wait_seconds, refresh_explicit_captcha
from app.scraper.pacing import choose_pause
from app.scraper.result_scope import pagination_state, primary_cards
from app.scraper.seller import catalogue_html, seller_page_matches


class AutoRuAdapter:
    source = EngineType.AUTO_RU

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
        error: str | None = None
        diagnostics: dict[str, int | str] = {'engine': 'auto_ru', 'start_url': search_url}
        seen_pages: set[frozenset[str]] = set()
        catalogue_url = _list_url(search_url) if not seller_catalogue else search_url
        async with async_playwright() as p:
            async with browser_page(p) as page:
                for page_number in range(1, max_pages + 1):
                    url = _page_url(catalogue_url, page_number)
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
                            # Auto.ru can virtualise a short result list after a blind
                            # scroll and leave only the model landing content in the DOM.
                            # Read cards at the top first; target-card evidence scrolls
                            # only to the precise listing later in this method.
                            await page.evaluate('window.scrollTo(0, 0)')
                            await asyncio.sleep(settings.auto_ru_page_delay_seconds)
                        html = await page.content()
                        captcha_wait = operator_wait_seconds(self.source.value)
                        recovery = await refresh_explicit_captcha(
                            page, response, html, progress=self.progress_callback,
                            source=self.source.value, url=url,
                            wait_seconds=captcha_wait,
                            capture_challenge=(
                                lambda challenge_page, challenge_response, current_page=page_number: capture_page_evidence(
                                    challenge_page, source=self.source, search_url=search_url,
                                    page_number=current_page, evidence_dir=settings.evidence_dir,
                                    purpose='captcha_challenge', final_url=challenge_page.url,
                                    http_status=challenge_response.status if challenge_response else None,
                                )
                            ) if captcha_wait else None,
                        )
                        response, html = recovery.response, recovery.html
                        if recovery.refreshes:
                            http_status = response.status if response is not None else None
                            diagnostics[f'page_{page_number}_captcha_refreshes'] = recovery.refreshes
                            diagnostics[f'page_{page_number}_captcha_outcome'] = recovery.outcome
                            diagnostics[f'page_{page_number}_http_status'] = http_status or 0
                            if recovery.challenge_evidence:
                                diagnostics[f'page_{page_number}_captcha_evidence'] = recovery.challenge_evidence
                                diagnostics[f'page_{page_number}_captcha_evidence_manifest'] = evidence_manifest_name(
                                    recovery.challenge_evidence
                                )
                        diagnostics[f'page_{page_number}_requested_url'] = url
                        diagnostics[f'page_{page_number}_final_url'] = page.url
                        evidence_path = await capture_page_evidence(
                            page,
                            source=self.source,
                            search_url=search_url,
                            page_number=page_number,
                            evidence_dir=settings.evidence_dir,
                            purpose='search_page',
                            final_url=page.url,
                            http_status=http_status,
                        )
                        if evidence_path:
                            diagnostics[f'page_{page_number}_evidence'] = evidence_path
                            diagnostics[f'page_{page_number}_evidence_manifest'] = evidence_manifest_name(
                                evidence_path
                            )
                        else:
                            error = f'evidence capture failed page {page_number}'
                            diagnostics[f'page_{page_number}_state'] = 'evidence_missing'
                            break
                        if recovery.outcome in {'unresolved', 'operator_timeout', 'wrong_destination',
                                                'challenge_evidence_missing', 'no_document_response'}:
                            reason = ('no document response' if recovery.outcome == 'no_document_response'
                                      else f'CAPTCHA {recovery.outcome}')
                            error = f'blocked page {page_number}: {reason}'
                            diagnostics[f'page_{page_number}_state'] = 'blocked'
                            break
                        if http_status is not None and http_status >= 400:
                            error = f'page {page_number}: RuntimeError: HTTP {http_status}'
                            diagnostics[f'page_{page_number}_state'] = 'technical_error'
                            break
                        if seller_catalogue and not seller_page_matches(search_url, page.url):
                            error = 'seller page redirected outside the approved seller catalogue'
                            break
                        parsed = self._extract(catalogue_html(html) if seller_catalogue else html, page_number)
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
                        declared_offers = _declared_offer_count(html)
                        if declared_offers is not None:
                            diagnostics[f'page_{page_number}_declared_offers'] = declared_offers
                        card_evidence = 0
                        missing_card_evidence: list[str] = []
                        if page_state == 'results' and target_keys:
                            for hit in parsed:
                                key = canonical_listing_key(self.source, hit.url)
                                if key not in target_keys:
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
                                    hit.raw['card_evidence_manifest'] = evidence_manifest_name(card_path)
                                    card_evidence += 1
                                elif key:
                                    missing_card_evidence.append(key)
                        if missing_card_evidence:
                            error = (
                                f'listing-card evidence capture failed page {page_number}: '
                                + ', '.join(sorted(missing_card_evidence))
                            )
                            diagnostics[f'page_{page_number}_state'] = 'evidence_missing'
                            break
                        diagnostics[f'page_{page_number}'] = len(parsed)
                        diagnostics[f'page_{page_number}_state'] = page_state
                        pages_scanned += 1
                        final_result_page = pagination['last'] or (not pagination['has_next'] and _all_offers_are_visible(declared_offers, len(parsed)))
                        self._progress(
                            'page_finished',
                            page=page_number,
                            pages_total=max_pages,
                            cards=len(parsed),
                            target_cards=card_evidence,
                            state=page_state,
                            evidence=evidence_path,
                            exhausted=final_result_page,
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
                        if final_result_page:
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
            complete=error is None and (pages_scanned == max_pages or exhausted),
            exhausted=exhausted,
            error=error,
        )

    def _extract(self, html: str, page_number: int) -> list[ListingHit]:
        results: list[ListingHit] = []
        seen: set[str] = set()
        cards = []
        for candidate in primary_cards(html, '.ListingItem, .OfferSnippet, [class*="ListingItemUniversal-"]',
                                       root_selector='.ListingCars__items, .CardGroupOffersList__items'):
            classes = candidate.get('class') or []
            if any(
                class_name in {'ListingItem', 'OfferSnippet'}
                or (class_name.startswith('ListingItemUniversal-') and '__' not in class_name)
                for class_name in classes
            ):
                cards.append(candidate)
        for card in cards:
            links = card.select('a[href]')
            ranked_links = sorted(
                links,
                key=lambda item: (
                    'ListingItemTitle__link' not in (item.get('class') or []),
                    'фото' in item.get_text(' ', strip=True).lower(),
                ),
            )
            link = None
            raw_url = ''
            for candidate in ranked_links:
                candidate_url = candidate.get('href') or ''
                if candidate_url.startswith('//'):
                    candidate_url = 'https:' + candidate_url
                if candidate_url.startswith('/'):
                    candidate_url = 'https://auto.ru' + candidate_url
                if is_marketplace_listing_url(EngineType.AUTO_RU, candidate_url):
                    link, raw_url = candidate, candidate_url
                    break
            if link is None:
                continue
            key = canonical_listing_key(EngineType.AUTO_RU, raw_url)
            if not key or key in seen:
                continue
            seen.add(key)
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
    parts = urlsplit(base_url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != 'page']
    query.append(('page', str(page_number)))
    return urlunsplit(parts._replace(query=urlencode(query)))


def _list_url(base_url: str) -> str:
    """Request the catalogue view instead of Auto.ru's model landing page."""
    parts = urlsplit(base_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    output_type_seen = False
    normalized: list[tuple[str, str]] = []
    for key, value in query:
        if key == 'output_type':
            if not output_type_seen:
                normalized.append((key, 'list'))
                output_type_seen = True
            continue
        normalized.append((key, value))
    if not output_type_seen:
        normalized.append(('output_type', 'list'))
    return urlunsplit(parts._replace(query=urlencode(normalized)))


def _declared_offer_count(html: str) -> int | None:
    """Read the result-count label Auto.ru displays above a short catalogue."""
    soup = BeautifulSoup(html, 'html.parser')
    active_radius_count = soup.select_one(
        '.ListingGeoRadiusCounters__item_active .ListingGeoRadiusCounters__itemCount'
    )
    if active_radius_count:
        text = active_radius_count.get_text(' ', strip=True)
        match = re.search(r'(?<!\d)(\d{1,5})\s+предложени(?:е|я|й)\b', text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    text = soup.get_text(' ', strip=True)
    match = re.search(r'(?<!\d)(\d{1,5})\s+предложени(?:е|я|й)\b', text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _all_offers_are_visible(declared_offers: int | None, parsed_count: int) -> bool:
    return declared_offers is not None and declared_offers > 0 and parsed_count == declared_offers
