from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    AbsenceEpisode,
    Base,
    EngineType,
    Listing,
    ListingChangeEvent,
    ListingLinkEvent,
    ListingObservation,
    ObservationState,
    ScanRun,
    SearchFilter,
    SourceImportSnapshot,
    VehicleFilterExpectation,
)
from app.scraper.base import canonical_listing_key
from app.service.analytics import activity_context, analytics_context
from app.service.monitor import MonitorService
from app.service.registry_audit import record_registry_change, registry_values

OLD = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/1234567890-old/'
NEW = 'https://auto.ru/cars/used/sale/mercedes/v_klasse/2234567890-new/'


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "analytics.db"}')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add_all([
            Listing(id='car', vehicle_signature='VIN-1', vin='VIN-1', brand='Mercedes-Benz',
                    model='V-Class', price_hint=15_000_000, source_auto_ru=NEW),
            SearchFilter(id='filter', source=EngineType.AUTO_RU, external_key='test', name='Москва'),
            ScanRun(id='run', source=EngineType.AUTO_RU, network_profile='local_browser'),
        ])
        session.flush()
        session.add(VehicleFilterExpectation(listing_id='car', filter_id='filter'))
        session.commit()
        yield session
    engine.dispose()


def observe(db, state, *, url=NEW, seconds_ago=60, payload=None, filter_id='filter', run_id='run'):
    found = state == ObservationState.FOUND
    db.add(ListingObservation(
        listing_id='car', filter_id=filter_id, run_id=run_id, source=EngineType.AUTO_RU,
        state=state, found=found, page_number=2 if found else 0, position_in_page=1 if found else 0,
        listing_url=url if found else None,
        raw_payload=payload if payload is not None else {
            'expected_listing_key': canonical_listing_key(EngineType.AUTO_RU, url), 'filter_version': 1,
        },
        observed_at=datetime.now(UTC) - timedelta(seconds=seconds_ago),
    ))
    db.commit()


def test_current_id_excludes_old_results_and_unattributed_legacy_absence(db):
    observe(db, ObservationState.FOUND, url=OLD)
    observe(db, ObservationState.ABSENT_CONFIRMED, payload={})
    data = analytics_context(db)
    assert data['placements'][0]['state'] == 'not_checked'
    assert data['placements'][0]['page'] is None
    assert data['period']['found'] == 1
    assert data['legacy_unattributed'] == 1


def test_latest_error_not_old_found_and_excluded_from_visibility_denominator(db):
    observe(db, ObservationState.FOUND, seconds_ago=90)
    observe(db, ObservationState.ABSENT_UNCERTAIN, seconds_ago=80)
    observe(db, ObservationState.TECHNICAL_ERROR, seconds_ago=70)
    data = analytics_context(db)
    assert data['placements'][0]['state'] == 'technical_error'
    assert data['placements'][0]['page'] is None
    assert data['period']['rate'] == 50.0
    assert data['period']['valid'] == 2
    assert data['period']['technical'] == 1
    assert sum(d['total'] for d in data['daily']) == data['period']['total']


def test_current_filter_version_and_removed_assignment_do_not_leak_into_current(db):
    observe(db, ObservationState.FOUND)
    db.get(SearchFilter, 'filter').version = 2
    db.commit()
    assert analytics_context(db)['placements'][0]['state'] == 'not_checked'
    db.query(VehicleFilterExpectation).delete()
    db.commit()
    db.expire_all()
    current = analytics_context(db)['placements'][0]
    assert current['state'] == 'not_configured'
    assert current['summary']['total'] == 0


def test_later_unattributed_legacy_result_does_not_promote_old_found(db):
    observe(db, ObservationState.FOUND, seconds_ago=100)
    observe(db, ObservationState.TECHNICAL_ERROR, seconds_ago=50, payload={})
    current = analytics_context(db)['placements'][0]
    assert current['state'] == 'not_checked'
    assert current['page'] is None
    assert current['summary']['found'] == 1


def test_vpn_diagnostics_excluded_and_empty_percentage_is_not_zero(db):
    db.add(ScanRun(id='vpn', source=EngineType.AUTO_RU, network_profile='local_vpn'))
    db.commit()
    observe(db, ObservationState.FOUND, run_id='vpn')
    data = analytics_context(db)
    assert data['period']['total'] == 0
    assert data['period']['rate'] is None
    assert next(run for run in data['runs'] if run['id'] == 'vpn')['trusted'] is False


def test_change_audit_is_idempotent_and_closes_old_episode_without_deleting_history(db):
    car = db.get(Listing, 'car')
    car.source_auto_ru = OLD
    db.add(SourceImportSnapshot(id='snapshot'))
    db.add(AbsenceEpisode(listing_id='car', filter_id='filter', source=EngineType.AUTO_RU))
    db.commit()
    previous = registry_values(car)
    car.source_auto_ru = NEW
    car.price_hint = 14_500_000
    car.is_active = False
    record_registry_change(db, car, previous, 'snapshot')
    db.commit()
    event = db.query(ListingChangeEvent).one()
    assert event.changes['price_hint'] == {'old': 15_000_000, 'new': 14_500_000}
    assert event.changes['is_active']['new'] is False
    assert db.query(ListingLinkEvent).one().new_url == NEW
    assert db.query(AbsenceEpisode).one().open is False
    assert 'without proof' in db.query(AbsenceEpisode).one().notes
    record_registry_change(db, car, registry_values(car), 'snapshot')
    db.commit()
    assert db.query(ListingChangeEvent).count() == 1
    assert db.query(ListingLinkEvent).count() == 1
    assert '14 500 000 ₽' in activity_context(db, kind='registry')['events'][0]['detail']
    assert analytics_context(db)['vehicle_count'] == 0
    assert analytics_context(db, scope='all')['vehicle_count'] == 1


def test_replacement_resets_streak_even_when_url_is_reused(db):
    observe(db, ObservationState.ABSENT_UNCERTAIN, seconds_ago=300)
    db.add(ListingLinkEvent(listing_id='car', source=EngineType.AUTO_RU, old_url=OLD,
                           new_url=NEW, actor='Менеджер', reason='Возвращена ссылка',
                           created_at=datetime.now(UTC) - timedelta(seconds=200)))
    db.commit()
    service = MonitorService(db)
    assert service._previous_miss_streak('car', 'filter', EngineType.AUTO_RU, 'local_browser') == (0, None)
    observe(db, ObservationState.ABSENT_UNCERTAIN, seconds_ago=100)
    assert service._previous_miss_streak('car', 'filter', EngineType.AUTO_RU, 'local_browser')[0] == 1


@pytest.mark.parametrize('name', ['placements', 'analytics', 'activity'])
def test_new_views_escape_user_content_and_have_shared_navigation(db, name):
    db.get(Listing, 'car').model = '<script>alert(1)</script>'
    db.commit()
    data = activity_context(db) if name == 'activity' else analytics_context(db)
    env = Environment(loader=FileSystemLoader(Path(__file__).parents[2] / 'src/app/templates'),
                      autoescape=select_autoescape(['html']))
    html = env.get_template(name + '.html').render(data=data)
    assert '<script>alert(1)</script>' not in html
    if name == 'placements':
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert 'aria-current="page"' in html
    assert '/static/app.css' in html
