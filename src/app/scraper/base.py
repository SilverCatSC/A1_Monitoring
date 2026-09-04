from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from app.models import EngineType


@dataclass
class SearchFilterDefinition:
    source: EngineType
    external_key: str
    name: str
    url: str
    criteria: dict[str, Any]
    expected_position: int | None = None


@dataclass
class ListingHit:
    external_id: str
    title: str
    url: str
    page_number: int
    position: int
    price: float | None
    raw: dict[str, Any]


@dataclass
class ScanResult:
    filter_id: str
    page_count: int
    hits: list[ListingHit]
    diagnostics: dict[str, Any]
    scanned_at: datetime
    requested_pages: int = 0
    complete: bool = True
    exhausted: bool = False
    error: str | None = None


BLOCK_PAGE_MARKERS = (
    'подтвердите, что вы не робот',
    'доступ ограничен',
    'проверка браузера',
    'access denied',
    'verify you are human',
    'cf-chl-',
    'showcaptcha',
)

EMPTY_PAGE_MARKERS = (
    'ничего не найдено',
    'объявлений не найдено',
    'по вашему запросу ничего',
    'нет подходящих объявлений',
    'items-not-found',
    'search-no-results',
)


def classify_result_page(html: str, card_count: int) -> tuple[str, str | None]:
    """Classify a result page without turning parser failures into false absences."""
    visible_text = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True).lower()
    lower_html = html.lower()
    for marker in BLOCK_PAGE_MARKERS:
        if marker in visible_text or marker in lower_html:
            return 'blocked', marker
    if card_count > 0:
        return 'results', None
    for marker in EMPTY_PAGE_MARKERS:
        if marker in visible_text or marker in lower_html:
            return 'empty', marker
    return 'unrecognized', 'no listing cards and no explicit empty-result marker'


def canonical_listing_key(source: EngineType, value: str | None) -> str | None:
    """Return a stable marketplace listing key and discard tracking query parameters."""
    raw = str(value or '').strip()
    if not raw:
        return None
    parsed = urlparse(raw if '://' in raw else f'https://{raw.lstrip("/")}')
    host = (parsed.hostname or '').lower().removeprefix('www.')
    path = unquote(parsed.path).rstrip('/').lower()

    if source == EngineType.AUTO_RU:
        if not _host_matches(host, 'auto.ru'):
            return None
        numeric_ids = re.findall(r'(?<!\d)(\d{5,})(?!\d)', path)
        if numeric_ids:
            return f'auto_ru:{numeric_ids[-1]}'
        return f'auto_ru:url:{host}{path}' if path else None

    if source == EngineType.AVITO:
        if not _host_matches(host, 'avito.ru'):
            return None
        match = re.search(r'_(\d{5,})(?:$|/)', path)
        if match:
            return f'avito:{match.group(1)}'
        numeric_ids = re.findall(r'(?<!\d)(\d{5,})(?!\d)', path)
        if numeric_ids:
            return f'avito:{numeric_ids[-1]}'
        return f'avito:url:{host}{path}' if path else None

    return None


def is_marketplace_search_url(source: EngineType, value: str | None) -> bool:
    raw = str(value or '').strip()
    if not raw.startswith(('http://', 'https://')):
        return False
    parsed = urlparse(raw)
    host = (parsed.hostname or '').lower().removeprefix('www.')
    path = unquote(parsed.path).rstrip('/').lower()

    if source == EngineType.AUTO_RU:
        if not _host_matches(host, 'auto.ru') or '/diler/' in path:
            return False
        if canonical_listing_key(source, raw) and re.search(r'/sale/', path):
            return False
        return '/cars/' in path or '/lcv/' in path

    if source == EngineType.AVITO:
        if not _host_matches(host, 'avito.ru'):
            return False
        if re.search(r'_\d{5,}(?:$|/)', path):
            return False
        return '/brands/' in path or '/avtomobili' in path

    return False


def is_marketplace_listing_url(source: EngineType, value: str | None) -> bool:
    raw = str(value or '').strip()
    key = canonical_listing_key(source, raw)
    if not key or ':url:' in key:
        return False
    path = unquote(urlparse(raw).path).lower()
    if source == EngineType.AUTO_RU:
        return '/sale/' in path or '/cars/new/group/' in path
    if source == EngineType.AVITO:
        return bool(re.search(r'_\d{5,}(?:$|/)', path.rstrip('/')))
    return False


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f'.{domain}')


async def capture_page_evidence(
    page: Any,
    *,
    source: EngineType,
    search_url: str,
    page_number: int,
    evidence_dir: str,
) -> str | None:
    """Capture a viewport screenshot; evidence failure never changes the scan fact."""
    try:
        root = Path(evidence_dir)
        root.mkdir(parents=True, exist_ok=True)
        url_hash = hashlib.sha256(search_url.encode('utf-8')).hexdigest()[:12]
        timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
        path = root / f'{source.value}_{url_hash}_{timestamp}_p{page_number}.png'
        await page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception:
        return None


def detect_filters_in_row(row: dict[str, Any]) -> list[SearchFilterDefinition]:
    filters: list[SearchFilterDefinition] = []
    seen_urls: set[str] = set()

    def _push_filter(
        source: EngineType, column: str, value: Any, criteria: dict[str, Any] | None = None
    ) -> None:
        url = str(value or '').strip()
        if not url:
            return
        if url.startswith('/'):
            return
        if not url.startswith('http') or not is_marketplace_search_url(source, url):
            return
        normalized = url.lower().rstrip('/')
        if normalized in seen_urls:
            return
        seen_urls.add(normalized)
        filters.append(
            SearchFilterDefinition(
                source=source,
                external_key=str(hashlib_safe(url)),
                name=_url_label(source.value.replace('_', ' ').title(), column, str(url)),
                url=str(url),
                criteria=criteria or {'source': column, 'url_detected_from': 'cell'},
            )
        )

    for col in ('search_url_auto_ru', 'auto_filter', 'auto_filters', 'auto_filter_url'):
        value = row.get(col)
        if value and str(value).strip().startswith('http'):
            _push_filter(EngineType.AUTO_RU, col, value, {'source': col})

    for col in ('search_url_avito', 'avito_filter', 'avito_filters', 'avito_filter_url'):
        value = row.get(col)
        if value and str(value).strip().startswith('http'):
            _push_filter(EngineType.AVITO, col, value, {'source': col})

    for key, value in row.items():
        raw_key = str(key or '').strip().lower()
        if not raw_key:
            continue
        if raw_key in {
            'search_url_auto_ru',
            'dealer_url_auto_ru',
            'auto_filter',
            'auto_filters',
            'auto_filter_url',
            'search_url_avito',
            'dealer_url_avito',
            'avito_filter',
            'avito_filters',
            'avito_filter_url',
            'listing_url',
            'listing_url_auto_ru',
            'listing_url_avito',
            'direct_url',
        }:
            continue
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not candidate.startswith('http'):
            continue
        parsed = urlparse(candidate)
        host = (parsed.hostname or '').lower()
        if _host_matches(host, 'auto.ru'):
            _push_filter(EngineType.AUTO_RU, raw_key, candidate, {'source': raw_key, 'detected': True})
        elif _host_matches(host, 'avito.ru'):
            _push_filter(EngineType.AVITO, raw_key, candidate, {'source': raw_key, 'detected': True})

    return filters


def hashlib_safe(value: Any) -> str:
    text = str(value or '').strip()
    h = 0
    for ch in text:
        h = (h * 31 + ord(ch)) % (10**9 + 7)
    return str(h)


def _url_label(prefix: str, column: str, value: str) -> str:
    suffix = re.sub(r'[^a-zA-Z0-9_-]+', '-', value.split('?')[0][-30:])
    return f'{prefix} {column} {suffix}'.strip()
