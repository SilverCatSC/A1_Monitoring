from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, EngineType, Listing, SearchFilter, VehicleFilterExpectation
from app.service.filters import (
    CANONICAL_FILTERS,
    FilterRegistryService,
    FilterValidationError,
    _canonical_url_contract_matches,
    _moscow_only,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "filters.db"}')
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    yield db
    db.close()
    engine.dispose()


def _listing(vin, *, active=True, auto_url=None, avito_url=None):
    return Listing(
        vehicle_signature=vin,
        vin=vin,
        is_active=active,
        source_auto_ru=auto_url,
        source_avito=avito_url,
    )


def test_registry_upserts_filter_and_replaces_assignments(session):
    first = _listing(
        'W1VVNLTZ5S4556796',
        auto_url='https://auto.ru/cars/used/sale/mercedes/v_class/1234567890-a/',
    )
    second = _listing(
        'X89183511M1GB1114',
        auto_url='https://auto.ru/cars/used/sale/mercedes/v_class/2234567890-b/',
    )
    without_auto = _listing('W1V44781313871282')
    session.add_all([first, second, without_auto])
    session.commit()

    service = FilterRegistryService(session)
    created = service.upsert(
        source=EngineType.AUTO_RU,
        name='V-Class — все',
        url='https://auto.ru/moskva/cars/mercedes/v_class/used/?sort=price-asc&output_type=list',
        apply_to_all_active=True,
    )
    assert created['expectations'] == 2
    assert session.query(SearchFilter).count() == 1

    updated = service.upsert(
        source=EngineType.AUTO_RU,
        name='V-Class — приоритет',
        url='https://auto.ru/moskva/cars/mercedes/v_class/used/?output_type=list&sort=price-asc',
        vins=['W1VVNLTZ5S4556796', 'MISSINGVIN0000001'],
    )
    assert updated['id'] == created['id']
    assert updated['expectations'] == 1
    assert updated['missing_vins'] == ['MISSINGVIN0000001']
    assert session.query(SearchFilter).count() == 1
    assert session.query(VehicleFilterExpectation).count() == 1


def test_registry_rejects_non_marketplace_and_ambiguous_assignment(session):
    service = FilterRegistryService(session)
    with pytest.raises(FilterValidationError, match='not a supported'):
        service.upsert(
            source=EngineType.AUTO_RU,
            name='Wrong host',
            url='https://a1auto.ru/cars-for-sale/v-businessjet.html',
            apply_to_all_active=True,
        )
    with pytest.raises(FilterValidationError, match='either vins'):
        service.upsert(
            source=EngineType.AVITO,
            name='Ambiguous',
            url='https://www.avito.ru/moskva/avtomobili',
            vins=['W1VVNLTZ5S4556796'],
            apply_to_all_active=True,
        )


def test_all_active_assignments_refresh_after_stock_changes(session):
    first = _listing(
        'W1VVNLTZ5S4556796',
        auto_url='https://auto.ru/cars/used/sale/mercedes/v_class/1234567890-a/',
    )
    session.add(first)
    session.commit()
    service = FilterRegistryService(session)
    created = service.upsert(
        source=EngineType.AUTO_RU,
        name='Весь активный сток',
        url='https://auto.ru/moskva/cars/all/',
        apply_to_all_active=True,
    )

    first.is_active = False
    second = _listing(
        'X89183511M1GB1114',
        auto_url='https://auto.ru/cars/used/sale/mercedes/v_class/2234567890-b/',
    )
    session.add(second)
    session.commit()
    refreshed = service.refresh_managed_assignments()

    expectation = session.query(VehicleFilterExpectation).one()
    assert expectation.filter_id == created['id']
    assert expectation.listing_id == second.id
    assert refreshed == {
        'managed_filters': 1,
        'added': 1,
        'removed': 1,
        'expectations': 1,
    }


def test_legacy_managed_filter_without_assignment_mode_is_not_guessed(session):
    listing = _listing(
        'W1VVNLTZ5S4556796',
        auto_url='https://auto.ru/cars/used/sale/mercedes/v_class/1234567890-a/',
    )
    search_filter = SearchFilter(
        source=EngineType.AUTO_RU,
        external_key='legacy',
        name='Legacy',
        raw_url='https://auto.ru/moskva/cars/all/',
        raw_criteria={'managed_by': 'filter_registry'},
        active=True,
    )
    session.add_all([listing, search_filter])
    session.commit()

    refreshed = FilterRegistryService(session).refresh_managed_assignments()

    assert refreshed['managed_filters'] == 0
    assert session.query(VehicleFilterExpectation).count() == 0


def test_canonical_catalog_assigns_by_marketplace_family_and_condition(session):
    listings = [
        _listing(
            'NEWVCLASS00000001',
            auto_url='https://auto.ru/cars/new/group/mercedes/v_klasse/1/2/1234567890-a/',
        ),
        _listing(
            'USEDVCLASS0000001',
            auto_url='https://auto.ru/cars/used/sale/mercedes/v_klasse/2234567890-b/',
        ),
        _listing(
            'HONGQIHQ900000001',
            auto_url='https://auto.ru/cars/new/group/hongqi/hq9/1/2/3234567890-c/',
            avito_url='https://www.avito.ru/moskva/avtomobili/hongqi_hq9_2.0_at_2026_4234567890',
        ),
        _listing(
            'AVITOVCLASS000001',
            auto_url='https://auto.ru/cars/new/group/mercedes/v_klasse/1/2/5234567890-d/',
            avito_url='https://www.avito.ru/moskva/avtomobili/mercedes-benz_v-klass_2.0_at_2026_6234567890',
        ),
        _listing(
            'SPRINTER000000001',
            auto_url='https://auto.ru/lcv/used/sale/mercedes/sprinter/7234567890-e/',
            avito_url='https://www.avito.ru/moskva/avtomobili/mercedes-benz_sprinter_3.0_at_2024_9_540_km_8234567890',
        ),
    ]
    session.add_all(listings)
    session.commit()

    service = FilterRegistryService(session)
    first = service.sync_canonical_catalog()
    second = service.sync_canonical_catalog()

    assert first['definitions'] == 16
    assert first['created'] == 16
    assert first['expectations'] == 7
    assert first['unmatched'] == 1
    assert first['unmatched_rows'][0]['vin'] == 'AVITOVCLASS000001'
    assert first['unmatched_rows'][0]['source'] == 'avito'
    assert first['unmatched_rows'][0]['family'] == 'v_class'
    assert second['created'] == 0
    assert second['updated'] == 0
    assert session.query(SearchFilter).count() == 16
    assert session.query(VehicleFilterExpectation).count() == 7


def test_catalog_has_one_semantic_avito_sprinter_filter_and_preserves_operator_disable(
    session,
):
    avito_sprinter = [
        definition
        for definition in CANONICAL_FILTERS
        if definition.source == EngineType.AVITO and definition.family == 'sprinter'
    ]
    assert len(avito_sprinter) == 1

    session.add(
        _listing(
            'SPRINTERACTIVE001',
            auto_url='https://auto.ru/lcv/new/sale/mercedes/sprinter/9234567890-a/',
            avito_url='https://www.avito.ru/moskva/avtomobili/mercedes-benz_sprinter_3.0_at_2026_9234567891',
        )
    )
    session.commit()
    service = FilterRegistryService(session)
    service.sync_canonical_catalog()
    entity = (
        session.query(SearchFilter)
        .filter(SearchFilter.source == EngineType.AVITO, SearchFilter.name.contains('Sprinter'))
        .one()
    )
    assert entity.active is True
    service.set_active(entity.id, False)

    service.sync_canonical_catalog()

    assert entity.active is False
    assert entity.raw_criteria['operator_active_override'] is False


def test_new_v_class_filter_forces_list_view_and_moscow_only(session):
    definition = next(item for item in CANONICAL_FILTERS if item.key == 'auto_v_class_new')
    query = parse_qs(urlsplit(definition.url).query)

    assert query['output_type'] == ['list']
    assert query['geo_radius'] == ['0']
    assert query['rid'] == ['213']
    assert definition.output_type == 'list'
    assert definition.geo_radius_km == 0

    session.add(
        _listing(
            'NEWVCLASSFILTER01',
            auto_url='https://auto.ru/cars/new/group/mercedes/v_klasse/1/2/1234567890-a/',
        )
    )
    session.commit()
    FilterRegistryService(session).sync_canonical_catalog()

    entity = (
        session.query(SearchFilter)
        .filter(SearchFilter.source == EngineType.AUTO_RU, SearchFilter.name.contains('V-Class'))
        .filter(SearchFilter.name.contains('новые'))
        .one()
    )
    assert entity.raw_url.endswith('geo_radius=0&output_type=list&rid=213')
    assert entity.raw_criteria['output_type'] == 'list'
    assert entity.raw_criteria['geo_radius_km'] == 0


def test_all_auto_filters_use_moscow_only_and_keep_existing_criteria():
    for definition in CANONICAL_FILTERS:
        if definition.source != EngineType.AUTO_RU:
            continue
        query = parse_qs(urlsplit(definition.url).query)
        assert query['geo_radius'] == ['0'], definition.key
        assert query['rid'] == ['213'], definition.key
        assert definition.geo_radius_km == 0
        if definition.key == 'auto_range_rover_new':
            assert 'tech_param=23789468' in query['catalog_filter'][0]
        if definition.key == 'auto_sprinter_all':
            assert query['sort'] == ['fresh_relevance_1-desc']


def test_catalog_filter_without_current_expectations_is_inactive(session):
    service = FilterRegistryService(session)
    service.sync_canonical_catalog()

    assert session.query(SearchFilter).count() == 16
    assert session.query(SearchFilter).filter(SearchFilter.active.is_(True)).count() == 0


def test_all_avito_filters_use_moscow_only_and_keep_model_paths(session):
    definitions = [d for d in CANONICAL_FILTERS if d.source == EngineType.AVITO]
    assert len(definitions) == 6
    FilterRegistryService(session).sync_canonical_catalog()
    for definition in definitions:
        parts = urlsplit(definition.url)
        query = parse_qs(parts.query)
        assert parts.path.startswith('/moskva/avtomobili/'), definition.key
        assert query['radius'] == query['searchRadius'] == query['localPriority'] == ['0']
        assert definition.geo_radius_km == 0
        assert _canonical_url_contract_matches(definition)
        entity = session.query(SearchFilter).filter(
            SearchFilter.source == EngineType.AVITO,
            SearchFilter.name == definition.name,
        ).one()
        assert entity.raw_url == definition.url
        assert entity.raw_criteria['geo_radius_km'] == 0
        if definition.key == 'avito_zeekr_9x_new':
            assert parts.path.endswith('/novyy/zeekr/9x-ASgBAgICA0SGFMbmAeC2DYaJwxDitg328K8V')
        if definition.key == 'avito_sprinter_all':
            assert query['cd'] == ['1']
            assert 'context' in query


def test_avito_moscow_rule_replaces_conflicting_radius_and_preserves_other_parameters():
    definition = next(d for d in CANONICAL_FILTERS if d.key == 'avito_zeekr_9x_new')
    old_url = definition.url.replace('/moskva/', '/all/').split('?')[0]
    old_url += '?radius=200&radius=500&searchRadius=200&localPriority=1&s=104&context=kept'
    fixed = _moscow_only(replace(definition, url=old_url, geo_radius_km=None))
    query = parse_qs(urlsplit(fixed.url).query)
    assert query == {
        'radius': ['0'], 'searchRadius': ['0'], 'localPriority': ['0'],
        's': ['104'], 'context': ['kept'],
    }
    assert _canonical_url_contract_matches(fixed)
    assert _moscow_only(fixed) == fixed


@pytest.mark.parametrize('bad_change', [
    lambda url: url.replace('/moskva/', '/all/'),
    lambda url: url.replace('radius=0', 'radius=200'),
    lambda url: url.replace('searchRadius=0', 'searchRadius=200'),
    lambda url: url.replace('localPriority=0', 'localPriority=1'),
    lambda url: url + '&radius=200',
])
def test_avito_contract_rejects_national_expanded_or_ambiguous_geography(bad_change):
    definition = next(d for d in CANONICAL_FILTERS if d.key == 'avito_zeekr_9x_new')
    assert not _canonical_url_contract_matches(
        replace(definition, url=bad_change(definition.url))
    )


def test_avito_geography_sync_preserves_identity_assignments_and_operator_disable(session):
    session.add(_listing(
        'ZEEKRMOSCOW000001',
        auto_url='https://auto.ru/cars/new/group/zeekr/9x/1/2/1234567890-a/',
        avito_url='https://www.avito.ru/moskva/avtomobili/zeekr_9x_2026_1234567890',
    ))
    session.commit()
    service = FilterRegistryService(session)
    service.sync_canonical_catalog()
    entity = session.query(SearchFilter).filter(
        SearchFilter.source == EngineType.AVITO, SearchFilter.name == 'Zeekr 9X — новые',
    ).one()
    original_id, original_version = entity.id, entity.version
    assignments = session.query(VehicleFilterExpectation).filter_by(filter_id=entity.id)
    original_listing_id = assignments.one().listing_id
    entity.raw_url = entity.raw_url.replace('/moskva/', '/all/').split('?')[0]
    service.set_active(entity.id, False)

    result = service.sync_canonical_catalog()
    assert result['updated'] == 1
    assert result['created'] == 0
    assert result['expectations'] == 2
    assert entity.id == original_id
    assert entity.version == original_version + 1
    assert entity.active is False
    assert entity.raw_criteria['geo_radius_km'] == 0
    assert entity.raw_criteria['operator_active_override'] is False
    assert assignments.one().listing_id == original_listing_id
    assert service.sync_canonical_catalog()['updated'] == 0
