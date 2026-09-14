from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import confirm_reconciliation
from app.config import settings
from app.models import (
    AbsenceEpisode,
    Base,
    DealerDiscoveryRun,
    DealerListingCandidate,
    EngineType,
    Listing,
    ListingLinkOverride,
    ListingObservation,
    ListingReconciliation,
    ObservationState,
    ScanRun,
    SearchFilter,
    VehicleFilterExpectation,
)
from app.schemas import ReconciliationConfirm
from app.scraper.base import (
    ListingHit,
    ScanResult,
    canonical_listing_key,
    is_marketplace_listing_url,
    is_marketplace_search_url,
)
from app.scraper.seller import direct_page_status, seller_page_matches
from app.service.dealer_discovery import DealerDiscoveryService
from app.service.filters import CANONICAL_FILTERS
from app.service.monitor import MonitorService
from app.service.reconciliation import SellerReconciliationService, reconciliation_context

OLD = 'https://auto.ru/cars/new/group/mercedes/vle/25032437/25069598/1133252498-92c0e2e5/'
NEW = 'https://auto.ru/cars/new/group/mercedes/vle/25032431/25069598/1133334954-b51707f0/'
DEALER = 'https://auto.ru/diler/cars/all/a1_avto_moskva/'


@pytest.fixture
def db(tmp_path, monkeypatch):
    for key in ('scan_page_pause_min_seconds', 'scan_page_pause_max_seconds', 'scan_filter_pause_min_seconds', 'scan_filter_pause_max_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'network_profile', 'local_browser')
    monkeypatch.setattr(settings, 'scan_enabled_engines', 'auto_ru')
    monkeypatch.setattr('app.service.reconciliation.SELLER_SOURCES', {'auto_ru': [DEALER]})
    engine = create_engine(f'sqlite:///{tmp_path / "preflight.db"}')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(Listing(id='car', vehicle_signature='car', brand='Mercedes-Benz', model='VLE', source_auto_ru=OLD))
        session.add(SearchFilter(id='filter', external_key='vle', source=EngineType.AUTO_RU, name='VLE', raw_url='https://auto.ru/moskva/cars/mercedes/vle/new/'))
        session.flush()
        session.add(VehicleFilterExpectation(listing_id='car', filter_id='filter'))
        session.commit()
        yield session
    engine.dispose()


def hit(url):
    return ListingHit(external_id=url, title='Mercedes-Benz VLE', url=url, page_number=1, position=1, price=23_850_000, raw={})


def result(urls=(), *, complete=True, exhausted=True, error=None):
    return ScanResult(filter_id='', page_count=1, hits=[hit(url) for url in urls], diagnostics={},
                      scanned_at=datetime.now(UTC), requested_pages=5, complete=complete, exhausted=exhausted, error=error)


class Adapter:
    def __init__(self, value):
        self.value, self.calls = value, []

    async def scan_filter(self, url, pages, **kwargs):
        self.calls.append((url, pages, kwargs))
        return self.value


async def no_status(*_):
    return {'state': 'unknown', 'reason': 'fixture'}


def reconcile(db, scan_result, inspector=no_status):
    adapter = Adapter(scan_result)
    service = SellerReconciliationService(db, discovery=DealerDiscoveryService(db, auto_adapter=adapter), inspector=inspector)
    return service.run(), adapter


def test_fresh_membership_proves_link_but_does_not_create_visibility(db):
    preflight, adapter = reconcile(db, result([OLD]))
    assert preflight['checks']['car:auto_ru']['state'] == 'verified'
    assert adapter.calls[0][2] == {'seller_catalogue': True}
    assert db.query(ListingObservation).count() == 0
    assert db.query(ListingReconciliation).count() == 1


def test_cycle_id_correlates_discovery_reconciliation_and_search(db):
    cycle_id = 'cycle-1'
    adapter = Adapter(result([OLD]))
    discovery = DealerDiscoveryService(
        db, auto_adapter=adapter, avito_adapter=adapter, cycle_id=cycle_id
    )
    preflight = SellerReconciliationService(
        db, discovery=discovery, cycle_id=cycle_id
    ).run()
    MonitorService(
        db, auto_adapter=Adapter(result([OLD])), preflight=preflight, cycle_id=cycle_id
    ).run_full_cycle()

    assert db.query(DealerDiscoveryRun).one().cycle_id == cycle_id
    assert db.query(ListingReconciliation).one().cycle_id == cycle_id
    run = db.query(ScanRun).one()
    assert run.cycle_id == cycle_id
    assert db.query(ListingObservation).one().raw_payload['cycle_id'] == cycle_id


def test_model_match_suggests_new_listing_but_never_rebinds_without_person(db):
    preflight, _ = reconcile(db, result([NEW]))
    assert preflight['checks']['car:auto_ru']['state'] == 'review_required'
    record = db.query(ListingReconciliation).one()
    assert record.candidates[0]['url'] == NEW
    assert db.get(Listing, 'car').source_auto_ru == OLD
    assert db.query(ListingLinkOverride).count() == 0


def test_old_cached_candidate_is_not_a_fresh_confirmation(db):
    db.add(DealerListingCandidate(source=EngineType.AUTO_RU, external_key=canonical_listing_key(EngineType.AUTO_RU, OLD),
        dealer_url=DEALER, listing_url=OLD, network_profile='local_browser', active=True,
        raw_payload={'discovery_run_id': 'old-run'}))
    db.commit()
    preflight, _ = reconcile(db, result(complete=False, exhausted=False, error='unknown layout'))
    assert preflight['checks']['car:auto_ru']['state'] != 'verified'
    assert db.query(DealerListingCandidate).one().active is True


def test_page_limit_is_not_a_complete_catalogue_but_positive_membership_is_usable(db):
    preflight, _ = reconcile(db, result([OLD], exhausted=False))
    assert db.query(DealerDiscoveryRun).one().complete is False
    assert 'end not proven' in db.query(DealerDiscoveryRun).one().error
    assert preflight['checks']['car:auto_ru']['state'] == 'verified'


def test_challenge_stops_second_catalogue_and_direct_inspection(db, monkeypatch):
    monkeypatch.setattr('app.service.reconciliation.SELLER_SOURCES', {'auto_ru': [DEALER, DEALER.replace('/cars/', '/lcv/')]})
    async def forbidden(*_):
        raise AssertionError('No request after CAPTCHA')
    preflight, adapter = reconcile(db, result(complete=False, exhausted=False, error='HTTP 429'), forbidden)
    assert len(adapter.calls) == 1
    assert preflight['blocked_sources'] == ['auto_ru']
    assert preflight['checks']['car:auto_ru']['state'] == 'unavailable'


def test_unresolved_link_skips_search_and_does_not_open_absence(db):
    preflight, _ = reconcile(db, result([NEW]))
    search = Adapter(result())
    summary = MonitorService(db, auto_adapter=search, preflight=preflight).run_full_cycle()
    assert search.calls == []
    assert summary['technical_errors'] == 1
    assert summary['missed_confirmed'] == summary['missed_uncertain'] == 0
    assert db.query(AbsenceEpisode).count() == 0
    observation = db.query(ListingObservation).one()
    assert observation.state == ObservationState.TECHNICAL_ERROR
    assert observation.raw_payload['seller_preflight']['state'] == 'review_required'


def test_verified_link_can_still_be_genuinely_missing_from_search(db):
    preflight, _ = reconcile(db, result([OLD]))
    search = Adapter(result())
    summary = MonitorService(db, auto_adapter=search, preflight=preflight).run_full_cycle()
    assert len(search.calls) == 1
    assert summary['missed_uncertain'] == 1
    assert db.query(ListingObservation).one().raw_payload['seller_preflight_batch'] == preflight['batch_id']


def test_duplicate_link_for_two_cars_requires_review(db):
    db.add(Listing(id='other', vehicle_signature='other', source_auto_ru=OLD))
    db.commit()
    preflight, _ = reconcile(db, result([OLD]))
    assert all(row['state'] == 'review_required' for row in preflight['checks'].values())


def test_removed_page_gets_separate_state_without_claiming_vehicle_sale(db):
    async def removed(*_):
        return {'state': 'removed', 'reason': 'Объявление снято'}
    preflight, _ = reconcile(db, result([NEW]), removed)
    assert preflight['checks']['car:auto_ru']['state'] == 'removed'
    assert 'не подтверждает продажу' in preflight['checks']['car:auto_ru']['reason']


def test_operator_confirmation_is_pinned_and_requires_new_preflight(db):
    reconcile(db, result([NEW]))
    record = db.query(ListingReconciliation).one()
    payload = ReconciliationConfirm(url=NEW, actor='Оператор', reason='Сверен автомобиль')
    confirm_reconciliation(record.id, payload, db)
    assert db.get(Listing, 'car').source_auto_ru == NEW
    assert db.query(ListingLinkOverride).one().url == NEW
    assert reconciliation_context(db)['rows'][0]['changed'] is True
    with pytest.raises(HTTPException) as error:
        confirm_reconciliation(record.id, payload, db)
    assert error.value.status_code == 409


def test_confirmation_rejects_unrelated_url_and_expired_snapshot(db):
    reconcile(db, result([NEW]))
    record = db.query(ListingReconciliation).one()
    with pytest.raises(HTTPException) as error:
        confirm_reconciliation(record.id, ReconciliationConfirm(url=OLD, actor='Оператор', reason='wrong candidate'), db)
    assert error.value.status_code == 422
    record.checked_at = datetime.now(UTC) - timedelta(days=2)
    db.commit()
    with pytest.raises(HTTPException) as error:
        confirm_reconciliation(record.id, ReconciliationConfirm(url=NEW, actor='Оператор', reason='old'), db)
    assert error.value.status_code == 409
    assert db.query(ListingLinkOverride).count() == 0


def test_confirmation_cannot_take_another_active_cars_link(db):
    reconcile(db, result([NEW]))
    record = db.query(ListingReconciliation).one()
    db.add(Listing(id='other', vehicle_signature='other', source_auto_ru=NEW))
    db.commit()
    with pytest.raises(HTTPException) as error:
        confirm_reconciliation(record.id, ReconciliationConfirm(url=NEW, actor='Оператор', reason='Проверено'), db)
    assert error.value.status_code == 409
    assert db.get(Listing, 'car').source_auto_ru == OLD
    assert db.query(ListingLinkOverride).count() == 0


def test_direct_inspection_is_bounded(db, monkeypatch):
    monkeypatch.setattr(settings, 'seller_direct_checks_limit', 1)
    db.add(Listing(id='other', vehicle_signature='other', source_auto_ru=NEW))
    db.commit()
    calls = []
    async def inspect(source, url, progress):
        calls.append(url)
        return {'state': 'unknown', 'reason': 'fixture'}
    preflight, _ = reconcile(db, result(), inspect)
    assert len(calls) == 1
    assert len(preflight['checks']) == 2
    assert all(row['state'] == 'review_required' for row in preflight['checks'].values())


def test_post_search_direct_card_inspection_is_saved_for_report(db):
    preflight, _ = reconcile(db, result([OLD]))

    async def inspect(_source, _url, _progress):
        return {
            'state': 'active',
            'status_code': 'active',
            'reason': 'Карточка открывается',
            'evidence': 'direct.png',
            'card': {'vat_status': 'С НДС', 'availability': 'В наличии'},
        }

    summary = SellerReconciliationService(db, inspector=inspect).inspect_current_cards(preflight)
    record = db.query(ListingReconciliation).one()

    assert summary['active'] == 1
    assert summary['checked'] == 1
    assert record.details['direct_inspection']['card']['vat_status'] == 'С НДС'
    assert reconciliation_context(db)['rows'][0]['direct_proof'].endswith('/evidence')


def test_mixed_filter_scans_verified_car_without_false_absence_for_stale_link(db):
    db.add(Listing(id='other', vehicle_signature='other', source_auto_ru=NEW))
    db.flush()
    db.add(VehicleFilterExpectation(listing_id='other', filter_id='filter'))
    db.commit()
    preflight, _ = reconcile(db, result([OLD]))
    search = Adapter(result([OLD]))
    summary = MonitorService(db, auto_adapter=search, preflight=preflight).run_full_cycle()
    observations = {row.listing_id: row for row in db.query(ListingObservation).all()}
    assert len(search.calls) == 1
    assert summary['technical_errors'] == 1
    assert observations['car'].state == ObservationState.FOUND
    assert observations['other'].state == ObservationState.TECHNICAL_ERROR
    assert db.query(AbsenceEpisode).count() == 0


def test_reconciliation_template_escapes_external_content(db):
    db.get(Listing, 'car').brand = '<script>alert(1)</script>'
    db.commit()
    reconcile(db, result([NEW]))
    env = Environment(loader=FileSystemLoader(Path(__file__).parents[2] / 'src/app/templates'), autoescape=select_autoescape(['html']))
    rendered = env.get_template('reconciliation.html').render(data=reconciliation_context(db))
    assert '<script>alert(1)</script>' not in rendered
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in rendered
    assert 'Подтверждаю: это именно этот автомобиль' in rendered


def test_vle_group_filter_is_not_confused_with_listing():
    from urllib.parse import parse_qs, urlsplit
    definition = next(f for f in CANONICAL_FILTERS if f.key == 'auto_vle_new')
    query = parse_qs(urlsplit(definition.url).query)
    assert query['geo_radius'] == ['0'] and query['rid'] == ['213']
    assert 'tech_param=25032431' in query['catalog_filter'][0]
    assert is_marketplace_search_url(EngineType.AUTO_RU, definition.url)
    assert not is_marketplace_listing_url(EngineType.AUTO_RU, definition.url)
    assert is_marketplace_listing_url(EngineType.AUTO_RU, NEW)
    assert not is_marketplace_search_url(EngineType.AUTO_RU, NEW)


@pytest.mark.parametrize('final,expected', [(DEALER, True), (DEALER+'?page=2', True), ('https://auth.auto.ru/login/', False),
                                          ('https://auto.ru/diler/cars/all/another_seller/', False)])
def test_seller_boundary(final, expected):
    assert seller_page_matches(DEALER, final) is expected


@pytest.mark.parametrize('html,status,expected', [('<h1>Автомобиль продан</h1>', 200, 'removed'),
    ('<h1>Mercedes VLE</h1><aside><h1>Автомобиль продан</h1></aside>', 200, 'active'),
    ('<h1>Не найдено</h1>', 404, 'unknown'), ('<h1>Ошибка</h1>', 429, 'blocked')])
def test_direct_ad_status_is_conservative(html, status, expected):
    assert direct_page_status(html, EngineType.AUTO_RU, OLD, OLD, status)['state'] == expected


def test_direct_avito_card_extracts_sales_fields_and_vat():
    avito_url = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_vle_8047929828'
    html = '''
    <h1>Mercedes-Benz VLE 2026</h1>
    <div data-marker="item-view/item-price">23 850 000 ₽</div>
    <div data-marker="item-view/item-description">
      Новый автомобиль, в пути. Цена указана с НДС. VIN W1VVNLTZXT4617043.
    </div>
    '''

    result = direct_page_status(html, EngineType.AVITO, avito_url, avito_url, 200)

    assert result['state'] == 'active'
    assert result['card']['price'] == 23_850_000
    assert result['card']['year'] == 2026
    assert result['card']['vin'] == 'W1VVNLTZXT4617043'
    assert result['card']['availability'] == 'В пути'
    assert result['card']['vat_status'] == 'С НДС'


def test_direct_price_can_be_read_from_metadata_and_vat_abbreviation():
    html = '''
    <h1>Mercedes-Benz VLE 2026</h1>
    <meta itemprop="price" content="23850000">
    <div itemprop="description">Стоимость указана в т.ч. НДС. Автомобиль в наличии.</div>
    '''

    result = direct_page_status(html, EngineType.AUTO_RU, NEW, NEW, 200)

    assert result['card']['price'] == 23_850_000
    assert result['card']['vat_status'] == 'С НДС'


def test_direct_avito_understands_not_subject_to_vat():
    avito_url = 'https://www.avito.ru/moskva/avtomobili/car_8047929828'
    html = '<h1>Автомобиль 2026</h1><div itemprop="description">Цена НДС не облагается.</div>'

    result = direct_page_status(html, EngineType.AVITO, avito_url, avito_url, 200)

    assert result['card']['vat_status'] == 'Без НДС'
