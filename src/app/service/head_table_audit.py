"""Read-only reconciliation of marketplace cards against the head-office table.

The deterministic layer owns matching and field comparison.  AI agents receive
only the bounded result and may explain or prioritise it; they must not invent
values that were not present in the table or a saved marketplace card.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.importer.sheet_csv import CsvOrXlsxReader
from app.models import (
    EngineType,
    Listing,
    ListingObservation,
    ListingReconciliation,
    ObservationState,
)
from app.scraper.base import canonical_company_site_key, canonical_listing_key

HEAD_TABLE_SHEET = 'Размещение_автомобилей'
ACTIVE_STATUSES = {'актуально', 'активно', 'active'}
VIN_PATTERN = re.compile(r'\b[A-HJ-NPR-Z0-9]{17}\b')


def _text(value: Any) -> str | None:
    value = str(value or '').strip()
    return value or None


def _fold(value: Any) -> str:
    return re.sub(r'[^0-9a-zа-я]+', '', str(value or '').casefold().replace('ё', 'е'))


def _vin(value: Any) -> str | None:
    matches = set(VIN_PATTERN.findall(str(value or '').upper()))
    return next(iter(matches)) if len(matches) == 1 else None


def _money(value: Any) -> float | None:
    digits = re.sub(r'[^0-9]', '', str(value or ''))
    return float(digits) if digits else None


def _year(value: Any) -> int | None:
    digits = re.sub(r'[^0-9]', '', str(value or ''))
    return int(digits) if len(digits) == 4 else None


def _active(value: Any) -> bool:
    return _fold(value) in ACTIVE_STATUSES


def _same_money(left: float | None, right: float | None) -> bool | None:
    if left is None or right is None:
        return None
    return round(left) == round(right)


def _same_text(left: str | None, right: str | None) -> bool | None:
    if not left or not right:
        return None
    return _fold(left) == _fold(right)


def _same_url(source: EngineType, left: str | None, right: str | None) -> bool | None:
    left_key = canonical_listing_key(source, left)
    right_key = canonical_listing_key(source, right)
    if not left_key or not right_key:
        return None
    return left_key == right_key


def _same_company_site_url(left: str | None, right: str | None) -> bool | None:
    left_key = canonical_company_site_key(left)
    right_key = canonical_company_site_key(right)
    if not left_key or not right_key:
        return None
    return left_key == right_key


def _vehicle_key(
    vin: str | None,
    auto_ru_url: str | None,
    avito_url: str | None,
    site_url: str | None,
    brand_model: str | None,
    configuration: str | None,
    year: int | None,
    price: float | None,
    row_number: int,
) -> tuple[str, str]:
    if vin:
        return f'vin:{vin}', 'vin'
    for source, url in ((EngineType.AUTO_RU, auto_ru_url), (EngineType.AVITO, avito_url)):
        key = canonical_listing_key(source, url)
        if key:
            return key, 'marketplace_url'
    site_key = canonical_company_site_key(site_url)
    if site_key:
        return site_key, 'company_site_url'
    parts = [_fold(brand_model), _fold(configuration), str(year or ''), str(round(price or 0))]
    if parts[0] and any(parts[1:]):
        return f'composite:{"|".join(parts)}', 'composite'
    return f'head_row:{row_number}', 'row_only'


def _head_row(row_number: int, values: dict[str, Any]) -> dict[str, Any] | None:
    vin = _vin(values.get('vin'))
    brand_model = _text(values.get('brand_model'))
    configuration = _text(values.get('configuration'))
    price = _money(values.get('price_hint'))
    year = _year(values.get('year'))
    auto_ru_url = _text(values.get('listing_url_auto_ru'))
    avito_url = _text(values.get('listing_url_avito'))
    site_url = _text(values.get('listing_url') or values.get('direct_url'))
    if not any((vin, brand_model, configuration, price, auto_ru_url, avito_url, site_url)):
        return None
    vehicle_key, identity_basis = _vehicle_key(
        vin, auto_ru_url, avito_url, site_url, brand_model, configuration, year, price, row_number
    )
    return {
        'row_number': row_number,
        'vin': vin,
        'vehicle_key': vehicle_key,
        'identity_basis': identity_basis,
        'brand_model': brand_model,
        'configuration': configuration,
        'price': price,
        'year': year,
        'mileage': _text(values.get('пробег')),
        'vat': _text(values.get('ндс')),
        'availability': _text(values.get('наличие')),
        'status': _text(values.get('source_status')),
        'active': _active(values.get('source_status')),
        'auto_ru_url': auto_ru_url,
        'avito_url': avito_url,
        'site_url': site_url,
    }


def _issue(
    code: str,
    severity: str,
    record: dict[str, Any],
    **details: Any,
) -> dict[str, Any]:
    return {
        'code': code,
        'severity': severity,
        'vehicle_key': record['vehicle_key'],
        'vin': record.get('vin'),
        'identity_basis': record['identity_basis'],
        'head_row': record['row_number'],
        **details,
    }


def _latest_found_cards(db: Session) -> dict[tuple[str, EngineType], ListingObservation]:
    rows = (
        db.query(ListingObservation)
        .filter(ListingObservation.state == ObservationState.FOUND)
        .order_by(ListingObservation.observed_at.desc(), ListingObservation.id.desc())
        .all()
    )
    latest: dict[tuple[str, EngineType], ListingObservation] = {}
    for row in rows:
        latest.setdefault((row.listing_id, row.source), row)
    return latest


def _latest_direct_cards(db: Session) -> dict[tuple[str, EngineType], ListingReconciliation]:
    rows = (
        db.query(ListingReconciliation)
        .order_by(ListingReconciliation.checked_at.desc(), ListingReconciliation.id.desc())
        .all()
    )
    latest: dict[tuple[str, EngineType], ListingReconciliation] = {}
    for row in rows:
        current_url = (
            row.listing.source_auto_ru
            if row.source == EngineType.AUTO_RU
            else row.listing.source_avito
        )
        if canonical_listing_key(row.source, row.url) != canonical_listing_key(
            row.source, current_url
        ):
            continue
        direct = (row.details or {}).get('direct_inspection')
        if isinstance(direct, dict):
            latest.setdefault((row.listing_id, row.source), row)
    return latest


def _listing_name(listing: Listing) -> str | None:
    return ' '.join(value for value in (listing.brand, listing.model) if value) or None


def _content_sample(record: dict[str, Any], source: EngineType, card: ListingObservation) -> dict[str, Any]:
    payload = card.raw_payload or {}
    return {
        'vin': record.get('vin'),
        'vehicle_key': record['vehicle_key'],
        'identity_basis': record['identity_basis'],
        'head_row': record['row_number'],
        'source': source.value,
        'observed_at': card.observed_at.isoformat(),
        'page_number': card.page_number,
        'listing_url': card.listing_url,
        'title': card.title,
        'card_text': str(payload.get('raw_text') or '')[:1200],
        'card_price': card.price_hint,
        'head': {
            key: record[key]
            for key in ('brand_model', 'configuration', 'price', 'year', 'mileage', 'vat', 'availability', 'site_url')
        },
        'card_evidence': payload.get('card_evidence'),
    }


def _direct_card_sample(
    record: dict[str, Any], source: EngineType, reconciliation: ListingReconciliation
) -> dict[str, Any]:
    direct = (reconciliation.details or {}).get('direct_inspection') or {}
    card = direct.get('card') if isinstance(direct.get('card'), dict) else {}
    return {
        'vin': record.get('vin'),
        'vehicle_key': record['vehicle_key'],
        'identity_basis': record['identity_basis'],
        'head_row': record['row_number'],
        'source': source.value,
        'checked_at': reconciliation.checked_at.isoformat(),
        'listing_url': reconciliation.url,
        'reconciliation_state': reconciliation.state,
        'direct_state': direct.get('state'),
        'status_code': direct.get('status_code') or direct.get('state'),
        'reason': direct.get('reason'),
        'card': card,
        'head': {
            key: record[key]
            for key in (
                'brand_model',
                'configuration',
                'price',
                'year',
                'mileage',
                'vat',
                'availability',
                'status',
                'site_url',
            )
        },
        'card_evidence': direct.get('evidence'),
    }


def audit_head_table(db: Session) -> dict[str, Any]:
    """Build a bounded, read-only comparison report for the local AI contour."""
    source_url = settings.head_table_google_sheet_export_url
    if not source_url:
        raise RuntimeError('HEAD_TABLE_GOOGLE_SHEET_EXPORT_URL is not configured')

    imported = CsvOrXlsxReader(source_url).read()
    records: list[dict[str, Any]] = []
    empty_rows: list[int] = []
    by_vin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in imported:
        record = _head_row(item.row_number, item.source)
        if record is None:
            if any(item.source.values()):
                empty_rows.append(item.row_number)
            continue
        records.append(record)
        if record['vin']:
            by_vin[record['vin']].append(record)

    listings = db.query(Listing).all()
    listings_by_vin = {
        listing.vin.strip().upper(): listing
        for listing in listings
        if listing.vin and _vin(listing.vin)
    }
    listings_by_url: dict[tuple[EngineType, str], list[Listing]] = defaultdict(list)
    listings_by_site_url: dict[str, list[Listing]] = defaultdict(list)
    for listing in listings:
        for source, url in (
            (EngineType.AUTO_RU, listing.source_auto_ru),
            (EngineType.AVITO, listing.source_avito),
        ):
            key = canonical_listing_key(source, url)
            if key:
                listings_by_url[(source, key)].append(listing)
        site_key = canonical_company_site_key(listing.direct_url)
        if site_key:
            listings_by_site_url[site_key].append(listing)
    latest_cards = _latest_found_cards(db)
    latest_direct_cards = _latest_direct_cards(db)
    issues: list[dict[str, Any]] = []
    content_samples: list[dict[str, Any]] = []
    direct_card_samples: list[dict[str, Any]] = []

    for duplicates in by_vin.values():
        if len(duplicates) > 1:
            active_rows = [record['row_number'] for record in duplicates if record['active']]
            issues.append(
                _issue(
                    'head_duplicate_vin',
                    'high' if len(active_rows) > 1 else 'medium',
                    duplicates[0],
                    duplicate_rows=[record['row_number'] for record in duplicates],
                    active_rows=active_rows,
                )
            )

    for record in records:
        listing = listings_by_vin.get(record['vin']) if record['vin'] else None
        if listing is None:
            candidates: dict[str, Listing] = {}
            for source, url in ((EngineType.AUTO_RU, record['auto_ru_url']), (EngineType.AVITO, record['avito_url'])):
                key = canonical_listing_key(source, url)
                for candidate in listings_by_url.get((source, key), []) if key else []:
                    candidates[candidate.id] = candidate
            site_key = canonical_company_site_key(record['site_url'])
            for candidate in listings_by_site_url.get(site_key, []) if site_key else []:
                candidates[candidate.id] = candidate
            if len(candidates) == 1:
                listing = next(iter(candidates.values()))
        if listing is None:
            if record['active']:
                issues.append(_issue('active_head_record_unmatched', 'high', record))
            continue

        if record['active'] != listing.is_active:
            issues.append(
                _issue(
                    'registry_activity_mismatch',
                    'high' if record['active'] else 'medium',
                    record,
                    head_status=record['status'],
                    registry_active=listing.is_active,
                )
            )

        comparisons = (
            ('brand_model', record['brand_model'], _listing_name(listing), _same_text),
            ('configuration', record['configuration'], listing.generation, _same_text),
            ('year', record['year'], listing.year, lambda left, right: left == right if left and right else None),
            ('price', record['price'], listing.price_hint, _same_money),
        )
        for field, head_value, registry_value, compare in comparisons:
            equal = compare(head_value, registry_value)
            if equal is False:
                issues.append(
                    _issue(
                        'registry_field_mismatch',
                        'high' if field == 'price' else 'medium',
                        record,
                        field=field,
                        head_value=head_value,
                        registry_value=registry_value,
                    )
                )

        for source, head_url, registry_url in (
            (EngineType.AUTO_RU, record['auto_ru_url'], listing.source_auto_ru),
            (EngineType.AVITO, record['avito_url'], listing.source_avito),
        ):
            same_url = _same_url(source, head_url, registry_url)
            if same_url is False:
                issues.append(
                    _issue(
                        'registry_link_mismatch',
                        'high',
                        record,
                        source=source.value,
                        head_url=head_url,
                        registry_url=registry_url,
                    )
                )
            card = latest_cards.get((listing.id, source))
            if card is not None:
                sample = _content_sample(record, source, card)
                content_samples.append(sample)
                if not sample['card_evidence']:
                    issues.append(
                        _issue(
                            'marketplace_card_evidence_missing',
                            'medium',
                            record,
                            source=source.value,
                            listing_url=card.listing_url,
                            page_number=card.page_number,
                        )
                    )
                if not record['active']:
                    issues.append(
                        _issue(
                            'inactive_head_record_visible',
                            'medium',
                            record,
                            source=source.value,
                            head_status=record['status'],
                            observed_at=card.observed_at.isoformat(),
                            listing_url=card.listing_url,
                        )
                    )
                if _same_money(record['price'], card.price_hint) is False:
                    issues.append(
                        _issue(
                            'marketplace_price_mismatch',
                            'high',
                            record,
                            source=source.value,
                            head_price=record['price'],
                            card_price=card.price_hint,
                            observed_at=card.observed_at.isoformat(),
                            listing_url=card.listing_url,
                            evidence=(card.raw_payload or {}).get('card_evidence'),
                        )
                    )
                if _same_url(source, head_url, card.listing_url) is False:
                    issues.append(
                        _issue(
                            'marketplace_link_mismatch',
                            'medium',
                            record,
                            source=source.value,
                            head_url=head_url,
                            card_url=card.listing_url,
                            observed_at=card.observed_at.isoformat(),
                        )
                    )

            direct_record = latest_direct_cards.get((listing.id, source))
            if direct_record is None:
                continue
            direct_sample = _direct_card_sample(record, source, direct_record)
            direct_card_samples.append(direct_sample)
            direct_card = direct_sample['card']
            status_code = direct_sample.get('status_code')
            if status_code in {'removed', 'sold', 'unpublished', 'closed'}:
                issues.append(
                    _issue(
                        'direct_card_inactive',
                        'high',
                        record,
                        source=source.value,
                        listing_url=direct_record.url,
                        status_code=status_code,
                        reason=direct_sample.get('reason'),
                        evidence=direct_sample.get('card_evidence'),
                    )
                )
            if status_code != 'active':
                continue
            if source == EngineType.AVITO and not direct_card.get('vat_status'):
                issues.append(
                    _issue(
                        'avito_vat_missing_in_description',
                        'high',
                        record,
                        source=source.value,
                        listing_url=direct_record.url,
                        evidence=direct_sample.get('card_evidence'),
                    )
                )
            if _same_text(record.get('vat'), direct_card.get('vat_status')) is False:
                issues.append(
                    _issue(
                        'direct_card_vat_mismatch',
                        'high',
                        record,
                        source=source.value,
                        head_vat=record.get('vat'),
                        card_vat=direct_card.get('vat_status'),
                        listing_url=direct_record.url,
                        evidence=direct_sample.get('card_evidence'),
                    )
                )
            if _same_money(record.get('price'), direct_card.get('price')) is False:
                issues.append(
                    _issue(
                        'direct_card_price_mismatch',
                        'high',
                        record,
                        source=source.value,
                        head_price=record.get('price'),
                        card_price=direct_card.get('price'),
                        listing_url=direct_record.url,
                        evidence=direct_sample.get('card_evidence'),
                    )
                )
            if record.get('year') and direct_card.get('year') and record['year'] != direct_card['year']:
                issues.append(
                    _issue(
                        'direct_card_year_mismatch',
                        'medium',
                        record,
                        source=source.value,
                        head_year=record.get('year'),
                        card_year=direct_card.get('year'),
                        listing_url=direct_record.url,
                        evidence=direct_sample.get('card_evidence'),
                    )
                )
            if record.get('vin') and direct_card.get('vin') and record['vin'] != direct_card['vin']:
                issues.append(
                    _issue(
                        'direct_card_vin_mismatch',
                        'high',
                        record,
                        source=source.value,
                        head_vin=record.get('vin'),
                        card_vin=direct_card.get('vin'),
                        listing_url=direct_record.url,
                        evidence=direct_sample.get('card_evidence'),
                    )
                )

        same_site_url = _same_company_site_url(record['site_url'], listing.direct_url)
        if same_site_url is False:
            issues.append(
                _issue(
                    'registry_company_site_link_mismatch',
                    'medium',
                    record,
                    source='a1_site',
                    head_url=record['site_url'],
                    registry_url=listing.direct_url,
                )
            )

    severity_counts = {level: sum(issue['severity'] == level for issue in issues) for level in ('high', 'medium', 'low')}
    return {
        'schema_version': 1,
        'generated_at': datetime.now(UTC).isoformat(),
        'head_table': {
            'title': 'A1 АВТО // Digital // Размещение автомобилей',
            'sheet': HEAD_TABLE_SHEET,
            'url': source_url,
            'raw_rows': len(imported),
            'records_with_single_vin': sum(record['vin'] is not None for record in records),
            'records_without_vin': sum(record['vin'] is None for record in records),
            'empty_or_unidentifiable_rows': empty_rows[:100],
        },
        'summary': {
            'active_head_records': sum(record['active'] for record in records),
            'active_head_records_without_vin': sum(
                record['active'] and record['vin'] is None for record in records
            ),
            'registry_records': len(listings),
            'issues_total': len(issues),
            'issues_by_severity': severity_counts,
            'content_samples': len(content_samples),
            'direct_card_samples': len(direct_card_samples),
        },
        'issues': issues[:250],
        # Business-facing snapshot. It preserves the table facts used during
        # this audit without making the dashboard call Google Sheets itself.
        'records': records[:500],
        'direct_card_samples': direct_card_samples[:250],
        # A bounded card-only input for AI content assessment.  It contains no
        # unvisited listing details and remains independently verifiable.
        'content_samples': content_samples[:120],
    }
