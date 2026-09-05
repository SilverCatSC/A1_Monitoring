import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, EngineType, Listing, SearchFilter, VehicleFilterExpectation
from app.service.filters import (
    CANONICAL_FILTERS,
    FilterRegistryService,
    FilterValidationError,
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

    service = FilterRegistryService(session)
    service.sync_canonical_catalog()
    entity = (
        session.query(SearchFilter)
        .filter(SearchFilter.source == EngineType.AVITO, SearchFilter.name.contains('Sprinter'))
        .one()
    )
    entity.active = False
    session.commit()

    service.sync_canonical_catalog()

    assert entity.active is False
