from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

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
        if not url.startswith('http'):
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

    for col in ('search_url_auto_ru', 'dealer_url_auto_ru', 'auto_filter', 'auto_filters', 'auto_filter_url'):
        value = row.get(col)
        if value and str(value).strip().startswith('http'):
            _push_filter(EngineType.AUTO_RU, col, value, {'source': col})

    for col in ('search_url_avito', 'dealer_url_avito', 'avito_filter', 'avito_filters', 'avito_filter_url'):
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
        if host.endswith('auto.ru') or host.endswith('auto.drom.ru'):
            _push_filter(EngineType.AUTO_RU, raw_key, candidate, {'source': raw_key, 'detected': True})
        elif host.endswith('avito.ru'):
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
