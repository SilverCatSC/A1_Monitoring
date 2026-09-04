from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.contracts import CANONICAL_FIELDS, SourceRecord, split_brand_model
from app.models import (
    EngineType,
    ImportFieldDrift,
    Listing,
    SearchFilter,
    SourceImportSnapshot,
    VehicleFilterExpectation,
)
from app.scraper.base import (
    SearchFilterDefinition,
    detect_filters_in_row,
    is_marketplace_listing_url,
    is_marketplace_search_url,
)


class SourceImportError(RuntimeError):
    pass


def _canonical_signature(row: dict[str, Any]) -> str | None:
    vin = str(row.get('vin') or '').strip().upper()
    if vin and vin != 'НЕ УКАЗАН':
        return hashlib.sha256(f'vin|{vin}'.encode()).hexdigest()
    signature_parts = [
        str(row.get('vehicle_signature') or '').strip().lower(),
        str(row.get('brand') or '').strip().lower(),
        str(row.get('model') or '').strip().lower(),
        str(row.get('generation') or '').strip().lower(),
        str(row.get('year') or '').strip(),
    ]
    if not any(signature_parts):
        return None
    base = '|'.join(signature_parts).encode('utf-8')
    return hashlib.sha256(base).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _has_multiple_vins(value: Any) -> bool:
    text = str(value or '').upper()
    candidates = re.findall(r'\b[A-HJ-NPR-Z0-9]{17}\b', text)
    return len(set(candidates)) > 1 or '\n' in text.strip()


def _is_active_status(value: Any) -> bool:
    status = str(value or '').strip().lower()
    return status in {'', 'актуально', 'активно', 'active', 'enabled'}


def _coerce_filters(row: dict[str, Any]) -> list[SearchFilterDefinition]:
    return detect_filters_in_row(row)


def _normalize_vehicle_names(row: dict[str, Any]) -> None:
    if not row.get('brand_model'):
        return
    parsed_brand, parsed_model = split_brand_model(row.get('brand_model'))
    if not row.get('brand') and parsed_brand:
        row['brand'] = parsed_brand
    if not row.get('model') and parsed_model:
        row['model'] = parsed_model


class SourceImporter:
    def __init__(self, db: Session):
        self.db = db

    def run(self, rows: list[SourceRecord], source_signature: str | None = None) -> dict[str, int]:
        if rows is None:
            rows = []

        parsed_rows: list[tuple[dict[str, Any], list[SearchFilterDefinition], str | None]] = []
        snapshot = SourceImportSnapshot(
            started_at=_utcnow(),
            source_signature=source_signature,
            raw_rows=len(rows),
            invalid_rows=0,
            valid_rows=0,
            blocked_by_schema_drift=False,
            notes=None,
        )
        self.db.add(snapshot)
        self.db.flush()

        missing = 0
        valid = 0
        drifted = False
        has_anchor = False
        unknown_headers: set[str] = set()
        raw_headers: list[str] = []
        required_missing: list[str] = []
        invalid_data: list[str] = []
        seen_vins: set[str] = set()
        present_signatures: set[str] = set()
        optional_unknown_allowed = {
            'source_status',
            'status',
            'priority',
            'source_updated_at',
            'source_notes',
            'note',
            'configuration',
        }

        if rows:
            raw_headers = list(rows[0].source.keys())
            unknown_headers.update(
                key
                for key in raw_headers
                if key not in CANONICAL_FIELDS and key not in optional_unknown_allowed
            )

        for item in rows:
            row = {k: v for k, v in item.source.items() if v is not None and str(v).strip() != ''}
            if not row:
                missing += 1
                continue
            _normalize_vehicle_names(row)

            anchor_keys = ('vehicle_signature', 'brand', 'model', 'generation', 'year', 'vin')
            if any(row.get(key) for key in anchor_keys):
                has_anchor = True
            else:
                required_missing.append(f'row:{item.row_number}')

            vin = str(row.get('vin') or '').strip().upper()
            vin_candidates = set(re.findall(r'\b[A-HJ-NPR-Z0-9]{17}\b', vin))
            present_signatures.update(
                hashlib.sha256(f'vin|{candidate}'.encode()).hexdigest()
                for candidate in vin_candidates
            )
            if _has_multiple_vins(vin):
                invalid_data.append(f'row:{item.row_number}:multiple_vin')
                missing += 1
                continue
            if vin and vin in seen_vins:
                invalid_data.append(f'row:{item.row_number}:duplicate_vin')
                missing += 1
                continue
            if vin:
                seen_vins.add(vin)

            signature = _canonical_signature(row)
            if not signature:
                missing += 1
                continue

            parsed_rows.append((row, _coerce_filters(row), signature))
            present_signatures.add(signature)
            valid += 1

        previous_snapshot = (
            self.db.query(SourceImportSnapshot)
            .filter(
                SourceImportSnapshot.id != snapshot.id,
                SourceImportSnapshot.finished_at.is_not(None),
                SourceImportSnapshot.blocked_by_schema_drift.is_(False),
            )
            .order_by(SourceImportSnapshot.started_at.desc())
            .first()
        )
        if (
            previous_snapshot is not None
            and previous_snapshot.valid_rows >= 10
            and valid < previous_snapshot.valid_rows * settings.import_min_valid_ratio
        ):
            drifted = True
            snapshot.notes = (snapshot.notes or '') + (
                f' Valid-row count fell from {previous_snapshot.valid_rows} to {valid}; '
                'last-good registry preserved.'
            )

        if not has_anchor:
            drifted = True

        if required_missing:
            snapshot.notes = (snapshot.notes or '') + f'Rows with missing anchor: {required_missing[:20]}'

        if drifted:
            self.db.add(
                ImportFieldDrift(
                    snapshot_id=snapshot.id,
                    missing_required=required_missing[:20],
                    unknown=sorted(unknown_headers),
                    raw_headers=raw_headers,
                )
            )
            snapshot.finished_at = _utcnow()
            snapshot.valid_rows = valid
            snapshot.invalid_rows = missing
            snapshot.blocked_by_schema_drift = drifted
            self.db.commit()
            raise SourceImportError(
                'Import quarantined; check schema/data diagnostics in /api/v1/status/imports'
            )

        if invalid_data:
            self.db.add(
                ImportFieldDrift(
                    snapshot_id=snapshot.id,
                    missing_required=[],
                    unknown=sorted(unknown_headers),
                    raw_headers=raw_headers,
                )
            )
            snapshot.notes = (snapshot.notes or '') + f' Rejected rows: {invalid_data[:20]}'

        # Safe point: write restructured data only after schema checks pass.
        try:
            for row, filters, signature in parsed_rows:
                listing = self.db.query(Listing).filter(Listing.vehicle_signature == signature).one_or_none()
                if listing is None:
                    listing = Listing(
                        vehicle_signature=signature,
                    )
                    self.db.add(listing)
                    self.db.flush()

                listing.brand = _clean_optional(row.get('brand'))
                listing.model = _clean_optional(row.get('model'))
                listing.generation = _clean_optional(row.get('generation') or row.get('configuration'))
                year = _clean_optional(row.get('year'))
                listing.year = int(float(year)) if year and year.replace('.', '', 1).isdigit() else None
                listing.vin = _clean_optional(row.get('vin'))
                raw_auto_ru = _clean_optional(
                    row.get('listing_url_auto_ru') or row.get('source_auto_ru')
                )
                raw_avito = _clean_optional(
                    row.get('listing_url_avito') or row.get('source_avito')
                )
                incoming_auto_ru = (
                    raw_auto_ru
                    if is_marketplace_listing_url(EngineType.AUTO_RU, raw_auto_ru)
                    else None
                )
                incoming_avito = (
                    raw_avito
                    if is_marketplace_listing_url(EngineType.AVITO, raw_avito)
                    else None
                )
                if raw_auto_ru and incoming_auto_ru is None:
                    snapshot.notes = (snapshot.notes or '') + ' Rejected invalid Auto.ru URL.'
                if raw_avito and incoming_avito is None:
                    snapshot.notes = (snapshot.notes or '') + ' Rejected invalid Avito URL.'
                if listing.source_auto_ru and not is_marketplace_listing_url(
                    EngineType.AUTO_RU, listing.source_auto_ru
                ):
                    listing.source_auto_ru = None
                if listing.source_avito and not is_marketplace_listing_url(
                    EngineType.AVITO, listing.source_avito
                ):
                    listing.source_avito = None
                # An empty contractor cell is ambiguous and must not erase a link
                # that a manager already confirmed. A non-empty source value remains
                # authoritative and updates the current registry.
                if incoming_auto_ru or not listing.source_auto_ru:
                    listing.source_auto_ru = incoming_auto_ru
                if incoming_avito or not listing.source_avito:
                    listing.source_avito = incoming_avito
                listing.dealer_auto_ru = _clean_optional(row.get('dealer_url_auto_ru'))
                listing.dealer_avito = _clean_optional(row.get('dealer_url_avito'))
                listing.direct_url = _clean_optional(row.get('listing_url') or row.get('direct_url'))
                price = row.get('price_hint')
                if price not in (None, ''):
                    cleaned_price = re.sub(r'[^0-9.,]', '', str(price)).replace(',', '.')
                    listing.price_hint = float(cleaned_price) if cleaned_price else None
                else:
                    listing.price_hint = None
                listing.notes = _clean_optional(row.get('notes'))
                listing.is_active = _is_active_status(row.get('source_status'))

                for filt in filters:
                    existing_filter = (
                        self.db.query(SearchFilter)
                        .filter(
                            SearchFilter.source == filt.source,
                            SearchFilter.external_key == filt.external_key,
                            SearchFilter.name == filt.name,
                        )
                        .one_or_none()
                    )
                    if existing_filter is None:
                        existing_filter = SearchFilter(
                            source=filt.source,
                            external_key=filt.external_key,
                            name=filt.name,
                            raw_url=filt.url,
                            raw_criteria=filt.criteria,
                            active=True,
                        )
                        self.db.add(existing_filter)
                        self.db.flush()
                    link = (
                        self.db.query(VehicleFilterExpectation)
                        .filter(
                            VehicleFilterExpectation.filter_id == existing_filter.id,
                            VehicleFilterExpectation.listing_id == listing.id,
                        )
                        .one_or_none()
                    )
                    if link is None:
                        self.db.add(
                            VehicleFilterExpectation(
                                filter_id=existing_filter.id,
                                listing_id=listing.id,
                                expected_position=filt.expected_position,
                                source_label=row.get('source_label'),
                                source_hint=row.get('source_hint'),
                            )
                        )

            # Older importer versions could mistake a1auto.ru vehicle pages for
            # auto.ru search filters. Preserve their history but remove them from runs.
            for existing_filter in self.db.query(SearchFilter).filter(SearchFilter.active.is_(True)):
                if not is_marketplace_search_url(existing_filter.source, existing_filter.raw_url):
                    existing_filter.active = False

            if present_signatures:
                self.db.query(Listing).filter(
                    Listing.is_active.is_(True),
                    Listing.vehicle_signature.not_in(present_signatures),
                ).update({Listing.is_active: False}, synchronize_session=False)

            snapshot.finished_at = _utcnow()
            snapshot.valid_rows = valid
            snapshot.invalid_rows = missing
            snapshot.blocked_by_schema_drift = drifted
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return {
            'rows_total': len(rows),
            'rows_valid': valid,
            'rows_invalid': missing,
        }
