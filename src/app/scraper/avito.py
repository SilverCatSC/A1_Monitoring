from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
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
from app.scraper.challenge_retry import operator_wait_seconds, refresh_explicit_captcha
from app.scraper.lazy_load import LazyLoadBlocked, load_more_results
from app.scraper.navigation_recovery import (
    recoverable_navigation_error,
    wait_for_navigation_recovery,
    watch_main_frame_documents,
)
from app.scraper.pacing import choose_pause
from app.scraper.result_scope import (
    avito_active_catalogue_url,
    avito_catalogue_result_count,
    avito_catalogue_show_all_url,
    avito_model_search_scope_problem,
    pagination_next_url,
    pagination_state,
    primary_cards,
    seller_catalogue_counters,
)
from app.scraper.seller import catalogue_html, seller_page_matches, source_challenged

AVITO_CARD_SELECTOR = (
    '[data-marker^="item_list_with_filters/item"][data-item-id], '
    '[data-marker="catalog-serp"] article, article[data-marker="item"], '
    'div.js-catalog-item-enum[data-item-id], div[data-marker="item"]'
)
AVITO_LATE_RENDER_WAIT_SECONDS = 6


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

    async def _wait_for_recovered_results(self, page, page_number, *, seller_catalogue=False,
                                          catalogue_boundary_url=None):
        """Wait for the redirected list, without treating a timer as absence."""
        await page.mouse.wheel(0, 1600)
        await asyncio.sleep(settings.avito_page_delay_seconds)
        previous = None
        next_notice = 0
        loop = asyncio.get_running_loop()
        while True:
            html = await read_page_content(page)
            body = catalogue_html(html) if seller_catalogue else html
            hits = self._extract(body, page_number)
            state, _ = classify_result_page(body, len(hits))
            if state in {'empty', 'blocked'}:
                return html
            signature = tuple(hit.external_id or hit.url for hit in hits)
            ready = state == 'results'
            # A dealer preview may intentionally contain no advert DOM cards.
            # Let the normal, boundary-checked Show all path expand it before
            # requiring result cards. A counter alone never proves coverage.
            if (seller_catalogue and catalogue_boundary_url
                    and seller_page_matches(catalogue_boundary_url, page.url)
                    and avito_active_catalogue_url(page.url)):
                show_all = avito_catalogue_show_all_url(html, page.url)
                counters = seller_catalogue_counters(html, page.url) or {}
                if show_all and isinstance(counters.get('active'), int):
                    signature = ('dealer_preview', show_all, counters['active'])
                    ready = True
            if ready and signature == previous:
                return html
            previous = signature if ready else None
            if loop.time() >= next_notice:
                self._progress('page_wait', page=page_number,
                               wait_reason='post_captcha_rendering', wait_seconds=1)
                next_notice = loop.time() + 30
            await asyncio.sleep(1)

    async def scan_filter(
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
        diagnostics = {'engine': 'avito', 'start_url': search_url}
        seen_pages: set[frozenset[str]] = set()
        catalogue_url = search_url
        catalogue_boundary_url = search_url
        expanded_catalogue = False
        catalogue_counters = None
        counter_conflict = False

        def observe_counters(body, current_url):
            nonlocal catalogue_counters, counter_conflict
            observed = seller_catalogue_counters(catalogue_html(body), current_url)
            result_count = avito_catalogue_result_count(catalogue_html(body), current_url)
            diagnostics['seller_primary_result_count'] = result_count
            if result_count is not None:
                if observed and observed.get('active') is not None and observed['active'] != result_count:
                    counter_conflict = True
                elif not observed or observed.get('active') is None:
                    observed = {'active': result_count, 'completed': None, 'basis': 'primary_result_count'}
            if observed and observed.get('active') is not None:
                if catalogue_counters and observed['active'] != catalogue_counters['active']:
                    counter_conflict = True
                catalogue_counters = observed
            diagnostics['seller_active_counter'] = (catalogue_counters or {}).get('active')
            diagnostics['seller_completed_counter'] = (catalogue_counters or {}).get('completed')
            diagnostics['seller_counter_conflict'] = counter_conflict

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
                        None, url=search_url, reopen=reopen, progress=self.progress_callback,
                        page_number=page_number, request_timeout=self.request_timeout,
                    )
                while page_number <= max_pages:
                    if load_actions >= max_pages:
                        diagnostics['load_budget_exhausted'] = True
                        error = 'lazy-load budget reached before full coverage; traversal incomplete'
                        break
                    url = catalogue_url if seller_catalogue else _page_url(search_url, page_number)
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
                        if http_status in {403, 429} and operator_wait_seconds(self.source.value):
                            # Rate-limit pages may contain no recognisable CAPTCHA.
                            # Keep this page/filter pending instead of marking the
                            # entire remaining source as abandoned. Recovery requires
                            # a fresh main document, never an XHR or stale HTTP 200.
                            page, response = await wait_for_navigation_recovery(
                                page, url=url, reopen=reopen, progress=self.progress_callback,
                                page_number=page_number, request_timeout=self.request_timeout,
                                document_watcher=document,
                            )
                            http_status = response.status if response is not None else None
                        diagnostics[f'page_{page_number}_http_status'] = http_status or 0
                        if http_status is None or http_status < 400:
                            await page.mouse.wheel(0, 1600)
                            await asyncio.sleep(settings.avito_page_delay_seconds)
                            # Avito can render the result list after the initial page
                            # delay.  Wait once for a recognised card before declaring
                            # a parser uncertainty, while keeping an explicit bounded
                            # timeout for true empty or changed pages.
                            if await page.locator(AVITO_CARD_SELECTOR).count() == 0:
                                try:
                                    await page.wait_for_selector(
                                        AVITO_CARD_SELECTOR,
                                        state='attached',
                                        timeout=AVITO_LATE_RENDER_WAIT_SECONDS * 1000,
                                    )
                                except PlaywrightTimeoutError:
                                    pass
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
                        if recovery.outcome in {'resolved_by_operator', 'cleared_by_refresh'}:
                            # The gate's document redirect is not proof that the
                            # asynchronous result list has finished rendering.
                            # Stay on this page and trigger its native lazy rendering.
                            latest_response = response

                            def recovered_document(candidate, current_page=page):
                                nonlocal latest_response
                                if (candidate.request.is_navigation_request()
                                        and candidate.request.frame == current_page.main_frame):
                                    latest_response = candidate

                            page.on('response', recovered_document)
                            try:
                                while True:
                                    html = await self._wait_for_recovered_results(
                                        page, page_number, seller_catalogue=seller_catalogue,
                                        catalogue_boundary_url=catalogue_boundary_url)
                                    response = latest_response
                                    repeated = await refresh_explicit_captcha(
                                        page, response, html, progress=self.progress_callback,
                                        source=self.source.value, url=url,
                                        wait_seconds=captcha_wait,
                                        capture_challenge=(lambda p, r, current_page=page_number: capture_page_evidence(
                                            p, source=self.source, search_url=search_url,
                                            page_number=current_page, evidence_dir=settings.evidence_dir,
                                            purpose='captcha_challenge', final_url=p.url,
                                            http_status=r.status if r else None,
                                        )) if captcha_wait else None,
                                    )
                                    response, html = repeated.response, repeated.html
                                    latest_response = response
                                    if repeated.outcome == 'not_needed':
                                        break
                                    recovery = repeated
                                    if repeated.outcome not in {'resolved_by_operator', 'cleared_by_refresh'}:
                                        break
                            finally:
                                page.remove_listener('response', recovered_document)
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
                            diagnostics[f'page_{page_number}_state'] = (
                                'blocked' if source_challenged(error, {}) else 'technical_error'
                            )
                            break
                        if not seller_catalogue:
                            scope_problem = avito_model_search_scope_problem(search_url, page.url)
                            if scope_problem:
                                error = f'search filter scope lost page {page_number}: {scope_problem}'
                                diagnostics[f'page_{page_number}_state'] = 'filter_scope_mismatch'
                                # Retain inspectable provisional data without treating
                                # an unverified scope as a valid search observation.
                                diagnostics[f'page_{page_number}_rejected_scope_snapshot'] = {
                                    'pagination': pagination_state(html, self.source.value),
                                    'provisional_hits': [
                                        {'url': hit.url, 'title': hit.title, 'price': hit.price,
                                         'page_number': hit.page_number, 'position': hit.position}
                                        for hit in self._extract(html, page_number, moscow_only=True)
                                    ],
                                }
                                break
                        if seller_catalogue and not seller_page_matches(catalogue_boundary_url, page.url):
                            if expanded_catalogue and seller_page_matches(search_url, page.url):
                                catalogue_boundary_url = page.url
                            else:
                                error = 'seller page redirected outside the approved seller catalogue'
                                break
                        if seller_catalogue and not avito_active_catalogue_url(page.url):
                            error = 'seller page redirected outside the approved active inventory'
                            break
                        if seller_catalogue and not expanded_catalogue:
                            observe_counters(html, page.url)
                            show_all_url = avito_catalogue_show_all_url(html, page.url)
                            if show_all_url:
                                diagnostics['catalogue_preview_evidence'] = evidence_path
                                diagnostics['catalogue_show_all_url'] = show_all_url
                                catalogue_url = catalogue_boundary_url = show_all_url
                                expanded_catalogue = True
                                self._progress('catalogue_show_all', url=show_all_url)
                                continue
                        if seller_catalogue:
                            observe_counters(html, page.url)
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
                            root_selector='[data-marker="catalog-serp"]', card_selector=AVITO_CARD_SELECTOR,
                            declared_total=((catalogue_counters or {}).get('active')
                                            if seller_catalogue else pagination_before['total']),
                            target_keys=(set(target_keys) - {canonical_listing_key(self.source, hit.url) for hit in hits}) if target_keys and not seller_catalogue else None,
                            progress=self.progress_callback,
                            batch_budget=max(0, max_pages - load_actions - reserved_pages),
                            seller_catalogue=seller_catalogue,
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
                        if lazy.batches or lazy.outcome == 'pagination_revealed':
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

                        # A Moscow query can legitimately paginate into offers from
                        # other cities.  Classify the response from all visible cards;
                        # apply the city scope only to the returned monitoring hits.
                        parsed_all = lazy.hits
                        moscow_only = not seller_catalogue and '/moskva/' in urlsplit(search_url).path
                        parsed = (
                            [hit for hit in parsed_all if _is_moscow_listing_url(hit.url)]
                            if moscow_only
                            else parsed_all
                        )
                        pagination = pagination_state(html, self.source.value)
                        if seller_catalogue:
                            observe_counters(html, page.url)
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
                        diagnostics[f'page_{page_number}_visible_cards'] = len(parsed_all)
                        page_state, reason = classify_result_page(html, len(parsed_all))
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
                        self._progress(
                            'page_finished',
                            page=page_number,
                            pages_total=max_pages,
                            cards=len(parsed),
                            target_cards=card_evidence,
                            state=page_state,
                            evidence=evidence_path,
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
                        if seller_catalogue:
                            # Page boundaries can overlap (e.g. 24 + 6 cards
                            # with one repeated publication). Keep the inventory
                            # and downstream candidate counts unique.
                            collected = {canonical_listing_key(self.source, hit.url) for hit in hits}
                            hits.extend(hit for hit in parsed
                                        if canonical_listing_key(self.source, hit.url) not in collected)
                        else:
                            hits.extend(parsed)
                        unique_keys = {canonical_listing_key(self.source, hit.url) for hit in hits}
                        declared_active = (catalogue_counters or {}).get('active')
                        if seller_catalogue and counter_conflict:
                            error = 'Catalogue active counter changed during traversal; coverage must be rechecked'
                            break
                        counter_incomplete = (seller_catalogue and declared_active is not None
                                              and len(unique_keys) != declared_active)
                        if not counter_incomplete and (pagination['last'] or (page_number == 1 and pagination['total'] is not None
                                                  and len(page_keys) == pagination['total'] and not pagination['has_next'])):
                            if seller_catalogue:
                                diagnostics['catalogue_end_proven_by'] = (
                                    'seller_active_counter' if declared_active is not None else 'pagination_last')
                            exhausted = True
                            break
                        targets_complete = bool(not seller_catalogue and target_keys and set(target_keys) <= {
                            canonical_listing_key(self.source, hit.url) for hit in hits
                        })
                        if targets_complete:
                            diagnostics['target_coverage_complete'] = True
                            break
                        if seller_catalogue:
                            next_url = pagination_next_url(html, self.source.value, page.url)
                            if next_url and page_number < max_pages:
                                if not seller_page_matches(catalogue_boundary_url, next_url):
                                    diagnostics['catalogue_navigation_unproven'] = True
                                    error = 'Catalogue next-page link leaves the seller catalogue'
                                    break
                                catalogue_url = next_url
                            elif not next_url:
                                # No observed continuation.  Only the seller's own
                                # ACTIVE-ads counter can prove the catalogue end,
                                # and only when it equals the number of unique
                                # primary IDs collected inside this confirmed
                                # seller scope (redirect and block checks above
                                # have already passed).  Anything less keeps the
                                # traversal incomplete; nothing is paginated by
                                # guesswork.
                                if declared_active is not None and len(unique_keys) == declared_active:
                                    diagnostics['catalogue_end_proven_by'] = 'seller_active_counter'
                                    exhausted = True
                                    break
                                diagnostics['catalogue_navigation_unproven'] = True
                                error = 'Catalogue end not proven; no observed next-page link'
                                break
                            elif page_number == max_pages:
                                diagnostics['catalogue_navigation_unproven'] = True
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
                        if getattr(exc, 'navigation', None):
                            diagnostics[f'page_{page_number}_lazy_navigation'] = exc.navigation
                        if operator_wait_seconds(self.source.value) and recoverable_navigation_error(exc):
                            hit_count, pages_scanned, load_actions, seen_pages = checkpoint
                            del hits[hit_count:]
                            diagnostics['load_actions'] = load_actions
                            self._progress('navigation_operator_required', page=page_number, url=url,
                                           error=f'{type(exc).__name__}: {exc}')
                            try:
                                page, resumed_response = await wait_for_navigation_recovery(
                                    page, url=url, reopen=reopen, progress=self.progress_callback,
                                    page_number=page_number, request_timeout=self.request_timeout,
                                    document_watcher=document,
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

    def _extract(self, html: str, page_number: int, *, moscow_only=False) -> list[ListingHit]:
        cards = primary_cards(html,
            AVITO_CARD_SELECTOR,
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


def _is_moscow_listing_url(url: str) -> bool:
    return urlsplit(url).path.startswith('/moskva/')
