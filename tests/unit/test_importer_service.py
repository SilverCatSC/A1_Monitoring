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
    from app.models import Listing

    session = app_db.SessionLocal()
    try:
        first = _make_rows(
            {
                'brand': 'Mercedes-Benz V-Class',
                'vin': 'W1VVNLTZ5S4556796',
                'listing_url_auto_ru': 'https://auto.ru/old/123-old/',
                'listing_url_avito': 'https://www.avito.ru/old_456',
                'source_status': 'Актуально',
            }
        )
        second = _make_rows(
            {
                'brand': 'Mercedes-Benz V-Class',
                'vin': 'W1VVNLTZ5S4556796',
                'listing_url_auto_ru': 'https://auto.ru/new/789-new/',
                'listing_url_avito': 'https://www.avito.ru/new_999',
                'source_status': 'Актуально',
            }
        )

        SourceImporter(session).run(first, source_signature='v1')
        SourceImporter(session).run(second, source_signature='v2')

        listing = session.query(Listing).one()
        assert listing.source_auto_ru == 'https://auto.ru/new/789-new/'
        assert listing.source_avito == 'https://www.avito.ru/new_999'
        assert listing.is_active is True
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
