from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.contracts import SourceRecord
from app.models import (
    Base,
    EngineType,
    Listing,
    ListingObservation,
    ListingReconciliation,
    ObservationState,
    ScanRun,
    SearchFilter,
)
from app.service import head_table_audit

VIN = 'W1VVNLSZXS4493307'
AUTO_URL = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1234567890-test/'
AVITO_URL = 'https://www.avito.ru/moskva/avtomobili/mercedes_v_klass_9876543210'
SITE_URL = 'https://a1auto.ru/cars-for-sale/v-vip_11_07.html'


def _session(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "head-audit.db"}')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def _head_record() -> SourceRecord:
    return SourceRecord(
        row_number=2,
        source={
            'vin': VIN,
            'brand_model': 'Mercedes-Benz V-Class',
            'configuration': 'V-VIP',
            'price_hint': '46 990 000',
            'year': '2026',
            'пробег': '0 км.',
            'ндс': 'с НДС',
            'наличие': 'В наличии',
            'source_status': 'Актуально',
            'listing_url_auto_ru': AUTO_URL,
            'listing_url_avito': AVITO_URL,
        },
        raw={},
    )


def test_head_row_requires_exactly_one_valid_vin() -> None:
    assert head_table_audit._head_row(2, {'vin': 'not-a-vin'}) is None
    row = head_table_audit._head_row(2, _head_record().source)
    assert row is not None
    assert row['vin'] == VIN
    assert row['price'] == 46_990_000
    assert row['active'] is True


def test_head_row_without_vin_uses_marketplace_url_and_status() -> None:
    row = head_table_audit._head_row(
        42,
        {
            'brand_model': 'Mercedes-Benz V-Class',
            'price_hint': '10 000 000',
            'source_status': 'Не актуально',
            'listing_url_auto_ru': AUTO_URL,
        },
    )

    assert row is not None
    assert row['vin'] is None
    assert row['vehicle_key'] == 'auto_ru:1234567890'
    assert row['identity_basis'] == 'marketplace_url'
    assert row['active'] is False


def test_head_row_without_vin_uses_company_site_url() -> None:
    row = head_table_audit._head_row(
        42,
        {
            'brand_model': 'Mercedes-Benz VLE',
            'source_status': 'Актуально',
            'listing_url': SITE_URL,
        },
    )

    assert row is not None
    assert row['vin'] is None
    assert row['vehicle_key'] == 'a1_site:/cars-for-sale/v-vip_11_07.html'
    assert row['identity_basis'] == 'company_site_url'


def test_audit_detects_deterministic_card_price_mismatch(monkeypatch, tmp_path) -> None:
    session, engine = _session(tmp_path)
    try:
        listing = Listing(
            id='car-1',
            vehicle_signature='car-1',
            vin=VIN,
            brand='Mercedes-Benz',
            model='V-Class',
            generation='V-VIP',
            year=2026,
            price_hint=46_990_000,
            source_auto_ru=AUTO_URL,
            source_avito=AVITO_URL,
            is_active=True,
        )
        run = ScanRun(id='run-1', source=EngineType.AUTO_RU, network_profile='local_browser')
        search_filter = SearchFilter(
            id='filter-1',
            source=EngineType.AUTO_RU,
            external_key='v-class',
            name='V-Class',
            raw_url='https://auto.ru/moskva/cars/mercedes/v_klasse/used/',
        )
        session.add_all([listing, run, search_filter])
        session.flush()
        session.add(
            ListingObservation(
                id='observation-1',
                run_id=run.id,
                listing_id=listing.id,
                filter_id=search_filter.id,
                source=EngineType.AUTO_RU,
                page_number=2,
                position_in_page=1,
                found=True,
                state=ObservationState.FOUND,
                title='Mercedes-Benz V-Class 2026',
                listing_url=AUTO_URL,
                price_hint=45_990_000,
                raw_payload={'raw_text': 'Mercedes-Benz V-Class 45 990 000 ₽'},
                observed_at=datetime.now(UTC),
            )
        )
        session.add(
            ListingReconciliation(
                id='reconciliation-1',
                batch_id='batch-1',
                listing_id=listing.id,
                source=EngineType.AUTO_RU,
                state='verified',
                url=AUTO_URL,
                reason='verified',
                details={
                    'direct_inspection': {
                        'state': 'active',
                        'status_code': 'active',
                        'reason': 'Карточка открывается',
                        'evidence': 'direct.png',
                        'card': {
                            'price': 44_990_000,
                            'year': 2025,
                            'vin': 'W1VVNLTZXT4617043',
                            'vat_status': 'Без НДС',
                        },
                    }
                },
                checked_at=datetime.now(UTC),
            )
        )
        session.commit()

        class Reader:
            def __init__(self, _url):
                pass

            def read(self):
                return [_head_record()]

        monkeypatch.setattr(head_table_audit, 'CsvOrXlsxReader', Reader)
        monkeypatch.setattr(head_table_audit.settings, 'head_table_google_sheet_export_url', 'https://docs.google.com/spreadsheets/d/test/export?format=csv')

        report = head_table_audit.audit_head_table(session)

        assert report['summary']['content_samples'] == 1
        assert any(issue['code'] == 'marketplace_price_mismatch' for issue in report['issues'])
        assert any(issue['code'] == 'marketplace_card_evidence_missing' for issue in report['issues'])
        sample = report['content_samples'][0]
        assert sample['vin'] == VIN
        assert sample['card_text'] == 'Mercedes-Benz V-Class 45 990 000 ₽'
        assert report['summary']['direct_card_samples'] == 1
        direct = report['direct_card_samples'][0]
        assert direct['status_code'] == 'active'
        assert direct['card_evidence'] == 'direct.png'
        codes = {issue['code'] for issue in report['issues']}
        assert {'direct_card_price_mismatch', 'direct_card_year_mismatch',
                'direct_card_vin_mismatch', 'direct_card_vat_mismatch'} <= codes
    finally:
        session.close()
        engine.dispose()
