"""Ordinary CAPTCHA refresh and same-page manual recovery in visible Chrome."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass
from html import escape
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.config import settings
from app.scraper.browser_session import read_page_content
from app.scraper.result_scope import avito_active_catalogue_url

_CAPTCHA_MARKERS = (
    'captcha', 'showcaptcha', 'подтвердите, что вы не робот',
    'подтвердите, что запросы отправляли вы, а не робот',
    'verify you are human',
)
# A notice only: this never ends the wait or reloads a denied page.
REDIRECT_STALL_NOTICE_SECONDS = 60


@dataclass(frozen=True)
class CaptchaRecovery:
    response: Any
    html: str
    refreshes: int
    outcome: str
    challenge_evidence: str | None = None


def operator_wait_seconds(source: str | None) -> int:
    """Human assistance requires the visible host browser for either marketplace."""
    if (source not in {'auto_ru', 'avito'} or not settings.browser_cdp_url
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
        # A brand catalogue identifies its seller in the path. Avito may drop
        # the redundant sellerId and the s=profile_search_show_all tracking
        # value from this URL after the manual gate. Search queries, sorting
        # (s=104), pagination and every other parameter still have to be
        # retained: only this exact pair is redundant for the brand catalogue.
        brand_catalogue = (
            (url.hostname or '').removeprefix('www.') == 'avito.ru'
            and re.fullmatch(r'/brands/[^/]+/(?:items/)?all/avtomobili/?', url.path) is not None
        )
        return Counter(
            (key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
            if key not in {'context', 'src', 'iid', 'yclid'} and not key.startswith('utm_')
            and not (brand_catalogue and key == 'sellerId')
            and not (brand_catalogue and key == 's' and value == 'profile_search_show_all')
        )

    return (
        requested.hostname is not None
        and (requested.hostname or '').removeprefix('www.') == (final.hostname or '').removeprefix('www.')
        and path(requested) == path(final)
        and required_query(requested) <= required_query(final)
    )


def autoru_catalogue_subset(requested_url, final_url):
    """A same-dealer condition redirect is readable, but narrows inventory scope."""
    requested = urlsplit(requested_url or '')
    final = urlsplit(final_url)
    if ((requested.hostname or '').removeprefix('www.') != 'auto.ru'
            or (final.hostname or '').removeprefix('www.') != 'auto.ru'):
        return False
    wanted = re.fullmatch(r'/diler/(cars|lcv)/all/([^/]+)/?', requested.path)
    got = re.fullmatch(r'/diler/(cars|lcv)/(new|used)/([^/]+)/?', final.path)
    if not wanted or not got or wanted.groups() != (got[1], got[3]):
        return False
    narrowed = urlunsplit(requested._replace(path=final.path))
    return _same_requested_page(narrowed, final_url)


def _same_recovered_page(requested_url, final_url, document_url):
    requested = urlsplit(requested_url or '')
    document = urlsplit(document_url)
    expanded = re.fullmatch(r'/brands/([^/]+)/items/?', requested.path)
    # The observed Show all control resolves to the seller's automobile list.
    # Preserve all real query restrictions; only the already-known profile
    # tracking parameters may disappear on this exact canonical brand route.
    if (expanded and (requested.hostname or '').removeprefix('www.') == 'avito.ru'
            and (document.hostname or '').removeprefix('www.') == 'avito.ru'
            and parse_qs(requested.query).get('s') == ['profile_search_show_all']
            and document.path.rstrip('/') == f'/brands/{expanded[1]}/items/all/avtomobili'):
        canonical_request = urlunsplit(requested._replace(path=document.path))
        if _same_requested_page(canonical_request, document_url):
            return _same_requested_page(canonical_request, final_url)
    # Avito can hydrate the canonical automobile list via history.replaceState
    # while the HTTP 200 document still belongs to the original Show all URL.
    # Accept only that same brand and retain all real query restrictions.
    if (expanded and (requested.hostname or '').removeprefix('www.') == 'avito.ru'
            and parse_qs(requested.query).get('s') == ['profile_search_show_all']
            and _same_requested_page(requested_url, document_url)
            and avito_active_catalogue_url(final_url)):
        canonical_request = urlunsplit(requested._replace(
            path=f'/brands/{expanded[1]}/items/all/avtomobili'))
        if (_same_requested_page(canonical_request, final_url)
                and _same_requested_page(final_url, canonical_request)):
            return True
    # Auto.ru can redirect its all-inventory dealer route to a condition tab.
    # Let the adapter inspect that document; it must retain incomplete scope.
    if autoru_catalogue_subset(requested_url, document_url):
        return _same_requested_page(document_url, final_url)
    if not _same_requested_page(requested_url, document_url):
        return False
    if _same_requested_page(requested_url, final_url):
        return True
    # Live evidence: the HTTP 200 document retains localPriority=0 or 1, while
    # Avito replaces the browser address without it. Accept this history change
    # only with that exact document proof; never assume the missing default.
    requested = urlsplit(requested_url or '')
    final = urlsplit(final_url)
    query = parse_qsl(final.query, keep_blank_values=True)
    priorities = [value for key, value in parse_qsl(requested.query, keep_blank_values=True)
                  if key == 'localPriority']
    if ((requested.hostname or '').removeprefix('www.') != 'avito.ru'
            or len(priorities) != 1 or priorities[0] not in {'0', '1'}
            or any(key == 'localPriority' for key, _ in query)):
        return False
    restored = urlunsplit(final._replace(query=urlencode(query + [('localPriority', priorities[0])])))
    return _same_requested_page(requested_url, restored)


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
    """Wait for a person when enabled; otherwise retain the ordinary one-refresh path.

    No CAPTCHA is clicked or solved by code. An Avito IP notice can receive up
    to two delayed reloads in the operator wait, after which the same page stays
    open for manual assistance. With the operator wait enabled the
    decision is made from the visible page only: raw-HTML markers that remain
    in a hidden, already-solved widget never trigger the legacy automatic
    reload — the downstream page classification ignores hidden-only markers the
    same way, and unknown markup stays a conservative technical result. A
    disappeared challenge counts as resolved only after the requested host/path
    is stable twice. Otherwise the caller must retain an incomplete/blocked
    result.
    """
    status = response.status if response is not None else None
    if status is None:
        return CaptchaRecovery(response, html, 0, 'no_document_response')
    if wait_seconds != 0:
        if (await _visible_operator_challenge(page, html, source)
                or (source == 'avito' and status == 439)):
            return await _wait_for_operator(
                page, response, html, refreshes=0, progress=progress, source=source,
                url=url, wait_seconds=wait_seconds, poll_seconds=poll_seconds,
                capture_challenge=capture_challenge,
            )
        # No visible challenge: nothing for a person to solve, and no reload
        # while a human can act. Hidden-only markers are handled by the same
        # visible-text classification downstream.
        return CaptchaRecovery(response, html, 0, 'not_needed')
    if not explicit_captcha(html, page.url, status):
        return CaptchaRecovery(response, html, 0, 'not_needed')
    if progress:
        progress({'event': 'captcha_refresh', 'source': source, 'url': url, 'attempt': 1})
    await asyncio.sleep(3)
    refreshed = await page.reload(wait_until='domcontentloaded')
    html = await read_page_content(page)
    status = refreshed.status if refreshed is not None else None
    if status is None:
        return CaptchaRecovery(refreshed, html, 1, 'no_document_response')
    if status >= 400:
        return CaptchaRecovery(refreshed, html, 1, 'http_blocked')
    if not explicit_captcha(html, page.url, status):
        outcome = 'cleared_by_refresh' if _same_requested_page(url, page.url) else 'wrong_destination'
        return CaptchaRecovery(refreshed, html, 1, outcome)
    return CaptchaRecovery(refreshed, html, 1, 'unresolved')


def operator_redirect_pending(html: str, source: str | None) -> bool:
    """Avito's solved gate still owns the page until its redirect completes."""
    if source != 'avito':
        return False
    text = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True).lower()
    return bool(re.search(r'проверка\s+пройдена\s*[,.:;!—-]*\s*перенаправление', text))


def operator_profile_load_failed(html: str, source: str | None) -> bool:
    if source != 'avito':
        return False
    text = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True).lower()
    return 'не получилось загрузить профиль' in text


def avito_ip_restriction(html: str, source: str | None) -> bool:
    """Recognise the visible IP notice, never an active or solved CAPTCHA."""
    if source != 'avito' or operator_redirect_pending(html, source):
        return False
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup.select('script,style,template,[hidden],[aria-hidden="true"]'):
        node.decompose()
    text = soup.get_text(' ', strip=True).lower()
    if any(marker in text for marker in _CAPTCHA_MARKERS):
        return False
    if soup.select_one('iframe[src*="captcha"],input[name*="captcha"],input[name*="Captcha"]'):
        return False
    return text.startswith('доступ ограничен') and bool(re.search(r'(?:проблема с|по)\s+ip\b', text))


def operator_challenge(html: str, final_url: str, source: str | None) -> bool:
    """A person may also use Avito's explicit Continue gate on an HTTP 429 page."""
    if (avito_ip_restriction(html, source)
            or operator_profile_load_failed(html, source) or operator_redirect_pending(html, source)
            or explicit_captcha(html, final_url, 200)):
        return True
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(' ', strip=True).lower()
    return (source == 'avito' and 'доступ ограничен' in text and 'проблема с ip' in text
            and any((node.get('value') or node.get_text(' ', strip=True)).lower() == 'продолжить'
                    for node in soup.select('button, a, [role="button"], input[value]')))


async def _visible_operator_markup(page, html):
    # A solved widget can remain hidden in the DOM. Its hidden text must not
    # keep a person waiting after the ordinary catalogue has become visible.
    if hasattr(page, 'evaluate'):
        visible = await page.evaluate('''() => {
            if (!document.body) return null;
            const buttons = [...document.querySelectorAll('button,a,[role="button"],input[value]')];
            return {text: document.body.innerText,
                    canContinue: buttons.some(node => node.getClientRects().length &&
                        (node.value || node.innerText || '').trim().toLowerCase() === 'продолжить'),
                    captchaVisible: [...document.querySelectorAll(
                        'iframe[src*="captcha"],input[name*="captcha"],input[name*="Captcha"]'
                    )].some(node => node.getClientRects().length)};
        }''')
        if isinstance(visible, dict) and isinstance(visible.get('text'), str):
            html = escape(visible['text'])
            if visible.get('canContinue'):
                html += '<button>Продолжить</button>'
            if visible.get('captchaVisible'):
                html += '<span>captcha</span>'
    return html


async def _visible_operator_challenge(page, html, source):
    return operator_challenge(await _visible_operator_markup(page, html), page.url, source)


async def _wait_for_operator(page, response, html, *, refreshes, progress, source,
                             url, wait_seconds, poll_seconds, capture_challenge):
    # Only main-document responses can restore a denied page. An image/XHR 200
    # or a changed title must never turn the original HTTP 429 into success.
    latest_response = response

    def document_response(candidate):
        nonlocal latest_response
        request = candidate.request
        if request.is_navigation_request() and request.frame == page.main_frame:
            latest_response = candidate

    listening = hasattr(page, 'on')
    if listening:
        page.on('response', document_response)
    loop = asyncio.get_running_loop()
    deadline = None if wait_seconds == -1 else loop.time() + wait_seconds
    stable_url = None
    denied_without_challenge_since = None
    redirect_pending_since = None
    ip_notice_since = loop.time() if avito_ip_restriction(html, source) else None
    ip_refreshes = 0
    automatic_response = None
    last_wait_state = None
    next_notice = 0
    last_html = html
    try:
        challenge_evidence = await capture_challenge(page, response) if capture_challenge else None
        if capture_challenge and not challenge_evidence:
            return CaptchaRecovery(response, html, refreshes, 'challenge_evidence_missing')
        await page.bring_to_front()
        if progress:
            progress({'event': 'captcha_operator_required', 'source': source,
                      'url': url, 'wait_seconds': wait_seconds})
        while deadline is None or loop.time() < deadline:
            delay = max(0.1, poll_seconds)
            if deadline is not None:
                delay = min(delay, max(0, deadline - loop.time()))
            await asyncio.sleep(delay)
            try:
                last_html = await read_page_content(page)
            except Exception as exc:
                if not any(marker in str(exc).lower() for marker in (
                    'page is navigating and changing the content', 'execution context was destroyed',
                )):
                    raise
                stable_url = None
                if progress:
                    stalled = (redirect_pending_since is not None
                               and loop.time() - redirect_pending_since >= REDIRECT_STALL_NOTICE_SECONDS)
                    progress({'event': 'captcha_operator_waiting', 'source': source, 'url': url,
                              'wait_reason': 'redirect_stalled' if stalled else 'redirect_pending',
                              'final_url': page.url})
                    if stalled and loop.time() >= next_notice:
                        progress({'event': 'captcha_manual_refresh_hint', 'source': source,
                                  'url': url, 'wait_reason': 'redirect_stalled',
                                  'instruction': 'Avito не завершил переход после проверки. '
                                                 'Обновите ЭТУ ЖЕ вкладку один раз (Cmd+R).'})
                        next_notice = loop.time() + 30
                continue
            status = latest_response.status if latest_response is not None else None
            document_url = getattr(latest_response, 'url', page.url)
            visible_html = await _visible_operator_markup(page, last_html)
            ip_notice = avito_ip_restriction(visible_html, source)
            ip_same_page = (ip_notice
                           and (urlsplit(page.url).hostname or '').removeprefix('www.') == 'avito.ru'
                           and _same_requested_page(url, page.url))
            if (ip_same_page
                    and ip_refreshes < settings.avito_ip_refresh_attempts):
                if ip_notice_since is None:
                    ip_notice_since = loop.time()
                if loop.time() - ip_notice_since >= settings.avito_ip_refresh_delay_seconds:
                    ip_refreshes += 1
                    refreshes += 1
                    stable_url = None
                    if progress:
                        progress({'event': 'captcha_refresh', 'source': source, 'url': url,
                                  'reason': 'ip_restriction', 'attempt': ip_refreshes,
                                  'limit': settings.avito_ip_refresh_attempts})
                    latest_response = None
                    try:
                        refreshed = await page.reload(wait_until='domcontentloaded',
                            timeout=settings.request_timeout_seconds * 1000)
                        latest_response = refreshed
                        automatic_response = refreshed
                    except (TimeoutError, PlaywrightTimeoutError):
                        # A reload can time out while its document is still arriving.
                        # Keep this same page and the document listener alive.
                        pass
                    ip_notice_since = loop.time()
                    continue
            elif not ip_notice:
                ip_notice_since = None
            if operator_challenge(visible_html, page.url, source):
                stable_url = None
                denied_without_challenge_since = None
                reason = ('wrong_destination' if ip_notice and not ip_same_page
                          else 'ip_refresh_wait' if ip_notice and ip_refreshes < settings.avito_ip_refresh_attempts
                          else 'ip_refresh_exhausted' if ip_notice and settings.avito_ip_refresh_attempts
                          else 'profile_load_failed' if operator_profile_load_failed(visible_html, source)
                          else 'redirect_pending' if operator_redirect_pending(visible_html, source)
                          else 'challenge_present')
                if reason == 'redirect_pending':
                    if redirect_pending_since is None:
                        redirect_pending_since = loop.time()
                    if loop.time() - redirect_pending_since >= REDIRECT_STALL_NOTICE_SECONDS:
                        reason = 'redirect_stalled'
                else:
                    redirect_pending_since = None
            elif status == 200 and _same_recovered_page(url, page.url, document_url):
                redirect_pending_since = None
                denied_without_challenge_since = None
                if stable_url == page.url:
                    outcome = ('cleared_by_refresh' if ip_refreshes and latest_response is automatic_response
                               else 'resolved_by_operator')
                    if progress:
                        progress({'event': 'captcha_operator_resolved', 'source': source, 'url': url,
                                  'outcome': outcome})
                    return CaptchaRecovery(latest_response, last_html, refreshes,
                                           outcome, challenge_evidence)
                stable_url = page.url
                reason = 'stabilizing'
            else:
                redirect_pending_since = None
                stable_url = None
                if not _same_requested_page(url, page.url):
                    reason = 'wrong_destination'
                elif status is not None and status >= 400:
                    # The visible challenge is gone, yet the latest main document
                    # is still the denial. In-place/XHR rendering is not a fresh
                    # document and is never accepted as recovery; the operator
                    # needs to refresh this very tab.
                    if denied_without_challenge_since is None:
                        denied_without_challenge_since = loop.time()
                    grace = settings.captcha_redirect_grace_seconds if source == 'avito' else 0
                    reason = ('redirect_pending' if loop.time() - denied_without_challenge_since < grace
                              else 'manual_refresh_required')
                else:
                    reason = 'awaiting_successful_document'
            wait_state = (reason, page.url, status, document_url)
            if progress and (wait_state != last_wait_state or loop.time() >= next_notice):
                progress({'event': 'captcha_operator_waiting', 'source': source, 'url': url,
                          'final_url': page.url, 'document_url': document_url,
                          'http_status': status, 'wait_reason': reason})
                if reason in {'manual_refresh_required', 'redirect_stalled'}:
                    progress({'event': 'captcha_manual_refresh_hint', 'source': source,
                      'url': url, 'final_url': page.url, 'http_status': status,
                              'wait_reason': reason,
                              'instruction': ('Avito не завершил переход после проверки. '
                                              'Обновите ЭТУ ЖЕ вкладку один раз (Cmd+R).'
                                              if reason == 'redirect_stalled' else
                                              'Если проверка пройдена и каталог виден — '
                                              'обновите ЭТУ ЖЕ вкладку (Cmd+R)')})
                last_wait_state = wait_state
                next_notice = loop.time() + 30
    except Exception:
        if progress:
            progress({'event': 'captcha_operator_unresolved', 'source': source,
                      'url': url, 'outcome': 'browser_error'})
        raise
    finally:
        if listening:
            page.remove_listener('response', document_response)

    outcome = ('wrong_destination' if not operator_challenge(last_html, page.url, source)
               and not _same_requested_page(url, page.url) else 'operator_timeout')
    if progress:
        progress({'event': 'captcha_operator_unresolved', 'source': source,
                  'url': url, 'outcome': outcome})
    return CaptchaRecovery(latest_response, last_html, refreshes, outcome, challenge_evidence)
