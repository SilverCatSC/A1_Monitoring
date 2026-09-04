from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

CANONICAL_FIELDS = {
    'vehicle_signature',
    'brand',
    'model',
    'generation',
    'year',
    'vin',
    'source_status',
    'priority',
    'configuration',
    'search_url_auto_ru',
    'search_url_avito',
    'listing_url_auto_ru',
    'listing_url_avito',
    'listing_url',
    'dealer_url_auto_ru',
    'dealer_url_avito',
    'price_hint',
    'notes',
}


REQUIRED_CANONICAL: set[str] = set()


HEADER_ALIASES: dict[str, str] = {
    'vin': 'vin',
    'vin_дата': 'vin',
    'vin_номер': 'vin',
    'вин': 'vin',
    'вин_номер': 'vin',
    'vehicle_signature': 'vehicle_signature',
    'айди': 'vehicle_signature',
    'id': 'vehicle_signature',
    'марка': 'brand',
    'бренд': 'brand',
    'марка_модель': 'brand',
    'модель': 'model',
    'комплектация_а1': 'configuration',
    'комплектация_a1': 'configuration',
    'поколение': 'generation',
    'год': 'year',
    'год_выпуска': 'year',
    'статус': 'source_status',
    'source_status': 'source_status',
    'приоритет': 'priority',
    'priority': 'priority',
    'ссылка_auto_ru': 'listing_url_auto_ru',
    'ссылка_avito_ru': 'listing_url_avito',
    'listing_url_auto_ru': 'listing_url_auto_ru',
    'listing_url_avito': 'listing_url_avito',
    'search_url_auto_ru': 'search_url_auto_ru',
    'фильтр_auto_ru': 'search_url_auto_ru',
    'auto_filter': 'search_url_auto_ru',
    'auto_filter_url': 'search_url_auto_ru',
    'auto_ru': 'listing_url_auto_ru',
    'search_url_avito': 'search_url_avito',
    'фильтр_avito_ru': 'search_url_avito',
    'avito_filter': 'search_url_avito',
    'avito_filter_url': 'search_url_avito',
    'avito_ru': 'listing_url_avito',
    'avito': 'listing_url_avito',
    'listing_url': 'listing_url',
    'dealer_url_auto_ru': 'dealer_url_auto_ru',
    'dealer_auto_ru': 'dealer_url_auto_ru',
    'dealer_url_avito': 'dealer_url_avito',
    'dealer_avito': 'dealer_url_avito',
    'цена': 'price_hint',
    'стоимость': 'price_hint',
    'price': 'price_hint',
    'заметки': 'notes',
    'notes': 'notes',
}


@dataclass
class SourceRecord:
    row_number: int
    source: dict[str, str | int | float | None]
    raw: dict[str, Any]


def normalize_header(header: str) -> str:
    text = unicodedata.normalize('NFKC', str(header)).strip().lower().replace('ё', 'е')
    text = re.sub(r'[^0-9a-zа-я]+', '_', text, flags=re.IGNORECASE)
    return text.strip('_')


def canonicalize_headers(headers: Iterable[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for header in headers:
        key = normalize_header(str(header))
        canonical = HEADER_ALIASES.get(key)
        if canonical:
            mapping[key] = canonical
            continue
        # keep non-empty unmapped names under lower_snake_case
        mapping[key] = key
    return mapping


def map_row(headers_map: dict[str, str], row: dict[str, Any]) -> dict[str, str | int | float | None]:
    normalized: dict[str, str | int | float | None] = {}
    for raw_key, value in row.items():
        key = normalize_header(str(raw_key))
        target = headers_map.get(key, key)

        if isinstance(value, float) and value != value:
            value = None

        # Признак: несколько синонимных колонок могут существовать в одной строке
        # (например, Brand + Марка). Берём первый не пустой вариант.
        current = normalized.get(target)
        if current is not None and current == current:
            continue
        if value is None or value != value:
            continue
        normalized[target] = value

        # Если это уже было первое заполненное значение, не перезаписываем его далее.
    return normalized
