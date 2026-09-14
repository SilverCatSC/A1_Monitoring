import pytest


@pytest.fixture
def db_modules(tmp_path):
    from types import SimpleNamespace

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base

    engine = create_engine(f'sqlite:///{tmp_path / "importer.db"}')
    Base.metadata.create_all(engine)
    yield SimpleNamespace(SessionLocal=sessionmaker(bind=engine)), None
    engine.dispose()


def _make_rows(*rows):
    from app.contracts import SourceRecord

    return [SourceRecord(row_number=idx + 1, source=row, raw=row.copy()) for idx, row in enumerate(rows)]


def test_import_service_accepts_optional_unknown_columns(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter
    from app.models import SourceImportSnapshot

    session = app_db.SessionLocal()
    try:
        rows = _make_rows(
            {
                'brand': 'Lada',
                'model': 'Granta',
                'vin': 'VIN1234567890ABC',
                'status': 'active',
                'source_status': 'ok',
                'search_url_avito': 'https://www.avito.ru/brands/lada',
            }
        )

        summary = SourceImporter(session).run(rows, source_signature='test1')
        assert summary == {'rows_total': 1, 'rows_valid': 1, 'rows_invalid': 0}

        snapshot = (
            session.query(SourceImportSnapshot).order_by(SourceImportSnapshot.started_at.desc()).first()
        )
        assert snapshot.blocked_by_schema_drift is False
    finally:
        session.close()


def test_import_snapshot_is_correlated_to_a_full_cycle(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter
    from app.models import SourceImportSnapshot

    session = app_db.SessionLocal()
    try:
        rows = _make_rows(
            {
                'brand': 'Lada',
                'model': 'Granta',
                'vin': 'VIN1234567890ABC',
                'source_status': 'ok',
            }
        )
        summary = SourceImporter(session).run(
            rows, source_signature='test-cycle', cycle_id='cycle-1'
        )

        assert summary['snapshot_id']
        snapshot = session.get(SourceImportSnapshot, summary['snapshot_id'])
        assert snapshot.cycle_id == 'cycle-1'
    finally:
        session.close()


def test_empty_import_is_quarantined_without_deactivating_last_good_registry(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter, SourceImportError
    from app.models import Listing, SourceImportSnapshot

    session = app_db.SessionLocal()
    try:
        session.add(Listing(id='existing', vehicle_signature='existing', is_active=True))
        session.commit()

        with pytest.raises(SourceImportError):
            SourceImporter(session).run([], source_signature='empty', cycle_id='cycle-empty')

        snapshot = session.query(SourceImportSnapshot).one()
        assert snapshot.cycle_id == 'cycle-empty'
        assert snapshot.blocked_by_schema_drift is True
        assert snapshot.valid_rows == 0
        assert session.get(Listing, 'existing').is_active is True
    finally:
        session.close()


def test_importer_splits_current_combined_brand_model_header(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter
    from app.models import Listing

    session = app_db.SessionLocal()
    try:
        rows = _make_rows(
            {
                'brand_model': 'Mercedes-Benz V-Class',
                'vin': 'W1VVNLTZ5S4556796',
                'source_status': 'Актуально',
            }
        )
        SourceImporter(session).run(rows, source_signature='combined-name')

        listing = session.query(Listing).one()
        assert listing.brand == 'Mercedes-Benz'
        assert listing.model == 'V-Class'
    finally:
        session.close()


def test_import_service_marks_anchor_missing_as_drift(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv('DATABASE_DSN', f'sqlite:///{tmp_path / "importer2.db"}')
    monkeypatch.setenv('EVIDENCE_DIR', str(tmp_path / 'artifacts2'))
    import app.config
    import app.db

    app.config = importlib.reload(app.config)
    app.db = importlib.reload(app.db)
    from app.models import ImportFieldDrift, SourceImportSnapshot

    app.db.init_db()

    from app.importer.service import SourceImporter, SourceImportError

    rows = _make_rows(
        {
            'some_unknown': 'value',
            'another': 'value2',
        }
    )
    session = app.db.SessionLocal()
    try:
        with pytest.raises(SourceImportError):
            SourceImporter(session).run(rows, source_signature='test2')

        snapshot = (
            session.query(SourceImportSnapshot).order_by(SourceImportSnapshot.started_at.desc()).first()
        )
        assert snapshot.blocked_by_schema_drift is True
        drift = session.query(ImportFieldDrift).filter(ImportFieldDrift.snapshot_id == snapshot.id).first()
        assert drift is not None
        assert len(drift.raw_headers or []) > 0
    finally:
        session.close()


def test_importer_does_not_change_state_on_schema_drift(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter, SourceImportError
    from app.models import Listing

    session = app_db.SessionLocal()
    try:
        session.add(Listing(vehicle_signature='existing', brand='Audi', model='A4', is_active=True))
        session.commit()

        rows = _make_rows(
            {
                'some_unknown': 'x',
                'another': 'y',
            }
        )

        with pytest.raises(SourceImportError):
            SourceImporter(session).run(rows, source_signature='drift-test')

        assert session.query(Listing).count() == 1
    finally:
        session.close()


def test_importer_detects_filter_urls_from_unknown_columns(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter
    from app.models import SearchFilter, VehicleFilterExpectation

    session = app_db.SessionLocal()
    try:
        rows = _make_rows(
            {
                'brand': 'Nissan',
                'model': 'Qashqai',
                'generation': 'II',
                'year': 2021,
                'col_j': 'https://auto.ru/diler/cars/all/a1_avto_moskva/',
                'col_k': 'https://auto.ru/cars/all/a1_avto_moskva/?dealer=1',
                'col_l': 'https://www.avito.ru/brands/a1auto/items/all/avtomobili?s=profile_search_show_all&sellerId=e4036231f69cc5cfc365db2f79600230',
            }
        )

        SourceImporter(session).run(rows, source_signature='unknown-cols')

        filters = session.query(SearchFilter).order_by(SearchFilter.source, SearchFilter.name).all()
        assert len(filters) >= 2
        assert any(item.source.value == 'auto_ru' for item in filters)
        assert any(item.source.value == 'avito' for item in filters)
        assert session.query(VehicleFilterExpectation).count() == 2
    finally:
        session.close()


def test_importer_updates_existing_listing_links(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter
    from app.models import Listing, ListingChangeEvent, ListingLinkEvent

    session = app_db.SessionLocal()
    try:
        first = _make_rows(
            {
                'brand': 'Mercedes-Benz V-Class',
                'vin': 'W1VVNLTZ5S4556796',
                'listing_url_auto_ru': 'https://auto.ru/cars/used/sale/brand/model/1234567890-old/',
                'listing_url_avito': 'https://www.avito.ru/moskva/avtomobili/old_1234567890',
                'source_status': 'Актуально',
            }
        )
        second = _make_rows(
            {
                'brand': 'Mercedes-Benz V-Class',
                'vin': 'W1VVNLTZ5S4556796',
                'listing_url_auto_ru': 'https://auto.ru/cars/used/sale/brand/model/2234567890-new/',
                'listing_url_avito': 'https://www.avito.ru/moskva/avtomobili/new_2234567890',
                'source_status': 'Актуально',
            }
        )

        SourceImporter(session).run(first, source_signature='v1')
        SourceImporter(session).run(second, source_signature='v2')

        listing = session.query(Listing).one()
        assert listing.source_auto_ru.endswith('/2234567890-new/')
        assert listing.source_avito.endswith('/new_2234567890')
        assert listing.is_active is True
        assert session.query(ListingChangeEvent).count() == 2
        assert session.query(ListingLinkEvent).count() == 4
        SourceImporter(session).run(second, source_signature='v2-repeat')
        assert session.query(ListingChangeEvent).count() == 2
        assert session.query(ListingLinkEvent).count() == 4
    finally:
        session.close()


def test_no_vin_rows_with_same_model_but_different_offers_never_merge(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter
    from app.models import Listing, Offer, OfferVehicleLink, SourceRecord, Vehicle

    session = app_db.SessionLocal()
    try:
        rows = _make_rows(
            {
                'brand': 'Mercedes-Benz',
                'model': 'V-Class',
                'generation': 'W447',
                'year': 2024,
                'listing_url_auto_ru': 'https://auto.ru/cars/used/sale/mercedes/v/1132311022-a/',
                'listing_url_avito': 'https://www.avito.ru/moskva/avtomobili/v_1132311022',
                'source_status': 'Актуально',
            },
            {
                'brand': 'Mercedes-Benz',
                'model': 'V-Class',
                'generation': 'W447',
                'year': 2024,
                'listing_url_auto_ru': 'https://auto.ru/cars/used/sale/mercedes/v/1133334954-b/',
                'listing_url_avito': 'https://www.avito.ru/moskva/avtomobili/v_1133334954',
                'source_status': 'Актуально',
            },
        )

        SourceImporter(session).run(rows, source_signature='two-no-vin-offers')
        SourceImporter(session).run(rows, source_signature='two-no-vin-offers-repeat')

        assert session.query(Listing).count() == 2
        assert session.query(Vehicle).count() == 2
        assert session.query(Offer).count() == 4
        assert session.query(SourceRecord).count() == 4
        assert session.query(OfferVehicleLink).filter_by(state='candidate').count() == 4
        assert {item.vehicle_id for item in session.query(Listing).all()} == {
            item.id for item in session.query(Vehicle).all()
        }
    finally:
        session.close()


def test_no_vin_republication_requires_manual_identity_confirmation(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter
    from app.models import EngineType, Listing, Offer, OfferVehicleLink, Vehicle
    from app.service.listings import ListingRegistryService

    old = 'https://auto.ru/cars/used/sale/mercedes/v/1132311022-old/'
    new = 'https://auto.ru/cars/used/sale/mercedes/v/1133334954-new/'
    session = app_db.SessionLocal()
    try:
        base = {
            'brand': 'Mercedes-Benz',
            'model': 'V-Class',
            'generation': 'W447',
            'year': 2024,
            'source_status': 'Актуально',
        }
        SourceImporter(session).run(_make_rows({**base, 'listing_url_auto_ru': old}))
        original = session.query(Listing).one()
        SourceImporter(session).run(_make_rows({**base, 'listing_url_auto_ru': new}))

        assert session.query(Vehicle).count() == 2
        assert original.is_active is False
        new_offer = session.query(Offer).filter_by(external_key='auto_ru:1133334954').one()
        assert {link.vehicle_id for link in new_offer.vehicle_links if link.state == 'candidate'} != {
            original.vehicle_id
        }

        ListingRegistryService(session).update_link(
            original.id,
            source=EngineType.AUTO_RU,
            url=new,
            actor='Оператор',
            reason='Подтверждена перевыкладка без VIN',
        )

        links = session.query(OfferVehicleLink).filter_by(offer_id=new_offer.id).all()
        assert any(
            link.vehicle_id == original.vehicle_id
            and link.state == 'confirmed'
            and link.method == 'operator_confirmed'
            for link in links
        )
        assert any(
            link.vehicle_id != original.vehicle_id and link.state == 'rejected'
            for link in links
        )
        old_offer = session.query(Offer).filter_by(external_key='auto_ru:1132311022').one()
        assert any(link.state == 'superseded' for link in old_offer.vehicle_links)
    finally:
        session.close()


def test_importer_does_not_erase_confirmed_links_with_blank_source_cells(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter
    from app.models import Listing

    session = app_db.SessionLocal()
    try:
        initial = _make_rows(
            {
                'brand': 'Mercedes-Benz V-Class',
                'vin': 'W1VVNLTZ5S4556796',
                'listing_url_auto_ru': 'https://auto.ru/cars/used/sale/mercedes/v/1234567890-a/',
                'listing_url_avito': 'https://www.avito.ru/moskva/avtomobili/v_9876543210',
                'source_status': 'Актуально',
            }
        )
        blank_next = _make_rows(
            {
                'brand': 'Mercedes-Benz V-Class',
                'vin': 'W1VVNLTZ5S4556796',
                'listing_url_auto_ru': '',
                'listing_url_avito': '',
                'source_status': 'Актуально',
            }
        )

        SourceImporter(session).run(initial, source_signature='with-links')
        SourceImporter(session).run(blank_next, source_signature='blank-links')

        listing = session.query(Listing).one()
        assert listing.source_auto_ru.endswith('/1234567890-a/')
        assert listing.source_avito.endswith('/v_9876543210')
    finally:
        session.close()


def test_confirmed_republication_survives_old_empty_and_conflicting_source_urls(db_modules):
    from app.importer.service import SourceImporter
    from app.models import EngineType, Listing, ListingLinkOverride
    from app.service.listings import ListingRegistryService

    app_db, _ = db_modules
    old = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1132311022-old/'
    new = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1133334954-new/'
    other = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1134444954-other/'
    def rows(url):
        return _make_rows({'brand': 'Mercedes-Benz', 'model': 'V-Class', 'vin': 'W1VVNLTZ5S4556796',
                           'source_status': 'Актуально', 'listing_url_auto_ru': url})
    with app_db.SessionLocal() as session:
        SourceImporter(session).run(rows(old))
        car = session.query(Listing).one()
        ListingRegistryService(session).update_link(car.id, source=EngineType.AUTO_RU, url=new,
                                                   actor='Оператор', reason='Подтверждена перевыкладка')
        session.commit()
        for stale in (old, '', new, old, other):
            SourceImporter(session).run(rows(stale))
            assert session.query(Listing).one().source_auto_ru == new
            assert session.query(ListingLinkOverride).one().last_source_url == (stale or None)
        assert session.query(Listing).count() == 1


def test_importer_rejects_non_marketplace_listing_urls(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter
    from app.models import Listing, SourceImportSnapshot

    session = app_db.SessionLocal()
    try:
        rows = _make_rows(
            {
                'brand': 'Unsafe URL',
                'vin': 'W1VVNLTZ5S4556796',
                'listing_url_auto_ru': 'javascript:alert(1)',
                'listing_url_avito': 'https://example.com/fake_1234567890',
            }
        )

        SourceImporter(session).run(rows, source_signature='invalid-listing-links')

        listing = session.query(Listing).one()
        assert listing.source_auto_ru is None
        assert listing.source_avito is None
        assert 'Rejected invalid Auto.ru URL' in session.query(SourceImportSnapshot).one().notes
    finally:
        session.close()


def test_importer_rejects_multi_vin_row_but_accepts_valid_rows(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter
    from app.models import Listing, SourceImportSnapshot

    session = app_db.SessionLocal()
    try:
        rows = _make_rows(
            {'brand': 'A', 'vin': 'W1VVNLTZ5S4556796', 'source_status': 'Актуально'},
            {
                'brand': 'B',
                'vin': 'X89183511M1GB1114\nW1V44781313871282',
                'source_status': 'Актуально',
            },
        )

        summary = SourceImporter(session).run(rows, source_signature='mixed')

        assert summary == {'rows_total': 2, 'rows_valid': 1, 'rows_invalid': 1}
        assert session.query(Listing).count() == 1
        snapshot = session.query(SourceImportSnapshot).one()
        assert snapshot.blocked_by_schema_drift is False
        assert 'multiple_vin' in (snapshot.notes or '')
    finally:
        session.close()


def test_importer_quarantines_large_valid_row_drop(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter, SourceImportError
    from app.models import Listing, SourceImportSnapshot

    session = app_db.SessionLocal()
    try:
        baseline = _make_rows(
            *[
                {
                    'brand': f'Car {index}',
                    'vin': f'VALIDVIN{index:09d}',
                    'source_status': 'Актуально',
                }
                for index in range(10)
            ]
        )
        SourceImporter(session).run(baseline, source_signature='baseline')

        with pytest.raises(SourceImportError):
            SourceImporter(session).run(baseline[:2], source_signature='truncated')

        assert session.query(Listing).filter(Listing.is_active.is_(True)).count() == 10
        latest = session.query(SourceImportSnapshot).order_by(SourceImportSnapshot.started_at.desc()).first()
        assert latest.blocked_by_schema_drift is True
        assert 'last-good registry preserved' in (latest.notes or '')
    finally:
        session.close()


def test_importer_deactivates_vehicle_absent_from_healthy_snapshot(db_modules):
    app_db, _ = db_modules
    from app.importer.service import SourceImporter
    from app.models import Listing

    session = app_db.SessionLocal()
    try:
        first = _make_rows(
            {'brand': 'A', 'vin': 'W1VVNLTZ5S4556796', 'source_status': 'Актуально'},
            {'brand': 'B', 'vin': 'X89183511M1GB1114', 'source_status': 'Актуально'},
            {'brand': 'C', 'vin': 'W1V44781313871282', 'source_status': 'Актуально'},
        )
        second = _make_rows(
            {'brand': 'A', 'vin': 'W1VVNLTZ5S4556796', 'source_status': 'Актуально'},
            {'brand': 'B', 'vin': 'X89183511M1GB1114', 'source_status': 'Актуально'},
        )
        SourceImporter(session).run(first, source_signature='full')
        SourceImporter(session).run(second, source_signature='healthy-next')

        removed = session.query(Listing).filter(Listing.vin == 'W1V44781313871282').one()
        assert removed.is_active is False
    finally:
        session.close()
