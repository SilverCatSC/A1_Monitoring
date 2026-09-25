"""Conservative seller-page boundaries and bounded inspection of stale direct links."""
import asyncio
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from app.config import settings
from app.models import EngineType
from app.scraper.base import (
    canonical_listing_key,
    capture_page_evidence,
    classify_result_page,
    evidence_manifest_name,
)
from app.scraper.browser_session import browser_page
from app.scraper.challenge_retry import operator_wait_seconds, refresh_explicit_captcha
from app.scraper.pacing import choose_pause
from app.scraper.result_scope import SUPPLEMENT_HEADING
from app.service.placement_identity import placement_id_claim_from_description, placement_id_from_description

SELLER_SOURCES = {
    'auto_ru': ['https://auto.ru/diler/cars/all/a1_avto_moskva/',
                'https://auto.ru/diler/lcv/all/a1_avto_moskva/'],
    'avito': ['https://www.avito.ru/brands/a1auto/all/avtomobili?src=search_seller_info&iid=8047929828&sellerId=e4036231f69cc5cfc365db2f79600230'],
}


def seller_page_matches(requested, final):
    def identity(url):
        parts = urlsplit(url)
        return parts.hostname, re.sub(r'/items(?=/all/)', '', parts.path).rstrip('/')
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
    description = first_text((
        '[data-marker="item-view/item-description"]',
        '[itemprop="description"]',
        '[class*="CardOfferDescription"]',
        '[class*="Description"]',
    ))
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
    price_digits = re.sub(r'[^0-9]', '', price_text or '')
    return {
        'title': title,
        'price': int(price_digits) if price_digits else None,
        'year': int(years[0]) if years else None,
        'vin': vins[0] if len(vins) == 1 else None,
        'availability': availability,
        'vat_status': vat_status,
        'vat_required': source == EngineType.AVITO,
        'description_excerpt': (description or '')[:1800] or None,
        'placement_id_raw': placement_id_claim_from_description(description),
        'placement_id': placement_id_from_description(description),
    }


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
    if http_status is not None and http_status >= 400:
        return {'state': 'unknown', 'status_code': 'http_error', 'reason': f'HTTP {http_status}; это не доказывает продажу'}
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


async def inspect_direct_link(source, url, progress=None):
    delay = choose_pause(settings.scan_page_pause_min_seconds, settings.scan_page_pause_max_seconds)
    if progress:
        progress({'event': 'dealer_link_check', 'source': source.value, 'url': url, 'wait_seconds': round(delay, 1)})
    await asyncio.sleep(delay)
    try:
        async with async_playwright() as p:
            async with browser_page(p) as page:
                response = await page.goto(url, timeout=settings.request_timeout_seconds * 1000, wait_until='domcontentloaded')
                await asyncio.sleep(settings.auto_ru_page_delay_seconds if source.value == 'auto_ru' else settings.avito_page_delay_seconds)
                html = await page.content()
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
                result = direct_page_status(html, source, url, page.url, response.status if response else None)
                if recovery.outcome in {'unresolved', 'operator_timeout', 'wrong_destination',
                                        'challenge_evidence_missing', 'no_document_response'}:
                    reason = ('Не получен ответ страницы' if recovery.outcome == 'no_document_response'
                              else f'CAPTCHA {recovery.outcome}')
                    result = {'state': 'blocked', 'status_code': 'blocked',
                              'reason': reason}
                if recovery.refreshes:
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
