"""Conservative seller-page boundaries and bounded inspection of stale direct links."""
import asyncio
import hashlib
import re
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from app.config import settings
from app.models import EngineType
from app.scraper.base import (
    canonical_listing_key,
    capture_page_evidence,
    classify_result_page,
    evidence_manifest_name,
)
from app.scraper.browser_session import browser_page, read_page_content
from app.scraper.challenge_retry import operator_wait_seconds, refresh_explicit_captcha
from app.scraper.pacing import choose_pause
from app.scraper.result_scope import SUPPLEMENT_HEADING, avito_active_catalogue_url
from app.service.placement_identity import placement_id_claim_from_description, placement_id_from_description
from app.service.vehicle_content import structured_vehicle_fields

SELLER_SOURCES = {
    'auto_ru': ['https://auto.ru/diler/cars/all/a1_avto_moskva/',
                'https://auto.ru/diler/lcv/all/a1_avto_moskva/'],
    'avito': ['https://www.avito.ru/brands/a1auto/all/avtomobili?src=search_seller_info&iid=8047929828&sellerId=e4036231f69cc5cfc365db2f79600230'],
}

DESCRIPTION_SELECTORS = (
    '[data-marker="item-view/item-description"]',
    '[class*="CardDescription__textInner"]',
    '[class*="CardOfferDescription__text"]',
    '[class*="CardOfferDescription"]',
    '[class~="CardDescription"]',
    '[itemprop="description"]:not(meta)',
    '[class*="Description"]',
)
DESCRIPTION_EXPAND_SELECTORS = (
    '[class~="CardDescription"] [class*="CardDescription__cutLink"], '
    '[class*="CardOfferDescription"] [class*="cutLink"], '
    '[data-marker="item-view/item-description"] button, '
    '[data-marker="item-view/item-description"] [role="button"]'
)


def seller_page_matches(requested, final):
    def identity(url):
        parts = urlsplit(url)
        path = parts.path.rstrip('/')
        if ((parts.hostname or '').removeprefix('www.') == 'avito.ru'
                and re.fullmatch(r'/brands/[^/]+/items', path)
                and avito_active_catalogue_url(url)):
            path += '/all/avtomobili'
        return parts.hostname, re.sub(r'/items(?=/all/)', '', path)
    wanted_host, wanted_path = identity(requested)
    got_host, got_path = identity(final)
    return ((got_host or '').removeprefix('www.') == (wanted_host or '').removeprefix('www.')
            and wanted_path == got_path)


def catalogue_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    # Only explicit recommendation containers are excluded; an unknown layout stays uncertain.
    for node in soup.select('aside, [data-marker*="recommend"], [class*="Recommendations"], [class*="RelatedOffers"]'):
        node.decompose()
    return str(soup)


def source_challenged(error, diagnostics):
    return (any(token in str(error or '').lower() for token in
                ('http 401', 'http 403', 'http 429', 'blocked', 'captcha', 'seller page redirected'))
            or any(str(value) == 'blocked' for key, value in diagnostics.items() if key.endswith('_state')))


def _card_price(price_text):
    """Keep the money token separate from percentages and promotional figures."""
    value = str(price_text or '').strip()
    amounts = re.findall(r'(?<![\d.,])(\d[\d\s\u00a0\u202f]*)\s*(?:₽|руб(?:лей|ля|\.)?)(?!\w)', value, re.I)
    if len(amounts) == 1:
        return int(re.sub(r'\D', '', amounts[0]))
    if not amounts and re.fullmatch(r'\d[\d\s\u00a0\u202f]*(?:\.00)?', value):
        return int(re.sub(r'\D', '', value.removesuffix('.00')))
    return None


def _direct_card_fields(html, source):
    soup = BeautifulSoup(catalogue_html(html), 'html.parser')

    def first_text(selectors):
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                value = ' '.join(node.get_text(' ', strip=True).split())
                if not value and node.get('content'):
                    value = ' '.join(str(node.get('content')).split())
                if value:
                    return value
        return None

    title = first_text(('h1', '[data-marker="item-view/title-info"]', '[itemprop="name"]'))
    description = None
    description_selector = None
    for selector in DESCRIPTION_SELECTORS:
        node = soup.select_one(selector)
        if node is None:
            continue
        for control in node.select('button, [class*="__cutLink"], [class*="__title"]'):
            control.decompose()
        value = '\n'.join(' '.join(line.split()) for line in node.get_text('\n', strip=True).splitlines()
                          if line.strip())
        if value:
            description, description_selector = value, selector
            break
    price_text = first_text((
        '[data-marker="item-view/item-price"]',
        '[itemprop="price"]',
        '[class*="PriceUsedOffer"]',
        '[class*="OfferPrice"]',
    ))
    evidence_text = ' '.join(value for value in (title, description) if value)
    folded = evidence_text.casefold().replace('ё', 'е')
    has_without_vat = bool(re.search(
        r'\bбез\s+ндс\b|\bндс\s+не\s+облагается\b|\bне\s+облагается\s+ндс\b',
        folded,
    ))
    has_with_vat = bool(re.search(
        r'\b(?:с|включая)\s+ндс\b|\bндс\s+(?:включен|включена|включено)\b|\bв\s*т\.?\s*ч\.?\s*ндс\b',
        folded,
    ))
    if has_without_vat and has_with_vat:
        vat_status = 'Неоднозначно'
    elif has_without_vat:
        vat_status = 'Без НДС'
    elif has_with_vat:
        vat_status = 'С НДС'
    else:
        vat_status = None
    availability = next(
        (label for marker, label in (
            ('в наличии', 'В наличии'),
            ('в пути', 'В пути'),
            ('под заказ', 'Под заказ'),
            ('на заказ', 'На заказ'),
        ) if marker in folded),
        None,
    )
    vins = sorted(set(re.findall(r'\b[A-HJ-NPR-Z0-9]{17}\b', evidence_text.upper())))
    years = re.findall(r'\b20[0-3]\d\b', evidence_text)
    attributes = structured_vehicle_fields(html)
    title_years = re.findall(r'\b(?:19|20)\d{2}\b', title or '')
    title_year = int(title_years[0]) if len(set(title_years)) == 1 else None
    return {
        **attributes,
        'title': title,
        'price': _card_price(price_text),
        'year': int(years[0]) if years else None,
        'year_title': title_year,
        'vin': vins[0] if len(vins) == 1 else None,
        'availability': availability,
        'vat_status': vat_status,
        'vat_required': source == EngineType.AVITO,
        'description': description,
        # Compatibility alias: old consumers must also receive the entire text.
        'description_excerpt': description,
        'description_capture': {
            'mode': 'full_dom_text', 'selector': description_selector,
            'characters': len(description or ''), 'software_truncated': False,
            'sha256': hashlib.sha256((description or '').encode('utf-8')).hexdigest(),
        },
        'placement_id_raw': placement_id_claim_from_description(description),
        'placement_id': placement_id_from_description(description),
    }


async def expand_direct_description(page, html, *, progress=None):
    """Expand only the advert's own observed description control, at most once."""
    soup = BeautifulSoup(catalogue_html(html), 'html.parser')
    candidates = [node for node in soup.select(DESCRIPTION_EXPAND_SELECTORS)
                  if re.fullmatch(r'Читать дальше|Читать полностью|Показать полностью|Показать описание',
                                  node.get_text(' ', strip=True), re.I)]
    if not candidates:
        return html, {'state': 'not_needed', 'clicks': 0}
    controls = page.locator(DESCRIPTION_EXPAND_SELECTORS).filter(
        has_text=re.compile(r'^(?:Читать дальше|Читать полностью|Показать полностью|Показать описание)$', re.I))
    for index in range(await controls.count()):
        control = controls.nth(index)
        if not await control.is_visible():
            continue
        try:
            if await control.evaluate(
                    '(element) => Boolean(element.closest(\'aside, [data-marker*="recommend"], '
                    '[class*="Recommendations"], [class*="RelatedOffers"]\'))'):
                continue
            if progress:
                progress({'event': 'direct_description_expanding', 'no_timeout_skip': True})
            button = await control.element_handle()
            root_selector = (
                '[class~="CardDescription"], [class*="CardOfferDescription"], '
                '[data-marker="item-view/item-description"]'
            )
            before = await control.evaluate(
                '(button, selector) => (button.closest(selector) || button.parentElement)?.textContent',
                root_selector,
            )
            await control.click(timeout=0)
            # Some layouts keep the same toggle label after expansion. Waiting
            # only for its disappearance stalls despite the newly loaded text.
            # Still wait without a timeout until there is actual expansion proof.
            await page.wait_for_function(
                '''({button, selector, before}) => {
                    if (!button.isConnected || !button.getClientRects().length) return true;
                    const style = getComputedStyle(button);
                    if (style.display === 'none' || style.visibility === 'hidden') return true;
                    if (button.getAttribute('aria-expanded') === 'true') return true;
                    if (/^(?:Свернуть|Скрыть)(?:\\s|$)/i.test(button.textContent.trim())) return true;
                    const root = button.closest(selector) || button.parentElement;
                    return root && root.textContent !== before;
                }''',
                arg={'button': button, 'selector': root_selector, 'before': before},
                timeout=0,
            )
            return await read_page_content(page), {'state': 'expanded', 'clicks': 1}
        except Exception as exc:
            return html, {'state': 'failed', 'clicks': 1, 'reason': type(exc).__name__}
    return html, {'state': 'already_in_dom', 'clicks': 0}


CONTENT_STATE_LABELS = {
    'active': 'действующая карточка',
    'removed': 'карточка с отметкой о продаже или снятии',
    'unknown': 'страница без распознанной карточки',
}


def _content_verdict(html, source):
    """Classify the rendered main document only; transport is not considered."""
    soup = BeautifulSoup(catalogue_html(html), 'html.parser')
    nodes = soup.select('h1, [data-marker="item-view/status"], [class*="OfferStatus"], [itemprop="availability"]')
    status_text = ' '.join(n.get_text(' ', strip=True) + ' ' + str(n.get('href', '')) for n in nodes).lower()
    if any(marker in status_text for marker in ('автомобиль продан', 'машина продана', 'объявление снято',
                                               'объявление закрыто', 'soldout', 'discontinued')):
        code = 'sold' if 'продан' in status_text else 'unpublished'
        return {'state': 'removed', 'status_code': code, 'reason': 'На странице объявления есть явная отметка о продаже или снятии',
                'card': _direct_card_fields(html, source)}
    # The 09.09 operator screenshots show banners outside h1/OfferStatus.
    # Accept an exact standalone banner before recommendations, never a description quote.
    for node in soup.select('script, style, template, [itemprop="description"], '
                            '[data-marker*="description"], [class*="Description"], [class*="description"]'):
        node.decompose()
    removed_banner = re.compile(r'(?:этот автомобиль (?:уже )?продан|автомобиль (?:уже )?продан|'
                                r'объявление снято(?: с публикации)?|объявление закрыто)[.!]?', re.I)
    supplement_heading = None
    for node in soup.find_all(True):
        label = ' '.join(node.get_text(' ', strip=True).split())
        if node.name in ('h2', 'h3') and SUPPLEMENT_HEADING.search(label):
            supplement_heading = node
            break
        if removed_banner.fullmatch(label):
            folded = label.casefold().replace('ё', 'е')
            code = 'sold' if 'продан' in folded else 'closed' if 'закрыто' in folded else 'unpublished'
            return {'state': 'removed', 'status_code': code, 'reason': f'На странице объявления: «{label}»',
                    'card': _direct_card_fields(html, source)}
    card = _direct_card_fields(html, source)
    if card.get('description_excerpt') and removed_banner.search(card['description_excerpt']):
        return {
            'state': 'unknown',
            'status_code': 'ambiguous_status_text',
            'reason': 'Фраза о снятии есть только в описании; состояние карточки нельзя подтвердить',
            'card': card,
        }
    if supplement_heading is not None:
        supplement_text = ' '.join(
            sibling.get_text(' ', strip=True)
            for sibling in supplement_heading.find_all_next()
        )
        if removed_banner.search(supplement_text):
            return {
                'state': 'unknown',
                'status_code': 'ambiguous_status_text',
                'reason': 'Фраза о снятии найдена только после блока похожих объявлений',
                'card': card,
            }
    if card['title']:
        return {'state': 'active', 'status_code': 'active', 'reason': 'Прямая карточка объявления открывается', 'card': card}
    return {'state': 'unknown', 'status_code': 'unknown', 'reason': 'Страница доступна, но карточка объявления не распознана', 'card': card}


def direct_page_status(html, source, expected_url, final_url, http_status):
    final = urlsplit(final_url)
    if (final.hostname == 'auth.auto.ru' or '/login' in final.path
            or any(marker in final.path.lower() for marker in ('/captcha', '/showcaptcha'))):
        return {'state': 'blocked', 'status_code': 'blocked', 'reason': 'Требуется вход или проверка пользователя'}
    if source_challenged(f'HTTP {http_status}', {}):
        return {'state': 'blocked', 'status_code': 'blocked', 'reason': f'HTTP {http_status}'}
    page_state, reason = classify_result_page(html, 0)
    if page_state == 'blocked':
        return {'state': 'blocked', 'status_code': 'blocked', 'reason': reason}
    if canonical_listing_key(source, final_url) != canonical_listing_key(source, expected_url):
        return {'state': 'unknown', 'status_code': 'redirected', 'reason': 'Ссылка перенаправила на другую страницу'}
    verdict = _content_verdict(html, source)
    if http_status is None or http_status < 400:
        return verdict
    # A transport error over a rendered document keeps three separate facts:
    # the original HTTP status, the parsed fields, and an explicit uncertainty.
    # The content verdict is reported as evidence and never upgrades the claim:
    # only a clean 2xx response for the same URL can confirm the card, and no
    # automatic retry is performed here.  Under an anomalous transport an
    # «active» verdict also needs corroborating card fields, not a bare heading.
    card = verdict.get('card') or {}
    corroborated = bool(
        card.get('title')
        and (card.get('price') is not None or card.get('description_excerpt'))
    )
    content_state = (
        verdict['state']
        if verdict['state'] != 'active' or corroborated
        else 'unknown'
    )
    return {
        'state': 'unknown',
        'status_code': 'http_error',
        'reason': (
            f'HTTP {http_status}; это не доказывает продажу. Содержимое распознано как '
            f'{CONTENT_STATE_LABELS.get(content_state, content_state)}; для подтверждения '
            'нужен чистый ответ 2xx по той же ссылке'
        ),
        'http_status': http_status,
        'content_state': content_state,
        'card': card,
    }


async def wait_for_direct_card_content(page, source, url, http_status, html, *,
                                       notice_after_seconds=None, poll_seconds=0.2,
                                       progress=None, status_provider=None, require_document=False):
    """Read hydrated card fields instead of assuming a fixed delay was enough.

    Two matching title/price/description snapshots are required. A missing ID
    in an otherwise loaded description is a data issue, not a reason to wait.
    Explicit challenges, removal and redirects stop this content wait; manual
    CAPTCHA recovery remains a separate stage with its own operator deadline.
    """
    notice_after = (settings.card_content_notice_after_seconds
                    if notice_after_seconds is None else notice_after_seconds)
    loop = asyncio.get_running_loop()
    started = loop.time()
    next_notice = started + max(0.01, notice_after)
    previous = None
    required = ('title', 'price', 'description_excerpt')
    while True:
        if status_provider is not None:
            http_status = status_provider()
        verdict = direct_page_status(html, source, url, page.url, http_status)
        fields = _direct_card_fields(html, source)
        missing = [key for key in required if not fields.get(key)]
        terminal = (verdict.get('state') in {'blocked', 'removed'}
                    or (http_status is not None and verdict.get('status_code') == 'redirected')
                    or verdict.get('content_state') == 'removed')
        terminal = terminal and (http_status is not None or not require_document)
        snapshot = tuple(fields.get(key) for key in required)
        stable = not missing and snapshot == previous and (http_status is not None or not require_document)
        # An explicitly empty description is a content defect, not a timeout.
        explicit_missing = bool(fields.get('title') and fields.get('price') is not None
                                and re.search(r'описание отсутствует|нет описания',
                                              fields.get('description_excerpt') or '', re.I))
        if terminal or stable:
            return html, {
                'state': 'terminal' if terminal else 'missing_content' if explicit_missing else 'ready',
                'wait_seconds': round(loop.time() - started, 3),
                'missing_fields': missing,
                'stable': stable,
            }
        previous = snapshot if not missing else None
        if loop.time() >= next_notice:
            if progress:
                progress({'event': 'direct_content_waiting', 'source': source.value,
                          'url': url, 'wait_seconds': round(loop.time() - started, 1),
                          'missing_fields': missing, 'document_response_missing': http_status is None,
                          'no_timeout_skip': True})
            next_notice = loop.time() + max(1, notice_after)
        await asyncio.sleep(max(0.01, poll_seconds))
        try:
            html = await read_page_content(page)
        except Exception as exc:
            if not any(marker in str(exc).lower() for marker in (
                'page is navigating and changing the content', 'execution context was destroyed',
            )):
                raise


@asynccontextmanager
async def _card_document_responses(page):
    state = {'response': None}

    def document_response(response):
        if response.request.is_navigation_request() and response.request.frame == page.main_frame:
            state['response'] = response

    listening = hasattr(page, 'on')
    if listening:
        page.on('response', document_response)
    try:
        yield state
    finally:
        if listening:
            page.remove_listener('response', document_response)


async def inspect_direct_link(source, url, progress=None):
    delay = choose_pause(settings.scan_page_pause_min_seconds, settings.scan_page_pause_max_seconds)
    if progress:
        progress({'event': 'dealer_link_check', 'source': source.value, 'url': url, 'wait_seconds': round(delay, 1)})
    await asyncio.sleep(delay)
    try:
        async with async_playwright() as p:
            async with browser_page(p, source=source.value) as page, _card_document_responses(page) as document:
                try:
                    response = await page.goto(url, timeout=settings.request_timeout_seconds * 1000, wait_until='domcontentloaded')
                except PlaywrightTimeoutError:
                    response = document['response']
                    if progress:
                        progress({'event': 'direct_navigation_waiting', 'source': source.value,
                                  'url': url, 'reason': 'navigation_timeout', 'no_timeout_skip': True})
                if document['response'] is None:
                    document['response'] = response
                # Read inside the wait loop so transient navigation cannot skip this card.
                html = ''
                html, readiness = await wait_for_direct_card_content(
                    page, source, url, response.status if response else None, html,
                    progress=progress, require_document=True,
                    status_provider=lambda: document['response'].status if document['response'] else None,
                )
                response = document['response']
                captcha_wait = operator_wait_seconds(source.value)
                recovery = await refresh_explicit_captcha(
                    page, response, html, progress=progress, source=source.value, url=url,
                    wait_seconds=captcha_wait,
                    capture_challenge=(
                        lambda challenge_page, challenge_response: capture_page_evidence(
                            challenge_page, source=source, search_url=url, page_number=1,
                            evidence_dir=settings.evidence_dir, purpose='captcha_challenge',
                            final_url=challenge_page.url,
                            http_status=challenge_response.status if challenge_response else None,
                        )
                    ) if captcha_wait else None,
                )
                response, html = recovery.response, recovery.html
                if response is not None:
                    document['response'] = response
                if recovery.outcome in {'resolved_by_operator', 'cleared_by_refresh'}:
                    html, readiness = await wait_for_direct_card_content(
                        page, source, url, response.status if response else None, html,
                        progress=progress, require_document=True,
                        status_provider=lambda: document['response'].status if document['response'] else None,
                    )
                    response = document['response']
                expansion = {'state': 'not_applicable', 'clicks': 0}
                before_expansion = direct_page_status(
                    html, source, url, page.url, response.status if response else None)
                if before_expansion.get('state') == 'active':
                    html, expansion = await expand_direct_description(page, html, progress=progress)
                    if expansion['state'] == 'expanded':
                        html, readiness = await wait_for_direct_card_content(
                            page, source, url, response.status if response else None, html,
                            progress=progress, require_document=True,
                            status_provider=lambda: document['response'].status if document['response'] else None,
                        )
                        response = document['response']
                result = direct_page_status(html, source, url, page.url, response.status if response else None)
                result['description_expansion'] = expansion
                if expansion['state'] == 'failed' and result.get('state') == 'active':
                    result.update(state='unknown', status_code='content_incomplete', content_state='active',
                                  reason='Текст описания сохранён без обрезки, но раскрытие полного блока не подтверждено')
                    readiness = {**readiness, 'state': 'description_unverified'}
                if readiness['state'] == 'missing_content' and result.get('status_code') == 'active':
                    result.update(
                        state='unknown', status_code='content_incomplete', content_state='active',
                        reason='Карточка открыта, но площадка явно сообщает об отсутствии описания; проверка содержимого выявила недостаток',
                    )
                result['content_readiness'] = readiness
                if recovery.outcome in {'unresolved', 'operator_timeout', 'wrong_destination',
                                        'challenge_evidence_missing', 'no_document_response'}:
                    reason = ('Не получен ответ страницы' if recovery.outcome == 'no_document_response'
                              else f'CAPTCHA {recovery.outcome}')
                    result = {'state': 'blocked', 'status_code': 'blocked',
                              'reason': reason}
                if recovery.outcome != 'not_needed':
                    result['captcha_outcome'] = recovery.outcome
                    if recovery.challenge_evidence:
                        result['captcha_challenge_evidence'] = recovery.challenge_evidence
                        result['captcha_challenge_evidence_manifest'] = evidence_manifest_name(
                            recovery.challenge_evidence
                        )
                evidence = await capture_page_evidence(
                    page,
                    source=source,
                    search_url=url,
                    page_number=1,
                    evidence_dir=settings.evidence_dir,
                    purpose='direct_card',
                    final_url=page.url,
                    http_status=response.status if response else None,
                )
                if evidence is None:
                    return {
                        'state': 'unknown',
                        'status_code': 'evidence_missing',
                        'reason': 'Снимок прямой карточки не сохранён; статус нельзя подтвердить',
                        'unverified_state': result.get('state'),
                    }
                result['evidence'] = evidence
                result['evidence_manifest'] = evidence_manifest_name(evidence)
                return result
    except Exception as exc:
        return {'state': 'unknown', 'reason': f'{type(exc).__name__}: {exc}'}
