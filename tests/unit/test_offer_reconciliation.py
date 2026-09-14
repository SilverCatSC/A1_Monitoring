from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, DealerListingCandidate, EngineType, Listing, ListingReconciliation
from app.service.offer_reconciliation import offer_review_queue

AUTO_URL = 'https://auto.ru/cars/used/sale/mercedes/v/1132311022-old/'
AVITO_URL = 'https://www.avito.ru/moskva/avtomobili/v_8047929828'


def _session(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "offers.db"}')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def _record(listing, source, *, state='verified', details=None, candidates=None):
    url = listing.source_auto_ru if source == EngineType.AUTO_RU else listing.source_avito
    return ListingReconciliation(
        listing_id=listing.id,
        source=source,
        batch_id='batch',
        state=state,
        url=url,
        reason='fixture',
        details=details or {},
        candidates=candidates or [],
    )


def test_offer_review_queue_explains_price_vin_year_and_ghost_offer(tmp_path):
    session, engine = _session(tmp_path)
    try:
        listing = Listing(
            id='car',
            vehicle_signature='car',
            brand='Mercedes-Benz',
            model='V-Class',
            year=2024,
            vin='W1VVNLTZ5S4556796',
            price_hint=10_000_000,
            source_auto_ru=AUTO_URL,
            is_active=True,
        )
        session.add(listing)
        session.flush()
        session.add(
            _record(
                listing,
                EngineType.AUTO_RU,
                details={
                    'discovery_run_ids': ['run-auto'],
                    'direct_inspection': {
                        'state': 'active',
                        'status_code': 'active',
                        'evidence': 'direct.png',
                        'evidence_manifest': 'direct.png.json',
                        'card': {
                            'price': 11_000_000,
                            'year': 2023,
                            'vin': 'X89183511M1GB1114',
                        },
                    },
                },
            )
        )
        session.add(
            DealerListingCandidate(
                source=EngineType.AUTO_RU,
                external_key='auto_ru:1133334954',
                dealer_url='https://auto.ru/diler/cars/all/a1/',
                listing_url='https://auto.ru/cars/used/sale/mercedes/v/1133334954-new/',
                active=True,
                raw_payload={
                    'discovery_run_id': 'run-auto',
                    'page_evidence': 'catalogue.png',
                    'page_evidence_manifest': 'catalogue.png.json',
                },
            )
        )
        session.commit()

        queue = offer_review_queue(session)
        codes = {item['code'] for item in queue['findings']}

        assert {'price_mismatch', 'year_mismatch', 'vin_mismatch', 'ghost_offer'} <= codes
        price = next(item for item in queue['findings'] if item['code'] == 'price_mismatch')
        assert price['values'] == {'registry_price': 10_000_000, 'card_price': 11_000_000}
        assert price['proof'].endswith('/evidence')
    finally:
        session.close()
        engine.dispose()


def test_offer_review_queue_keeps_missing_offer_and_vat_as_separate_actions(tmp_path):
    session, engine = _session(tmp_path)
    try:
        avito = Listing(
            id='avito',
            vehicle_signature='avito',
            brand='Mercedes-Benz',
            model='V-Class',
            source_avito=AVITO_URL,
            is_active=True,
        )
        removed = Listing(
            id='removed',
            vehicle_signature='removed',
            source_auto_ru=AUTO_URL,
            is_active=True,
        )
        session.add_all([avito, removed])
        session.flush()
        session.add_all(
            [
                _record(
                    avito,
                    EngineType.AVITO,
                    details={
                        'direct_inspection': {
                            'state': 'active',
                            'status_code': 'active',
                            'evidence': 'avito.png',
                            'evidence_manifest': 'avito.png.json',
                            'card': {'vat_status': None},
                        }
                    },
                ),
                _record(
                    removed,
                    EngineType.AUTO_RU,
                    state='removed',
                    details={
                        'direct_inspection': {
                            'state': 'removed',
                            'status_code': 'sold',
                            'reason': 'fixture removal',
                            'evidence': 'removed.png',
                            'evidence_manifest': 'removed.png.json',
                        }
                    },
                ),
            ]
        )
        session.commit()

        queue = offer_review_queue(session)
        codes = {item['code'] for item in queue['findings']}

        assert 'vat_not_disclosed' in codes
        assert 'missing_offer' in codes
        assert 'direct_check_incomplete' not in codes
    finally:
        session.close()
        engine.dispose()


def test_offer_review_queue_does_not_treat_unproven_direct_state_as_business_fact(tmp_path):
    session, engine = _session(tmp_path)
    try:
        listing = Listing(
            id='car',
            vehicle_signature='car',
            source_auto_ru=AUTO_URL,
            is_active=True,
        )
        session.add(listing)
        session.flush()
        session.add(
            _record(
                listing,
                EngineType.AUTO_RU,
                details={'direct_inspection': {'state': 'removed', 'status_code': 'sold'}},
            )
        )
        session.commit()

        queue = offer_review_queue(session)

        assert [item['code'] for item in queue['findings']] == ['direct_evidence_missing']
    finally:
        session.close()
        engine.dispose()
