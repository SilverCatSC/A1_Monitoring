from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from contextlib import AsyncExitStack
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
from app.scraper.browser_session import browser_page, read_page_content
from app.scraper.challenge_retry import (
    autoru_catalogue_subset,
    operator_wait_seconds,
    refresh_explicit_captcha,
)
from app.scraper.lazy_load import LazyLoadBlocked, load_more_results
from app.scraper.navigation_recovery import (
    recoverable_navigation_error,
    wait_for_navigation_recovery,
    watch_main_frame_documents,
)
from app.scraper.pacing import choose_pause
from app.scraper.result_scope import SUPPLEMENT_HEADING, pagination_next_url, pagination_state, primary_cards
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
        max_pages: int = 5,
        target_keys: set[str] | None = None,
        seller_catalogue: bool = False,
        evidence_keys: set[str] | None = None,
    ) -> ScanResult:
        result = await self._scan_filter(search_url, max_pages, target_keys,
                                         seller_catalogue, evidence_keys)
        condition_url = result.diagnostics.get('catalogue_condition_url')
        scope_error = 'Dealer all-inventory route redirected to one condition; full inventory coverage unproven'
        remaining = max_pages - result.diagnostics.get('load_actions', result.page_count)
        if (not seller_catalogue or result.error != scope_error or not result.exhausted
                or remaining < 1 or not autoru_catalogue_subset(search_url, condition_url or '')):
            return result
        # Auto.ru's all route may select only used/new. Cover the complementary
        # condition explicitly; never treat the redirected subset as inventory.
        parts = urlsplit(search_url)
        condition = 'new' if '/used/' in urlsplit(condition_url).path else 'used'
        complement = urlunsplit(parts._replace(path=parts.path.replace('/all/', f'/{condition}/')))
        self._progress('catalogue_complement_started', url=complement)
        other = await self._scan_filter(complement, remaining, None, True, evidence_keys)
        offset = result.page_count
        for hit in result.hits:
            hit.raw['page_evidence'] = result.diagnostics.get(f'page_{hit.page_number}_evidence')
            hit.raw['page_evidence_manifest'] = result.diagnostics.get(f'page_{hit.page_number}_evidence_manifest')
        seen = {canonical_listing_key(self.source, hit.url) for hit in result.hits}
        for hit in other.hits:
            hit.raw['page_evidence'] = other.diagnostics.get(f'page_{hit.page_number}_evidence')
            hit.raw['page_evidence_manifest'] = other.diagnostics.get(f'page_{hit.page_number}_evidence_manifest')
            hit.page_number += offset
            key = canonical_listing_key(self.source, hit.url)
            if key not in seen:
                result.hits.append(hit)
                seen.add(key)
        result.diagnostics['condition_runs'] = [
            {'url': condition_url, 'exhausted': result.exhausted, 'pages': offset},
            {'url': complement, 'exhausted': other.exhausted, 'complete': other.complete,
             'error': other.error, 'pages': other.page_count},
        ]
        for key, value in other.diagnostics.items():
            match = re.match(r'page_(\d+)(.*)', key)
            if match:
                result.diagnostics[f'page_{int(match[1]) + offset}{match[2]}'] = value
        result.diagnostics['load_actions'] = max_pages - remaining + other.diagnostics.get('load_actions', other.page_count)
        result.page_count += other.page_count
        result.complete = result.exhausted = bool(other.complete and other.exhausted)
        result.diagnostics['catalogue_scope_recovered'] = result.complete
        result.error = None if result.complete else other.error or scope_error
        return result

    async def _scan_filter(
        self,
        search_url: str,
        max_pages: int = 5,
        target_keys: set[str] | None = None,
        seller_catalogue: bool = False,
        evidence_keys: set[str] | None = None,
    ) -> ScanResult:
        start = datetime.now(UTC)
        hits: list[ListingHit] = []
        pages_scanned = 0
        load_actions = 0
        exhausted = False
        targets_complete = False
        error: str | None = None
        diagnostics: dict[str, int | str] = {'engine': 'auto_ru', 'start_url': search_url}
        seen_pages: set[frozenset[str]] = set()
        catalogue_url = _list_url(search_url) if not seller_catalogue else search_url
        async with async_playwright() as p:
            async with AsyncExitStack() as browser_pages:
                async def reopen():
                    return await browser_pages.enter_async_context(browser_page(p, source=self.source.value))

                page_number = 1
                resumed_response = None
                try:
                    page = await reopen()
                except Exception as exc:
                    if not operator_wait_seconds(self.source.value) or not recoverable_navigation_error(exc):
                        raise
                    page, resumed_response = await wait_for_navigation_recovery(
                        None, url=catalogue_url, reopen=reopen, progress=self.progress_callback,
                        page_number=page_number, request_timeout=self.request_timeout,
                        source=self.source.value,
                    )
                while page_number <= max_pages:
                    if load_actions >= max_pages:
                        diagnostics['load_budget_exhausted'] = True
                        error = 'lazy-load budget reached before full coverage; traversal incomplete'
                        break
                    url = catalogue_url if seller_catalogue else _page_url(catalogue_url, page_number)
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
                    checkpoint = (len(hits), pages_scanned, load_actions, seen_pages.copy())
                    # Main-frame request/response tracking must start before the
                    # goto: a fresh document that arrives while a destroyed
                    # execution context is still propagating stays visible to
                    # the recovery wait instead of hanging an open page.
                    document = watch_main_frame_documents(page)
                    try:
                        load_actions += 1
                        diagnostics['load_actions'] = load_actions
                        if resumed_response is not None:
                            if document.request is None and document.response is None:
                                document.response = resumed_response
                            response, resumed_response = resumed_response, None
                        else:
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
                        html = await read_page_content(page)
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
                        if recovery.outcome != 'not_needed':
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
                            if autoru_catalogue_subset(search_url, page.url):
                                diagnostics['catalogue_scope_narrowed'] = True
                                diagnostics['catalogue_condition_url'] = page.url
                            elif _condition_redirected(search_url, page.url):
                                error = 'Requested dealer condition redirected to another condition; inventory coverage unproven'
                                diagnostics[f'page_{page_number}_state'] = 'condition_redirect'
                                break
                            else:
                                error = 'seller page redirected outside the approved seller catalogue'
                                break
                        # A documented next-page control is the provable
                        # continuation of this catalogue: while it exists, the
                        # remaining document slots of the shared ceiling are
                        # reserved for pagination and load-more batches run
                        # only with actions pagination cannot use.
                        pagination_before = pagination_state(html, self.source.value)
                        reserved_pages = (
                            max_pages - page_number
                            if pagination_before['has_next'] and page_number < max_pages else 0
                        )
                        if reserved_pages:
                            diagnostics[f'page_{page_number}_lazy_budget_reserved'] = reserved_pages
                        lazy = await load_more_results(
                            page, response, html, source=self.source, requested_url=url,
                            search_url=search_url, page_number=page_number,
                            extract=lambda body, current_page=page_number: self._extract(catalogue_html(body) if seller_catalogue else body, current_page),
                            root_selector='.ListingCars__items, .CardGroupOffersList__items', card_selector='.ListingItem, .OfferSnippet, [class*="ListingItemUniversal-"]',
                            declared_total=_declared_offer_count(html),
                            target_keys=(set(target_keys) - {canonical_listing_key(self.source, hit.url) for hit in hits}) if target_keys else None,
                            progress=self.progress_callback,
                            batch_budget=max(0, max_pages - load_actions - reserved_pages),
                        )
                        load_actions += lazy.batches
                        diagnostics['load_actions'] = load_actions
                        response, html = lazy.response, lazy.html
                        http_status = response.status if response is not None else None
                        diagnostics[f'page_{page_number}_lazy_batches'] = lazy.batches
                        diagnostics[f'page_{page_number}_lazy_outcome'] = lazy.outcome
                        for lazy_recovery in lazy.recoveries:
                            diagnostics[f'page_{page_number}_captcha_outcome'] = lazy_recovery.outcome
                            if lazy_recovery.challenge_evidence:
                                diagnostics[f'page_{page_number}_captcha_evidence'] = lazy_recovery.challenge_evidence
                                diagnostics[f'page_{page_number}_captcha_evidence_manifest'] = evidence_manifest_name(lazy_recovery.challenge_evidence)
                        if lazy.batches:
                            evidence_path = await capture_page_evidence(
                                page, source=self.source, search_url=search_url,
                                page_number=page_number, evidence_dir=settings.evidence_dir,
                                purpose='search_page', final_url=page.url, http_status=http_status,
                            )
                            if not evidence_path:
                                raise RuntimeError('lazy-load page evidence missing')
                            diagnostics[f'page_{page_number}_evidence'] = evidence_path
                            diagnostics[f'page_{page_number}_evidence_manifest'] = evidence_manifest_name(evidence_path)
                        diagnostics[f'page_{page_number}_http_status'] = http_status or 0
                        parsed = lazy.hits
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
                        if (page_state == 'unrecognized' and not seller_catalogue
                                and '/group/' in urlsplit(search_url).path
                                and _preorder_only(html, len(parsed))):
                            page_state, reason = 'empty', 'preorder_only_no_primary_offers'
                            diagnostics[f'page_{page_number}_empty_basis'] = reason
                        declared_offers = _declared_offer_count(html)
                        if declared_offers is not None:
                            diagnostics[f'page_{page_number}_declared_offers'] = declared_offers
                        card_evidence = 0
                        missing_card_evidence: list[str] = []
                        capture_keys = target_keys if evidence_keys is None else evidence_keys
                        if page_state == 'results' and capture_keys:
                            for hit in parsed:
                                key = canonical_listing_key(self.source, hit.url)
                                if key not in capture_keys:
                                    continue
                                card_path = hit.raw.get('card_evidence') or await capture_listing_card_evidence(
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
                        targets_complete = bool(target_keys and set(target_keys) <= {
                            canonical_listing_key(self.source, hit.url) for hit in hits
                        })
                        if targets_complete:
                            diagnostics['target_coverage_complete'] = True
                            break
                        if seller_catalogue and page_number < max_pages:
                            next_url = pagination_next_url(html, self.source.value, page.url)
                            if not next_url:
                                diagnostics['catalogue_navigation_unproven'] = True
                                error = 'Catalogue end not proven; no observed next-page link'
                                break
                            if not seller_page_matches(page.url, next_url):
                                diagnostics['catalogue_navigation_unproven'] = True
                                error = 'Catalogue next-page link leaves the seller catalogue'
                                break
                            catalogue_url = next_url
                    except LazyLoadBlocked as exc:
                        # A marketplace denial during the load-more wait halts
                        # this source, exactly like a blocked primary page.
                        error = f'page {page_number}: {type(exc).__name__}: {exc}'
                        diagnostics[f'page_{page_number}_state'] = 'blocked'
                        diagnostics[f'page_{page_number}_block_reason'] = str(exc)
                        if exc.captcha_outcome:
                            diagnostics[f'page_{page_number}_captcha_outcome'] = exc.captcha_outcome
                            diagnostics[f'page_{page_number}_captcha_refreshes'] = exc.captcha_refreshes
                        self._progress(
                            'page_failed',
                            page=page_number,
                            pages_total=max_pages,
                            error=error,
                        )
                        break
                    except Exception as exc:
                        if seller_catalogue and _condition_redirected(search_url, page.url):
                            # A successful redirect to the other inventory tab is
                            # a scope failure, not a CAPTCHA or an unfinished
                            # document. Waiting for the operator cannot repair it.
                            error = 'Requested dealer condition redirected to another condition; inventory coverage unproven'
                            diagnostics[f'page_{page_number}_state'] = 'condition_redirect'
                            diagnostics[f'page_{page_number}_final_url'] = page.url
                            break
                        if operator_wait_seconds(self.source.value) and recoverable_navigation_error(exc):
                            # A recoverable navigation failure at the goto, scroll
                            # or read boundary keeps this scan on the failed page
                            # until the operator restores a fresh document, which
                            # is then processed here instead of a new goto.
                            hit_count, pages_scanned, load_actions, seen_pages = checkpoint
                            del hits[hit_count:]
                            diagnostics['load_actions'] = load_actions
                            self._progress('navigation_operator_required', page=page_number, url=url,
                                           error=f'{type(exc).__name__}: {exc}')
                            try:
                                page, resumed_response = await wait_for_navigation_recovery(
                                    page, url=url, reopen=reopen, progress=self.progress_callback,
                                    page_number=page_number, request_timeout=self.request_timeout,
                                    source=self.source.value, document_watcher=document,
                                    reject_destination=_condition_redirected if seller_catalogue else None,
                                )
                            except Exception as recovery_exc:
                                exc = recovery_exc
                            else:
                                continue
                        error = f'page {page_number}: {type(exc).__name__}: {exc}'
                        diagnostics[f'page_{page_number}_state'] = 'technical_error'
                        self._progress(
                            'page_failed',
                            page=page_number,
                            pages_total=max_pages,
                            error=error,
                        )
                        break
                    finally:
                        document.detach()
                    page_number += 1

        if diagnostics.get('catalogue_scope_narrowed') and error is None:
            error = 'Dealer all-inventory route redirected to one condition; full inventory coverage unproven'
        return ScanResult(
            filter_id='',
            page_count=pages_scanned,
            hits=hits,
            diagnostics=diagnostics,
            scanned_at=start,
            requested_pages=max_pages,
            complete=error is None and (pages_scanned == max_pages or exhausted or targets_complete),
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


def _condition_redirected(requested_url: str, final_url: str) -> bool:
    parts = urlsplit(requested_url)
    match = re.fullmatch(r'/diler/(cars|lcv)/(new|used)/([^/]+)/?', parts.path)
    if not match or seller_page_matches(requested_url, final_url):
        return False
    all_url = urlunsplit(parts._replace(path=parts.path.replace(f'/{match[2]}/', '/all/')))
    return autoru_catalogue_subset(all_url, final_url)


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


_SHOW_MORE_COUNT = re.compile(r'Показать\s+(\d[\d\s\u00a0]*)\s+предложени(?:е|я|й)\b', re.IGNORECASE)
_HIDDEN_STYLE = re.compile(r'display\s*:\s*none|visibility\s*:\s*hidden', re.IGNORECASE)


def _preorder_only(html: str, parsed_count: int) -> bool:
    if parsed_count:
        return False
    soup = BeautifulSoup(catalogue_html(html), 'html.parser')
    for node in reversed(soup.select('template, script, style, [hidden], [aria-hidden="true"]')):
        node.decompose()
    for node in reversed(soup.find_all(style=_HIDDEN_STYLE)):
        node.decompose()
    text = soup.get_text(' ', strip=True)
    headings = [n.get_text(' ', strip=True) for n in soup.select(
        'h2,h3,[role="heading"], [class*="EmptyGroupResetFilters__title"], '
        '[class*="EmptyGroupRelated__title"]')]
    return (any(re.fullmatch(r'Доступен предзаказ', h, re.I) for h in headings)
            and 'Дилеры с предзаказом' in text
            and any(re.search(r'похожие на этот', h, re.I) for h in headings))


def _declared_offer_count(html: str) -> int | None:
    """Read the result-count label Auto.ru displays above a short catalogue."""
    soup = BeautifulSoup(html, 'html.parser')

    def invisible(node):
        return (node.name in {'template', 'noscript', 'script', 'style'} or 'hidden' in node.attrs
                or node.get('aria-hidden') == 'true'
                or bool(_HIDDEN_STYLE.search(node.get('style') or '')))

    # Clean before *all* counters, including the active radius counter.
    for node in reversed(list(soup.find_all(invisible))):
        node.decompose()
    for node in reversed(soup.select('[data-marker*="recommend"], [class*="Recommendations"], '
                                     '[class*="RelatedOffers"]')):
        node.decompose()
    # Model/group pages have a result heading outside the concrete-card root.
    # Their sidebar's "show N offers" button describes another model. Read the
    # primary heading before supplemental results, rather than mixing counters.
    heading_counts = set()
    for node in soup.select('h2, h3, [role="heading"]'):
        label = ' '.join(node.get_text(' ', strip=True).split())
        if SUPPLEMENT_HEADING.search(label):
            # Sidebars must not end traversal of the main result column.
            if node.find_parent('aside') is None and not re.fullmatch(r'Похожие автомобили', label, re.I):
                break
            continue
        match = re.fullmatch(r'(\d[\d\s\u00a0]*)\s+предложени(?:е|я|й)(?:\s+от\s+.+)?', label, re.I)
        if match:
            heading_counts.add(int(re.sub(r'\D', '', match[1])))
        elif re.fullmatch(r'Одно предложение', label, re.I):
            heading_counts.add(1)
    active_radius_count = soup.select_one(
        '.ListingGeoRadiusCounters__item_active .ListingGeoRadiusCounters__itemCount'
    )
    if active_radius_count:
        text = active_radius_count.get_text(' ', strip=True)
        match = re.search(r'(?<!\d)(\d{1,5})\s+предложени(?:е|я|й)\b', text, re.IGNORECASE)
        if match:
            return int(match.group(1))

    if heading_counts:
        return heading_counts.pop() if len(heading_counts) == 1 else None

    def button_counts(scope):
        return [
            int(re.sub(r'\D', '', match.group(1)))
            for node in scope.select('button, a, [role="button"], .Button')
            for match in [_SHOW_MORE_COUNT.search(node.get_text(' ', strip=True))] if match
        ]

    # The primary list's own control is authoritative. Outside the primary
    # roots a single remaining value is accepted; several disagreeing values
    # mean the count is ambiguous and no exhausted claim may rely on it.
    root_counts = [
        count
        for root in soup.select('.ListingCars__items, .CardGroupOffersList__items')
        for count in button_counts(root)
    ]
    for counts in (root_counts, button_counts(soup)):
        unique = sorted(set(counts))
        if len(unique) == 1:
            return unique[0]
        if unique:
            return None
    for node in soup.select('aside'):
        node.decompose()
    counts = {
        int(match.group(1)) for match in re.finditer(
            r'(?<!\d)(\d{1,5})\s+предложени(?:е|я|й)\b(?!\s+дня\b)',
            soup.get_text(' ', strip=True), re.IGNORECASE,
        )
    }
    return counts.pop() if len(counts) == 1 else None


def _all_offers_are_visible(declared_offers: int | None, parsed_count: int) -> bool:
    return declared_offers is not None and declared_offers > 0 and parsed_count == declared_offers
