from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import EngineType, Listing, SearchFilter, VehicleFilterExpectation
from app.scraper.base import is_marketplace_search_url


class FilterValidationError(ValueError):
    pass


def normalize_search_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ''))


class FilterRegistryService:
    def __init__(self, db: Session):
        self.db = db

    def upsert(
        self,
        *,
        source: EngineType,
        name: str,
        url: str,
        active: bool = True,
        vins: list[str] | None = None,
        apply_to_all_active: bool = False,
    ) -> dict:
        clean_name = name.strip()
        normalized_url = normalize_search_url(url)
        if not clean_name:
            raise FilterValidationError('filter name is required')
        if not is_marketplace_search_url(source, normalized_url):
            raise FilterValidationError(
                f'URL is not a supported {source.value} search-results page'
            )
        if apply_to_all_active and vins:
            raise FilterValidationError('use either vins or apply_to_all_active, not both')

        external_key = hashlib.sha256(
            f'{source.value}|{normalized_url}'.encode('utf-8')
        ).hexdigest()
        entity = (
            self.db.query(SearchFilter)
            .filter(
                SearchFilter.source == source,
                SearchFilter.external_key == external_key,
            )
            .one_or_none()
        )
        if entity is None:
            entity = SearchFilter(source=source, external_key=external_key, name=clean_name)
            self.db.add(entity)
            self.db.flush()
        entity.name = clean_name
        entity.raw_url = normalized_url
        entity.active = active

        query = self.db.query(Listing).filter(Listing.is_active.is_(True))
        requested_vins = sorted({str(vin).strip().upper() for vin in vins or [] if str(vin).strip()})
        missing_vins: list[str] = []
        if apply_to_all_active:
            entity.raw_criteria = {
                'managed_by': 'filter_registry',
                'assignment_mode': 'all_active',
            }
            if source == EngineType.AUTO_RU:
                query = query.filter(Listing.source_auto_ru.is_not(None))
            else:
                query = query.filter(Listing.source_avito.is_not(None))
            listings = query.all()
        elif requested_vins:
            entity.raw_criteria = {
                'managed_by': 'filter_registry',
                'assignment_mode': 'vins',
                'vins': requested_vins,
            }
            listings = query.filter(func.upper(Listing.vin).in_(requested_vins)).all()
            found_vins = {str(item.vin or '').upper() for item in listings}
            missing_vins = [vin for vin in requested_vins if vin not in found_vins]
        else:
            raise FilterValidationError('provide vins or set apply_to_all_active=true')

        self.db.query(VehicleFilterExpectation).filter(
            VehicleFilterExpectation.filter_id == entity.id
        ).delete(synchronize_session=False)
        for listing in listings:
            self.db.add(
                VehicleFilterExpectation(
                    filter_id=entity.id,
                    listing_id=listing.id,
                    source_label='filter_registry',
                    source_hint=normalized_url,
                )
            )
        self.db.commit()
        return {
            'id': entity.id,
            'source': source.value,
            'name': entity.name,
            'url': entity.raw_url,
            'active': entity.active,
            'expectations': len(listings),
            'missing_vins': missing_vins,
        }

    def refresh_managed_assignments(self) -> dict[str, int]:
        """Synchronize persistent filter intent with the current active stock."""
        summary = {'managed_filters': 0, 'added': 0, 'removed': 0, 'expectations': 0}
        entities = self.db.query(SearchFilter).all()
        for entity in entities:
            criteria = entity.raw_criteria or {}
            if criteria.get('managed_by') != 'filter_registry':
                continue
            mode = criteria.get('assignment_mode')
            query = self.db.query(Listing).filter(Listing.is_active.is_(True))
            if mode == 'all_active':
                if entity.source == EngineType.AUTO_RU:
                    query = query.filter(Listing.source_auto_ru.is_not(None))
                else:
                    query = query.filter(Listing.source_avito.is_not(None))
            elif mode == 'vins':
                requested_vins = [
                    str(vin).strip().upper()
                    for vin in criteria.get('vins', [])
                    if str(vin).strip()
                ]
                query = query.filter(func.upper(Listing.vin).in_(requested_vins))
            else:
                # Legacy registry rows did not retain assignment intent. Guessing here
                # could silently broaden a business filter, so they remain unchanged.
                continue

            target_ids = {listing.id for listing in query.all()}
            existing = {
                expectation.listing_id: expectation
                for expectation in self.db.query(VehicleFilterExpectation)
                .filter(VehicleFilterExpectation.filter_id == entity.id)
                .all()
            }
            stale_ids = set(existing) - target_ids
            if stale_ids:
                self.db.query(VehicleFilterExpectation).filter(
                    VehicleFilterExpectation.filter_id == entity.id,
                    VehicleFilterExpectation.listing_id.in_(stale_ids),
                ).delete(synchronize_session=False)
            new_ids = target_ids - set(existing)
            for listing_id in new_ids:
                self.db.add(
                    VehicleFilterExpectation(
                        filter_id=entity.id,
                        listing_id=listing_id,
                        source_label='filter_registry',
                        source_hint=entity.raw_url,
                    )
                )
            summary['managed_filters'] += 1
            summary['added'] += len(new_ids)
            summary['removed'] += len(stale_ids)
            summary['expectations'] += len(target_ids)

        self.db.commit()
        return summary

    def set_active(self, filter_id: str, active: bool) -> SearchFilter:
        entity = self.db.query(SearchFilter).filter(SearchFilter.id == filter_id).one_or_none()
        if entity is None:
            raise FilterValidationError('filter not found')
        entity.active = active
        self.db.commit()
        return entity
