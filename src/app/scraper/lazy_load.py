"""Bounded native load-more controls inside the approved result list only."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from app.config import settings
from app.scraper.base import (
    ListingHit,
    canonical_listing_key,
    capture_listing_card_evidence,
    capture_page_evidence,
    classify_result_page,
    evidence_manifest_name,
)
from app.scraper.browser_session import read_page_content
from app.scraper.challenge_retry import (
    _same_recovered_page,
    _same_requested_page,
    operator_wait_seconds,
    refresh_explicit_captcha,
)
from app.scraper.pacing import choose_pause
from app.scraper.result_scope import SUPPLEMENT_HEADING, avito_active_catalogue_url, pagination_state

LOAD_MORE_NAME = re.compile(
    r'^(?:(?:показать|загрузить)\s+ещ[её](?:\s+\d+(?:\s+(?:объявлений|предложений))?)?'
    r'|(?:show|load)\s+more)$', re.I,
)

# Stable contract prefix shared with MonitorService: a scan that died on this
# marker was denied by the marketplace, not by a parser/scope/evidence error.
LAZY_LOAD_BLOCKED_MARKER = 'lazy load blocked'


class LazyLoadBlocked(RuntimeError):
    """The marketplace denied the continuation of this result list.

    Raised only for a CAPTCHA outcome or a blocked-page classification during
    a load-more wait. Ordinary parser, scope, evidence and timeout failures
    keep raising RuntimeError so they stay distinct technical errors.
    """

    def __init__(self, message: str, *, captcha_outcome: str | None = None,
                 captcha_refreshes: int = 0):
        super().__init__(message)
        self.captcha_outcome = captcha_outcome
        self.captcha_refreshes = captcha_refreshes


class LazyLoadScopeChanged(RuntimeError):
    """Keep the exact document/address pair when continuation is rejected."""

    def __init__(self, navigation):
        super().__init__('lazy load changed result scope or page')
        self.navigation = navigation


@dataclass
class LazyResults:
    response: Any
    html: str
    hits: list[ListingHit]
    batches: int = 0
    outcome: str = 'not_needed'
    recoveries: list = field(default_factory=list)


def _page_index(url):
    query = parse_qs(urlsplit(url).query)
    return query.get('p', query.get('page', ['1']))


def _avito_catalogue_document_matches(requested_url, final_url, document_url):
    if not all(avito_active_catalogue_url(url) for url in (requested_url, final_url, document_url)):
        return False
    if _same_recovered_page(requested_url, final_url, document_url):
        return True
    # A successful /items document can hydrate the full catalogue and replace
    # the address with /items/all/avtomobili without another document request.
    # Require the real document to match the original request, then check the
    # one known canonical path for this exact brand. Closed/filtered views
    # have already been rejected above; no arbitrary path rewrite is accepted.
    requested = urlsplit(requested_url)
    expanded = re.fullmatch(r'/brands/([^/]+)/items/?', requested.path)
    if (not expanded or parse_qs(requested.query).get('s') != ['profile_search_show_all']
            or not _same_requested_page(requested_url, document_url)):
        return False
    canonical = urlunsplit(requested._replace(path=f'/brands/{expanded[1]}/items/all/avtomobili'))
    return _same_requested_page(canonical, final_url)


async def _result_root(page, root_selector, card_selector, hits, *, seller_catalogue):
    roots = page.locator(root_selector)
    ranked = []
    for i in range(await roots.count()):
        root = roots.nth(i)
        count = await root.locator(card_selector).count()
        if count and await root.is_visible():
            ranked.append((count, i))
    if ranked:
        _, index = max(ranked, key=lambda item: (item[0], -item[1]))
        return roots.nth(index)
    if not seller_catalogue:
        return None
    # Hydrated profiles may leave catalog-serp empty. Derive a narrow scope
    # from the cards we actually parsed, never from a generic body selector.
    selector = await page.evaluate('''({cardSelector, urls}) => {
        const paths = new Set(urls.map(url => new URL(url).pathname));
        const cards = [...document.querySelectorAll(cardSelector)].filter(card => {
            if (!card.getClientRects().length || card.closest(
                'aside,template,[hidden],[aria-hidden="true"],[data-marker*="recommend"],'
                + '[class*="Recommendations"],[class*="RelatedOffers"]')) return false;
            return [...card.querySelectorAll('a[href]')].some(a => paths.has(new URL(a.href).pathname));
        });
        if (!cards.length) return null;
        let root = cards[0].parentElement;
        while (root && !cards.every(card => root.contains(card))) root = root.parentElement;
        if (!root || ['BODY', 'HTML'].includes(root.tagName)) return null;
        const parts = [];
        while (root && root.tagName !== 'HTML') {
            const siblings = [...root.parentElement.children].filter(node => node.tagName === root.tagName);
            parts.unshift(root.tagName.toLowerCase() + ':nth-of-type(' + (siblings.indexOf(root) + 1) + ')');
            root = root.parentElement;
        }
        return parts.join(' > ');
    }''', {'cardSelector': card_selector, 'urls': [hit.url for hit in hits]})
    return page.locator(selector) if selector else None


async def _load_more_button(root):
    if root is None:
        return None
    for scope in (root, root.locator('xpath=following-sibling::*[1]')):
        if await scope.count() == 0:
            continue
        markup = BeautifulSoup(await scope.inner_html(), 'html.parser')
        if any(SUPPLEMENT_HEADING.search(node.get_text(' ', strip=True))
               for node in markup.select('h2,h3,[role="heading"]')):
            continue
        buttons = scope.get_by_role('button', name=LOAD_MORE_NAME)
        for i in range(await buttons.count()):
            candidate = buttons.nth(i)
            if not await candidate.is_visible() or await candidate.get_attribute('href'):
                continue
            if await candidate.evaluate('''node => !node.closest(
                'aside,[data-marker*="recommend"],[class*="Recommendations"],[class*="RelatedOffers"]')'''):
                return candidate
    return None


async def load_more_results(page, response, html, *, source, requested_url, search_url,
                            page_number, extract, root_selector, card_selector,
                            declared_total=None, target_keys=None, progress=None, batch_budget=None,
                            seller_catalogue=False):
    result = LazyResults(response, html, extract(html))
    if (not settings.catalogue_lazy_load_enabled or not result.hits
            or classify_result_page(html, len(result.hits))[0] != 'results'
            or not hasattr(page, 'locator')):
        return result
    initial_page_index = _page_index(requested_url)
    initially_loaded_url = page.url
    known = {canonical_listing_key(source, hit.url): hit for hit in result.hits}
    pending_targets = set(target_keys or ())

    async def preserve_targets():
        for hit in result.hits:
            key = canonical_listing_key(source, hit.url)
            if key not in pending_targets:
                continue
            path = await capture_listing_card_evidence(
                page, source=source, search_url=search_url, page_number=page_number,
                hit=hit, evidence_dir=settings.evidence_dir,
            )
            if not path:
                raise RuntimeError('lazy-load exact-card evidence missing')
            hit.raw['card_evidence'] = path
            hit.raw['card_evidence_manifest'] = evidence_manifest_name(path)
            pending_targets.remove(key)

    batch_limit = settings.catalogue_lazy_load_max_batches
    if batch_budget is not None:
        batch_limit = min(batch_limit, max(0, batch_budget))
    for batch in range(1, batch_limit + 1):
        if target_keys and set(target_keys) <= known.keys():
            result.outcome = 'all_targets_loaded'
            break
        if declared_total is not None and len(known) >= declared_total:
            result.outcome = 'all_declared_loaded'
            break
        # Require the site's own visible control inside a known primary list.
        # A generic "more" button elsewhere could open recommendations or ads.
        root = await _result_root(page, root_selector, card_selector, result.hits,
                                  seller_catalogue=seller_catalogue)
        button = await _load_more_button(root)
        footer_probe = button is None and seller_catalogue and root is not None
        if button is None and not footer_probe:
            break
        await preserve_targets()
        pause = choose_pause(settings.scan_page_pause_min_seconds, settings.scan_page_pause_max_seconds)
        if progress:
            progress({'event': 'lazy_load_started', 'source': source.value, 'page': page_number,
                      'batch': batch, 'cards': len(known), 'wait_seconds': round(pause, 1)})
        await asyncio.sleep(pause)
        before = set(known)
        latest_response = result.response

        def document_response(candidate):
            nonlocal latest_response
            if candidate.request.is_navigation_request() and candidate.request.frame == page.main_frame:
                latest_response = candidate

        page.on('response', document_response)
        try:
            clicked = button is not None
            if clicked:
                await button.scroll_into_view_if_needed(timeout=3000)
                await button.click(timeout=3000)
            else:
                # Reveal the real list footer / intersection sentinel before
                # deciding that the catalogue has no continuation.
                await root.locator(card_selector).last.evaluate(
                    "node => node.scrollIntoView({block: 'end'})")
                await page.mouse.wheel(0, 800)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + settings.catalogue_lazy_load_timeout_seconds
            last_growth = loop.time()
            while True:
                await asyncio.sleep(0.25)
                result.html = await read_page_content(page)
                result.response = latest_response
                wait = operator_wait_seconds(source.value)
                recovery = await refresh_explicit_captcha(
                    page, result.response, result.html, source=source.value, url=requested_url,
                    progress=progress, wait_seconds=wait,
                    capture_challenge=(lambda p, r: capture_page_evidence(
                        p, source=source, search_url=search_url, page_number=page_number,
                        evidence_dir=settings.evidence_dir, purpose='captcha_challenge',
                        final_url=p.url, http_status=r.status if r else None,
                    )) if wait else None,
                )
                result.response, result.html = recovery.response, recovery.html
                if recovery.outcome != 'not_needed':
                    result.recoveries.append(recovery)
                if recovery.outcome in {'no_document_response', 'challenge_evidence_missing', 'wrong_destination'}:
                    raise RuntimeError(f'lazy load recovery failed: {recovery.outcome}')
                if result.response is None:
                    raise RuntimeError('lazy load recovery failed: no_document_response')
                if result.response.status >= 400 and result.response.status not in {401, 403, 429}:
                    raise RuntimeError(f'lazy load document HTTP {result.response.status}')
                if (result.response.status in {401, 403, 429}
                        or recovery.outcome in {'unresolved', 'http_blocked', 'operator_timeout'}):
                    raise LazyLoadBlocked(
                        f'{LAZY_LOAD_BLOCKED_MARKER}: CAPTCHA {recovery.outcome}',
                        captcha_outcome=recovery.outcome,
                        captcha_refreshes=recovery.refreshes,
                    )
                if recovery.outcome not in {'not_needed', 'resolved_by_operator', 'cleared_by_refresh'}:
                    raise RuntimeError(f'lazy load recovery failed: {recovery.outcome}')
                scope_matches = _same_requested_page(requested_url, page.url)
                document_url = str(getattr(result.response, 'url', ''))
                if seller_catalogue and source.value == 'avito':
                    # Show all has a proven canonical automobile route. Use
                    # the same HTTP-document-backed boundary as CAPTCHA
                    # recovery; exact path equality rejects that valid redirect.
                    scope_matches = (result.response.status == 200 and
                                     _avito_catalogue_document_matches(requested_url, page.url, document_url))
                page_matches = _page_index(page.url) == initial_page_index
                if seller_catalogue:
                    page_matches = page_matches and _page_index(document_url) == initial_page_index
                if not scope_matches or not page_matches:
                    raise LazyLoadScopeChanged({
                        'requested_url': requested_url, 'initially_loaded_url': initially_loaded_url,
                        'final_url': page.url, 'document_url': document_url,
                        'scope_matches': scope_matches, 'page_matches': page_matches,
                    })
                current = extract(result.html)
                state, reason = classify_result_page(result.html, len(current))
                if state == 'blocked':
                    raise LazyLoadBlocked(f'{LAZY_LOAD_BLOCKED_MARKER}: {reason}')
                for hit in current:
                    key = canonical_listing_key(source, hit.url)
                    if key not in known:
                        hit.position = len(known) + 1
                        hit.raw['lazy_load_batch'] = batch
                        known[key] = hit
                        last_growth = loop.time()
                result.hits = list(known.values())
                if footer_probe and not clicked and set(known) == before:
                    if pagination_state(result.html, source.value)['has_next']:
                        result.outcome = 'pagination_revealed'
                        break
                    root = await _result_root(page, root_selector, card_selector, current,
                                              seller_catalogue=True)
                    button = await _load_more_button(root)
                    if button is not None:
                        await button.scroll_into_view_if_needed(timeout=3000)
                        await button.click(timeout=3000)
                        clicked = True
                        deadline = loop.time() + settings.catalogue_lazy_load_timeout_seconds
                if ((set(known) != before and loop.time() - last_growth >= 1)
                        or loop.time() >= deadline):
                    break
            if set(known) == before:
                if footer_probe and not clicked:
                    if result.outcome != 'pagination_revealed':
                        result.outcome = 'continuation_unproven'
                    break
                raise RuntimeError('lazy load made no progress; traversal is not proven')
            result.batches = batch
            await preserve_targets()
            result.outcome = 'loaded'
            if progress:
                progress({'event': 'lazy_load_finished', 'source': source.value, 'page': page_number,
                          'batch': batch, 'cards': len(known)})
        finally:
            page.remove_listener('response', document_response)
    return result
