from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import EngineType, Listing, SearchFilter, VehicleFilterExpectation
from app.scraper.base import is_marketplace_search_url


class FilterValidationError(ValueError):
    pass


CATALOG_VERSION = 'a1-monitoring-instruction-2026-09-04-v1'


@dataclass(frozen=True)
class CanonicalFilterDefinition:
    key: str
    source: EngineType
    name: str
    url: str
    family: str
    condition: str


# Business-owned search-result URLs from "Инструкция по мониторингу машин.md".
# The two Avito Sprinter URLs in that document describe the same semantic search;
# only one is retained so one marketplace page is not counted twice.
CANONICAL_FILTERS: tuple[CanonicalFilterDefinition, ...] = (
    CanonicalFilterDefinition(
        'auto_v_class_new',
        EngineType.AUTO_RU,
        'Mercedes-Benz V-Class — новые',
        'https://auto.ru/moskva/cars/mercedes/v_klasse/new/',
        'v_class',
        'new',
    ),
    CanonicalFilterDefinition(
        'auto_v_class_used',
        EngineType.AUTO_RU,
        'Mercedes-Benz V-Class — с пробегом',
        'https://auto.ru/moskva/cars/mercedes/v_klasse/used/',
        'v_class',
        'used',
    ),
    CanonicalFilterDefinition(
        'auto_vle_new',
        EngineType.AUTO_RU,
        'Mercedes-Benz VLE — новые',
        'https://auto.ru/moskva/cars/mercedes/vle/new/',
        'vle',
        'new',
    ),
    CanonicalFilterDefinition(
        'auto_maybach_s_new',
        EngineType.AUTO_RU,
        'Mercedes-Maybach S-Класс — новые',
        'https://auto.ru/moskva/cars/new/group/mercedes/s_class_maybach/25017310-25017315/',
        'maybach_s',
        'new',
    ),
    CanonicalFilterDefinition(
        'auto_zeekr_9x_new',
        EngineType.AUTO_RU,
        'Zeekr 9X — новые',
        'https://auto.ru/moskva/cars/zeekr/9x/new/',
        'zeekr_9x',
        'new',
    ),
    CanonicalFilterDefinition(
        'auto_maextro_s800_new',
        EngineType.AUTO_RU,
        'Maextro S800 — новые',
        'https://auto.ru/moskva/cars/maextro/s800/new/',
        'maextro_s800',
        'new',
    ),
    CanonicalFilterDefinition(
        'auto_maextro_v_all',
        EngineType.AUTO_RU,
        'Maextro V — все',
        'https://auto.ru/moskva/cars/maextro/v/all/',
        'maextro_v',
        'all',
    ),
    CanonicalFilterDefinition(
        'auto_range_rover_new',
        EngineType.AUTO_RU,
        'Land Rover Range Rover — новые',
        'https://auto.ru/moskva/cars/new/group/land_rover/range_rover/23093220-23093279/?catalog_filter=mark%3DLAND_ROVER%2Cmodel%3DRANGE_ROVER%2Cgeneration%3D23093220%2Cconfiguration%3D23093279%2Ctech_param%3D23789468',
        'range_rover',
        'new',
    ),
    CanonicalFilterDefinition(
        'auto_hongqi_hq9_new',
        EngineType.AUTO_RU,
        'Hongqi HQ9 — новые',
        'https://auto.ru/moskva/cars/hongqi/hq9/new/',
        'hongqi_hq9',
        'new',
    ),
    CanonicalFilterDefinition(
        'auto_sprinter_all',
        EngineType.AUTO_RU,
        'Mercedes-Benz Sprinter — все',
        'https://auto.ru/moskva/lcv/mercedes/sprinter/all/?sort=fresh_relevance_1-desc&utm_referrer=https%3A%2F%2Fauto.ru%2Fmoskva%2Flcv%2Fmercedes%2Fsprinter%2Fall%2F%3Fsort%3Dfresh_relevance_1-desc',
        'sprinter',
        'all',
    ),
    CanonicalFilterDefinition(
        'avito_sprinter_all',
        EngineType.AVITO,
        'Mercedes-Benz Sprinter — все',
        'https://www.avito.ru/moskva/avtomobili/mercedes-benz/sprinter-ASgBAgICAkTgtg3omCjitg38sCg?cd=1&context=H4sIAAAAAAAA_wEmANn_YToxOntzOjE6InkiO3M6MTY6IkxldFUxbDlDT2dydHZ2RzUiO31XyraKJgAAAA&localPriority=0&radius=0&searchRadius=0',
        'sprinter',
        'all',
    ),
    CanonicalFilterDefinition(
        'avito_maybach_s_new',
        EngineType.AVITO,
        'Mercedes-Maybach S-Класс — новые',
        'https://www.avito.ru/moskva/avtomobili/novyy/mercedes-benz/maybach_s-klass-ASgBAgICA0SGFMbmAeC2DeiYKOK2DcqqKA?context=H4sIAAAAAAAA_wEmANn_YToxOntzOjE6InkiO3M6MTY6IjBIR3pQR3BNSHhOQkJ2SEoiO316dUh8JgAAAA&localPriority=0&radius=0&searchRadius=0',
        'maybach_s',
        'new',
    ),
    CanonicalFilterDefinition(
        'avito_zeekr_9x_new',
        EngineType.AVITO,
        'Zeekr 9X — новые',
        'https://www.avito.ru/all/avtomobili/novyy/zeekr/9x-ASgBAgICA0SGFMbmAeC2DYaJwxDitg328K8V',
        'zeekr_9x',
        'new',
    ),
    CanonicalFilterDefinition(
        'avito_maextro_s800_new',
        EngineType.AVITO,
        'Maextro S800 — новые',
        'https://www.avito.ru/all/avtomobili/novyy/maextro/s800-ASgBAgICA0SGFMbmAeC2DZ6hrxXitg2goa8V',
        'maextro_s800',
        'new',
    ),
    CanonicalFilterDefinition(
        'avito_range_rover_new',
        EngineType.AVITO,
        'Land Rover Range Rover — новые',
        'https://www.avito.ru/all/avtomobili/novyy/land_rover/range_rover-ASgBAgICA0SGFMbmAeC2DdCYKOK2DYyuKA',
        'range_rover',
        'new',
    ),
    CanonicalFilterDefinition(
        'avito_hongqi_hq9_new',
        EngineType.AVITO,
        'Hongqi HQ9 — новые',
        'https://www.avito.ru/moskva/avtomobili/novyy/hongqi/hq9-ASgBAgICA0SGFMbmAeC2Dabw4wLitg3o1LsR?context=H4sIAAAAAAAA_wEmANn_YToxOntzOjE6InkiO3M6MTY6ImxpZzd3Y1Z0amZmdHVsdlYiO32gzRRyJgAAAA&localPriority=0&radius=0&searchRadius=0',
        'hongqi_hq9',
        'new',
    ),
)


def _target_listing_url(listing: Listing, source: EngineType) -> str | None:
    return listing.source_auto_ru if source == EngineType.AUTO_RU else listing.source_avito


def _listing_family(listing: Listing, source: EngineType) -> str | None:
    target = str(_target_listing_url(listing, source) or '').lower()
    companion = str(
        (listing.source_avito if source == EngineType.AUTO_RU else listing.source_auto_ru)
        or ''
    ).lower()
    if source == EngineType.AUTO_RU:
        patterns = (
            ('/mercedes/v_klasse/', 'v_class'),
            ('/mercedes/vle/', 'vle'),
            ('/mercedes/s_class_maybach/', 'maybach_s'),
            ('/zeekr/9x/', 'zeekr_9x'),
            ('/maextro/s800/', 'maextro_s800'),
            ('/maextro/v/', 'maextro_v'),
            ('/land_rover/range_rover/', 'range_rover'),
            ('/hongqi/hq9/', 'hongqi_hq9'),
            ('/mercedes/sprinter/', 'sprinter'),
        )
        for marker, family in patterns:
            if marker in target:
                return family
        if '/mercedes/s_klasse/' in target and 'mercedes-benz_maybach_s-klass' in companion:
            return 'maybach_s'
        return None

    patterns = (
        ('mercedes-benz_v-klass', 'v_class'),
        ('mercedes-benz_maybach_s-klass', 'maybach_s'),
        ('zeekr_9x', 'zeekr_9x'),
        ('maextro_s800', 'maextro_s800'),
        ('land_rover_range_rover', 'range_rover'),
        ('hongqi_hq9', 'hongqi_hq9'),
        ('mercedes-benz_sprinter', 'sprinter'),
    )
    for marker, family in patterns:
        if marker in target:
            return family
    return None


def _listing_condition(listing: Listing, source: EngineType) -> str | None:
    target = str(_target_listing_url(listing, source) or '').lower()
    if source == EngineType.AUTO_RU:
        if '/new/' in target:
            return 'new'
        if '/used/' in target:
            return 'used'
        return None

    # Avito listing URLs do not consistently state the condition. Mileage embedded
    # in the slug is positive evidence of a used listing; otherwise a valid paired
    # Auto.ru URL supplies the condition. Ambiguous rows stay unassigned.
    if re.search(r'_\d[\d_]*_km_', target):
        return 'used'
    companion = str(listing.source_auto_ru or '').lower()
    if '/new/' in companion:
        return 'new'
    if '/used/' in companion:
        return 'used'
    return None


def _catalog_matches(
    listing: Listing, definition: CanonicalFilterDefinition
) -> tuple[bool, str | None]:
    target_url = _target_listing_url(listing, definition.source)
    if not target_url:
        return False, 'missing_direct_url'
    family = _listing_family(listing, definition.source)
    if family != definition.family:
        return False, 'family_mismatch'
    if definition.condition == 'all':
        return True, None
    condition = _listing_condition(listing, definition.source)
    if condition is None:
        return False, 'condition_unknown'
    if condition != definition.condition:
        return False, 'condition_mismatch'
    return True, None


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

    def canonical_catalog_status(self) -> dict:
        listings = (
            self.db.query(Listing)
            .filter(Listing.is_active.is_(True))
            .order_by(Listing.brand, Listing.model, Listing.vin)
            .all()
        )
        assignments: dict[str, list[Listing]] = {
            definition.key: [] for definition in CANONICAL_FILTERS
        }
        unmatched: list[dict[str, str | None]] = []
        ambiguous: list[dict[str, str | None]] = []

        for source in (EngineType.AUTO_RU, EngineType.AVITO):
            source_definitions = [
                definition for definition in CANONICAL_FILTERS if definition.source == source
            ]
            for listing in listings:
                target_url = _target_listing_url(listing, source)
                if not target_url:
                    continue
                matching = [
                    definition
                    for definition in source_definitions
                    if _catalog_matches(listing, definition)[0]
                ]
                if len(matching) == 1:
                    assignments[matching[0].key].append(listing)
                    continue
                family = _listing_family(listing, source)
                condition = _listing_condition(listing, source)
                payload = {
                    'listing_id': listing.id,
                    'vin': listing.vin,
                    'source': source.value,
                    'family': family,
                    'condition': condition,
                    'reason': 'no_canonical_filter' if family else 'unrecognized_listing_url',
                }
                if len(matching) > 1:
                    payload['reason'] = 'ambiguous_canonical_filters'
                    ambiguous.append(payload)
                else:
                    unmatched.append(payload)

        return {
            'catalog_version': CATALOG_VERSION,
            'definitions': len(CANONICAL_FILTERS),
            'definitions_by_source': {
                source.value: sum(
                    definition.source == source for definition in CANONICAL_FILTERS
                )
                for source in (EngineType.AUTO_RU, EngineType.AVITO)
            },
            'assignments': {
                key: [listing.id for listing in rows] for key, rows in assignments.items()
            },
            'expectations': sum(len(rows) for rows in assignments.values()),
            'unmatched': unmatched,
            'ambiguous': ambiguous,
        }

    def sync_canonical_catalog(self) -> dict:
        invalid_definitions = [
            definition.key
            for definition in CANONICAL_FILTERS
            if not is_marketplace_search_url(definition.source, definition.url)
        ]
        if invalid_definitions:
            raise FilterValidationError(
                f'invalid canonical search URLs: {", ".join(invalid_definitions)}'
            )
        status = self.canonical_catalog_status()
        created = 0
        updated = 0
        expectations = 0

        for definition in CANONICAL_FILTERS:
            assignment_ids = status['assignments'][definition.key]
            external_key = hashlib.sha256(
                f'canonical_catalog|{definition.key}'.encode('utf-8')
            ).hexdigest()
            entity = (
                self.db.query(SearchFilter)
                .filter(
                    SearchFilter.source == definition.source,
                    SearchFilter.external_key == external_key,
                )
                .one_or_none()
            )
            if entity is None:
                entity = SearchFilter(
                    source=definition.source,
                    external_key=external_key,
                    name=definition.name,
                    raw_url=normalize_search_url(definition.url),
                    active=bool(assignment_ids),
                    version=1,
                )
                self.db.add(entity)
                self.db.flush()
                created += 1
            else:
                operator_override = (entity.raw_criteria or {}).get(
                    'operator_active_override'
                )
                changed = entity.name != definition.name or entity.raw_url != normalize_search_url(
                    definition.url
                )
                entity.name = definition.name
                entity.raw_url = normalize_search_url(definition.url)
                if operator_override is None:
                    entity.active = bool(assignment_ids)
                if changed:
                    entity.version += 1
                    updated += 1

            criteria = {
                'managed_by': 'canonical_catalog',
                'assignment_mode': 'rule',
                'catalog_version': CATALOG_VERSION,
                'catalog_key': definition.key,
                'family': definition.family,
                'condition': definition.condition,
                'source_document': 'Инструкция по мониторингу машин.md',
            }
            if entity.raw_criteria and 'operator_active_override' in entity.raw_criteria:
                criteria['operator_active_override'] = entity.raw_criteria[
                    'operator_active_override'
                ]
            entity.raw_criteria = criteria
            self.db.query(VehicleFilterExpectation).filter(
                VehicleFilterExpectation.filter_id == entity.id
            ).delete(synchronize_session=False)
            for listing_id in assignment_ids:
                self.db.add(
                    VehicleFilterExpectation(
                        filter_id=entity.id,
                        listing_id=listing_id,
                        source_label=CATALOG_VERSION,
                        source_hint=definition.key,
                    )
                )
                expectations += 1

        self.db.commit()
        return {
            'catalog_version': CATALOG_VERSION,
            'definitions': len(CANONICAL_FILTERS),
            'created': created,
            'updated': updated,
            'expectations': expectations,
            'unmatched': len(status['unmatched']),
            'ambiguous': len(status['ambiguous']),
            'unmatched_rows': status['unmatched'],
            'ambiguous_rows': status['ambiguous'],
        }

    def set_active(self, filter_id: str, active: bool) -> SearchFilter:
        entity = self.db.query(SearchFilter).filter(SearchFilter.id == filter_id).one_or_none()
        if entity is None:
            raise FilterValidationError('filter not found')
        entity.active = active
        criteria = dict(entity.raw_criteria or {})
        if criteria.get('managed_by') == 'canonical_catalog':
            criteria['operator_active_override'] = active
            entity.raw_criteria = criteria
        self.db.commit()
        return entity
