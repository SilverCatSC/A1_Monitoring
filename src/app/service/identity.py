"""Conservative identity resolution for the registry import.

`Listing` is intentionally retained as the monitoring projection while this module
introduces separate application-owned vehicles and marketplace offers.  Matching a
VIN or an already-linked marketplace external ID is safe; matching a model, year or
price is not.  In particular, a row without either anchor always gets a new vehicle.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import (
    EngineType,
    Listing,
    Offer,
    OfferVehicleLink,
    SourceRecord,
    Vehicle,
)
from app.scraper.base import canonical_listing_key, is_marketplace_listing_url

ACTIVE_LINK_STATES = ('candidate', 'confirmed')


def _utcnow() -> datetime:
    return datetime.now(UTC)


def vin_fingerprint(value: Any) -> str | None:
    normalized = str(value or '').strip().upper()
    if not normalized or normalized == 'НЕ УКАЗАН':
        return None
    return hashlib.sha256(f'vin|{normalized}'.encode('utf-8')).hexdigest()


def row_fingerprint(row: dict[str, Any]) -> str:
    """Hash the accepted source row without persisting a second raw copy."""
    normalized = {
        str(key): str(value).strip()
        for key, value in row.items()
        if value is not None and str(value).strip()
    }
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def marketplace_urls(row: dict[str, Any]) -> dict[EngineType, str]:
    """Return only canonical marketplace listing URLs from an accepted source row."""
    candidates = (
        (EngineType.AUTO_RU, row.get('listing_url_auto_ru') or row.get('source_auto_ru')),
        (EngineType.AVITO, row.get('listing_url_avito') or row.get('source_avito')),
    )
    return {
        source: str(url).strip()
        for source, url in candidates
        if is_marketplace_listing_url(source, str(url or '').strip())
    }


class IdentityService:
    """Resolve import rows without implicit similarity-based vehicle merges."""

    def __init__(self, db: Session):
        self.db = db

    def bootstrap_legacy_identities(self) -> None:
        """Give pre-M2 projections an identity before resolving new import rows.

        The migration normally performs this work.  Keeping it here makes an
        application started from an older database and isolated test databases safe
        without needing to guess that two old Listing rows are the same car.
        """
        for listing in self.db.query(Listing).filter(Listing.vehicle_id.is_(None)).all():
            self.ensure_vehicle_for_listing(listing)

    def ensure_vehicle_for_listing(self, listing: Listing) -> Vehicle:
        if listing.vehicle_id:
            vehicle = self.db.get(Vehicle, listing.vehicle_id)
            if vehicle is not None:
                return vehicle

        vehicle = Vehicle(
            vin_fingerprint=vin_fingerprint(listing.vin),
            identity_state='legacy_backfill',
        )
        self.db.add(vehicle)
        self.db.flush()
        listing.vehicle_id = vehicle.id
        for source, url in self._listing_urls(listing).items():
            offer = self._get_or_create_offer(source, url)
            self._add_link_if_missing(
                offer,
                vehicle,
                state='candidate',
                method='legacy_backfill',
            )
        return vehicle

    def resolve(
        self,
        *,
        row: dict[str, Any],
    ) -> tuple[Vehicle, Listing, str]:
        """Return a safe Vehicle/Listing pair and an auditable resolution method."""
        self.bootstrap_legacy_identities()
        fingerprint = vin_fingerprint(row.get('vin'))
        row_urls = marketplace_urls(row)
        vehicle: Vehicle | None = None
        resolution = 'new_unanchored'

        if fingerprint:
            candidates = self.db.query(Vehicle).filter(
                Vehicle.vin_fingerprint == fingerprint
            ).order_by(Vehicle.id).all()
            if len(candidates) == 1:
                vehicle = candidates[0]
                resolution = 'vin_match'
            elif len(candidates) > 1:
                resolution = 'vin_ambiguous'
            else:
                resolution = 'vin_new'
        elif row_urls:
            candidates = self._vehicles_for_offer_urls(row_urls.items())
            if len(candidates) == 1:
                vehicle = candidates[0]
                resolution = 'offer_match'
            elif len(candidates) > 1:
                resolution = 'offer_ambiguous'

        if vehicle is None:
            vehicle = Vehicle(
                vin_fingerprint=fingerprint,
                identity_state=(
                    'vin_anchored'
                    if resolution == 'vin_new'
                    else 'provisional' if resolution == 'new_unanchored' else 'ambiguous'
                ),
            )
            self.db.add(vehicle)
            self.db.flush()

        listing = (
            self.db.query(Listing).filter(Listing.vehicle_id == vehicle.id).one_or_none()
        )
        if listing is None:
            listing = Listing(
                vehicle_id=vehicle.id,
                vehicle_signature=f'vehicle:{vehicle.id}',
            )
            self.db.add(listing)
            self.db.flush()
        return vehicle, listing, resolution

    def record_source_row(
        self,
        *,
        snapshot_id: str,
        row_number: int,
        row: dict[str, Any],
        vehicle: Vehicle,
        listing: Listing,
        resolution: str,
    ) -> SourceRecord:
        urls = marketplace_urls(row)
        record = SourceRecord(
            snapshot_id=snapshot_id,
            row_number=row_number,
            row_fingerprint=row_fingerprint(row),
            vin_fingerprint=vin_fingerprint(row.get('vin')),
            offer_keys=sorted(
                key
                for source, url in urls.items()
                if (key := canonical_listing_key(source, url))
            ),
            vehicle_id=vehicle.id,
            listing_id=listing.id,
            resolution=resolution,
        )
        self.db.add(record)
        self.db.flush()
        self.record_current_offers(
            vehicle=vehicle,
            source_record=record,
            urls=self._listing_urls(listing),
            resolution=resolution,
        )
        return record

    def confirm_republication(
        self,
        *,
        listing: Listing,
        source: EngineType,
        old_url: str | None,
        new_url: str,
        actor: str,
        reason: str,
    ) -> None:
        """Record a human decision that a new external ID belongs to this vehicle.

        This is deliberately the only code path that can reject an active candidate
        link held by another Vehicle.  It never merges or deletes that Vehicle or its
        monitoring history.
        """
        vehicle = self.ensure_vehicle_for_listing(listing)
        old_key = canonical_listing_key(source, old_url)
        new_key = canonical_listing_key(source, new_url)
        if new_key is None:
            return

        if old_key != new_key:
            for link in self._current_links_for_vehicle(vehicle.id, source):
                link.state = 'superseded'
                link.closed_at = _utcnow()
                link.actor = actor
                link.reason = reason

        offer = self._get_or_create_offer(source, new_url)
        confirmed_for_vehicle = False
        for link in self._current_links_for_offer(offer.id):
            if link.vehicle_id != vehicle.id:
                link.state = 'rejected'
                link.closed_at = _utcnow()
                link.actor = actor
                link.reason = reason
            elif link.state == 'candidate':
                link.state = 'superseded'
                link.closed_at = _utcnow()
                link.actor = actor
                link.reason = reason
            else:
                confirmed_for_vehicle = True

        if not confirmed_for_vehicle:
            self.db.add(
                OfferVehicleLink(
                    offer_id=offer.id,
                    vehicle_id=vehicle.id,
                    state='confirmed',
                    method='operator_confirmed',
                    actor=actor,
                    reason=reason,
                    details={'previous_external_key': old_key},
                )
            )
        self.db.flush()

    def record_current_offers(
        self,
        *,
        vehicle: Vehicle,
        source_record: SourceRecord,
        urls: dict[EngineType, str],
        resolution: str,
    ) -> None:
        for source, url in urls.items():
            offer = self._get_or_create_offer(source, url)
            requested_state = 'confirmed' if resolution in {'vin_match', 'vin_new'} else 'candidate'
            existing = [
                link
                for link in self._current_links_for_offer(offer.id)
                if link.vehicle_id == vehicle.id
            ]
            if existing:
                if requested_state == 'confirmed' and all(link.state != 'confirmed' for link in existing):
                    # Preserve the candidate assertion and add the stronger VIN-based
                    # assertion instead of rewriting history.
                    self.db.add(
                        OfferVehicleLink(
                            offer_id=offer.id,
                            vehicle_id=vehicle.id,
                            source_record_id=source_record.id,
                            state='confirmed',
                            method='vin_match',
                        )
                    )
                continue

            # A matching VIN is strong evidence for a new offer only when that offer
            # is not currently claimed by a different vehicle.  A conflict remains a
            # reviewable candidate; imports never transfer ownership.
            other_links = self._current_links_for_offer(offer.id)
            state = requested_state if not other_links else 'candidate'
            self.db.add(
                OfferVehicleLink(
                    offer_id=offer.id,
                    vehicle_id=vehicle.id,
                    source_record_id=source_record.id,
                    state=state,
                    method='vin_match' if resolution in {'vin_match', 'vin_new'} else 'source_import',
                )
            )
        self.db.flush()

    def _vehicles_for_offer_urls(
        self, values: Iterable[tuple[EngineType, str]]
    ) -> list[Vehicle]:
        vehicle_ids: set[str] = set()
        for source, url in values:
            key = canonical_listing_key(source, url)
            if key is None:
                continue
            offer = self.db.query(Offer).filter_by(source=source, external_key=key).one_or_none()
            if offer is None:
                continue
            vehicle_ids.update(link.vehicle_id for link in self._current_links_for_offer(offer.id))
        if len(vehicle_ids) != 1:
            return [] if not vehicle_ids else [self.db.get(Vehicle, item) for item in vehicle_ids]
        vehicle = self.db.get(Vehicle, next(iter(vehicle_ids)))
        return [vehicle] if vehicle is not None else []

    def _get_or_create_offer(self, source: EngineType, url: str) -> Offer:
        key = canonical_listing_key(source, url)
        if key is None:
            raise ValueError(f'unsupported {source.value} offer URL')
        offer = self.db.query(Offer).filter_by(source=source, external_key=key).one_or_none()
        if offer is None:
            offer = Offer(source=source, external_key=key, current_url=url, active=True)
            self.db.add(offer)
            self.db.flush()
        else:
            offer.current_url = url
            offer.active = True
            offer.last_seen_at = _utcnow()
        return offer

    def _add_link_if_missing(
        self,
        offer: Offer,
        vehicle: Vehicle,
        *,
        state: str,
        method: str,
    ) -> None:
        if any(link.vehicle_id == vehicle.id for link in self._current_links_for_offer(offer.id)):
            return
        self.db.add(
            OfferVehicleLink(
                offer_id=offer.id,
                vehicle_id=vehicle.id,
                state=state,
                method=method,
            )
        )
        self.db.flush()

    def _current_links_for_offer(self, offer_id: str) -> list[OfferVehicleLink]:
        return (
            self.db.query(OfferVehicleLink)
            .filter(
                OfferVehicleLink.offer_id == offer_id,
                OfferVehicleLink.state.in_(ACTIVE_LINK_STATES),
            )
            .order_by(OfferVehicleLink.created_at, OfferVehicleLink.id)
            .all()
        )

    def _current_links_for_vehicle(
        self, vehicle_id: str, source: EngineType
    ) -> list[OfferVehicleLink]:
        return (
            self.db.query(OfferVehicleLink)
            .join(Offer)
            .filter(
                OfferVehicleLink.vehicle_id == vehicle_id,
                Offer.source == source,
                OfferVehicleLink.state.in_(ACTIVE_LINK_STATES),
            )
            .all()
        )

    @staticmethod
    def _listing_urls(listing: Listing) -> dict[EngineType, str]:
        return {
            source: url
            for source, url in (
                (EngineType.AUTO_RU, listing.source_auto_ru),
                (EngineType.AVITO, listing.source_avito),
            )
            if is_marketplace_listing_url(source, str(url or '').strip())
        }
