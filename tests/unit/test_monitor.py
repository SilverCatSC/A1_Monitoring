from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    AbsenceEpisode,
    Base,
    EngineType,
    Listing,
    ListingObservation,
    ObservationState,
    ScanRunStatus,
    SearchFilter,
    VehicleFilterExpectation,
)
from app.scraper.base import (
    ListingHit,
    ScanResult,
    canonical_listing_key,
    classify_result_page,
    is_marketplace_listing_url,
    is_marketplace_search_url,
)
from app.service.monitor import MonitorService


class FakeAdapter:
    def __init__(self, results):
        self.results = list(results)

    async def scan_filter(self, _url: str, _max_pages: int) -> ScanResult:
        return self.results.pop(0)


def _result(*hits, complete=True, error=None):
    return ScanResult(
        filter_id='filter',
        page_count=3 if complete else 1,
        hits=list(hits),
        diagnostics={'fixture': True},
        scanned_at=datetime.now(UTC),
        requested_pages=3,
        complete=complete,
        error=error,
    )


def _hit(url: str):
    return ListingHit(
        external_id='1234567890',
        title='Mercedes-Benz V-Class',
        url=url,
        page_number=2,
        position=7,
        price=10_000_000,
        raw={'fixture': True},
    )


def _session(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "monitor.db"}')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def _seed(session):
    listing = Listing(
        vehicle_signature='W1VVNLTZ5S4556796',
        vin='W1VVNLTZ5S4556796',
        source_auto_ru='https://auto.ru/cars/used/sale/mercedes/v_class/1234567890-abcd/',
        is_active=True,
    )
    search_filter = SearchFilter(
        id='filter-1',
        source=EngineType.AUTO_RU,
        external_key='business-filter-1',
        name='V-Class Moscow',
        raw_url='https://auto.ru/moskva/cars/mercedes/v_class/used/',
        active=True,
    )
    session.add_all([listing, search_filter])
    session.flush()
    session.add(
        VehicleFilterExpectation(filter_id=search_filter.id, listing_id=listing.id)
    )
    session.commit()
    return listing, search_filter


def test_canonical_listing_key_ignores_slug_and_tracking_query():
    left = canonical_listing_key(
        EngineType.AUTO_RU,
        'https://auto.ru/cars/used/sale/mercedes/v_class/1234567890-old/?from=dealer',
    )
    right = canonical_listing_key(
        EngineType.AUTO_RU,
        'https://auto.ru/cars/used/sale/mercedes/v_class/1234567890-new/',
    )
    assert left == right == 'auto_ru:1234567890'
    assert (
        canonical_listing_key(
            EngineType.AVITO,
            'https://www.avito.ru/moskva/avtomobili/mercedes_9876543210?context=H4sIA',
        )
        == 'avito:9876543210'
    )


def test_result_page_classification_fails_closed():
    assert classify_result_page('<h1>Подтвердите, что вы не робот</h1>', 0)[0] == 'blocked'
    assert classify_result_page('<h1>По вашему запросу ничего не найдено</h1>', 0)[0] == 'empty'
    assert classify_result_page('<main>new unknown markup</main>', 0)[0] == 'unrecognized'
    assert classify_result_page('<article>car</article>', 1)[0] == 'results'


def test_filter_url_validation_does_not_confuse_a1auto_or_listing_pages():
    assert not is_marketplace_search_url(
        EngineType.AUTO_RU, 'https://a1auto.ru/cars-for-sale/v-businessjet.html'
    )
    assert not is_marketplace_search_url(
        EngineType.AUTO_RU,
        'https://auto.ru/cars/used/sale/mercedes/v_class/1234567890-car/',
    )
    assert is_marketplace_search_url(
        EngineType.AUTO_RU, 'https://auto.ru/moskva/cars/mercedes/v_class/used/'
    )
    assert is_marketplace_search_url(
        EngineType.AVITO,
        'https://www.avito.ru/brands/a1auto/items/all/avtomobili?s=profile_search_show_all',
    )
    assert is_marketplace_listing_url(
        EngineType.AUTO_RU,
        'https://auto.ru/cars/new/group/mercedes/vle/25032437/25069598/1133252498-car/',
    )


def test_incomplete_scan_records_technical_error_not_absence(tmp_path, monkeypatch):
    monkeypatch.setattr('app.service.monitor.settings.scan_enabled_engines', 'auto_ru')
    session, engine = _session(tmp_path)
    try:
        _seed(session)
        adapter = FakeAdapter([_result(complete=False, error='captcha')])
        summary = MonitorService(session, auto_adapter=adapter).run_full_cycle()

        observation = session.query(ListingObservation).one()
        assert observation.state == ObservationState.TECHNICAL_ERROR
        assert session.query(AbsenceEpisode).count() == 0
        assert summary['technical_errors'] == 1
        assert observation.scan_run.status == ScanRunStatus.FAILED
    finally:
        session.close()
        engine.dispose()


def test_inactive_expectations_are_ignored_and_missing_url_fails_closed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr('app.service.monitor.settings.scan_enabled_engines', 'auto_ru')
    session, engine = _session(tmp_path)
    try:
        active, search_filter = _seed(session)
        active.source_auto_ru = None
        inactive = Listing(
            vehicle_signature='X89183511M1GB1114',
            vin='X89183511M1GB1114',
            source_auto_ru='https://auto.ru/cars/used/sale/mercedes/v_class/2234567890-b/',
            is_active=False,
        )
        session.add(inactive)
        session.flush()
        session.add(
            VehicleFilterExpectation(filter_id=search_filter.id, listing_id=inactive.id)
        )
        session.commit()

        summary = MonitorService(
            session, auto_adapter=FakeAdapter([_result()])
        ).run_full_cycle()

        observations = session.query(ListingObservation).all()
        assert len(observations) == 1
        assert observations[0].listing_id == active.id
        assert observations[0].state == ObservationState.TECHNICAL_ERROR
        assert session.query(AbsenceEpisode).count() == 0
        assert summary['missed_uncertain'] == 0
        assert summary['technical_errors'] == 1
        assert observations[0].scan_run.status == ScanRunStatus.PARTIAL
    finally:
        session.close()
        engine.dispose()


def test_overlapping_scan_is_rejected(tmp_path):
    import app.service.monitor as monitor_module

    session, engine = _session(tmp_path)
    monitor_module._local_scan_lock.acquire()
    try:
        with pytest.raises(monitor_module.ScanAlreadyRunning):
            MonitorService(session).run_full_cycle()
    finally:
        monitor_module._local_scan_lock.release()
        session.close()
        engine.dispose()


def test_absence_requires_two_validated_misses_and_can_recur(tmp_path, monkeypatch):
    monkeypatch.setattr('app.service.monitor.settings.scan_enabled_engines', 'auto_ru')
    session, engine = _session(tmp_path)
    try:
        listing, _ = _seed(session)
        found = _hit(
            'https://auto.ru/cars/used/sale/mercedes/v_class/1234567890-new/?output_type=list'
        )
        adapter = FakeAdapter(
            [
                _result(),
                _result(),
                _result(found),
                _result(),
                _result(),
            ]
        )
        service = MonitorService(session, auto_adapter=adapter)

        first = service.run_full_cycle()
        assert first['missed_uncertain'] == 1
        assert session.query(AbsenceEpisode).count() == 0

        second = service.run_full_cycle()
        assert second['missed_confirmed'] == 1
        assert session.query(AbsenceEpisode).filter(AbsenceEpisode.open.is_(True)).count() == 1

        service.run_full_cycle()
        first_episode = session.query(AbsenceEpisode).one()
        assert first_episode.open is False
        assert listing.last_seen_at is not None

        service.run_full_cycle()
        service.run_full_cycle()
        episodes = session.query(AbsenceEpisode).order_by(AbsenceEpisode.started_at).all()
        assert len(episodes) == 2
        assert [episode.open for episode in episodes] == [False, True]
    finally:
        session.close()
        engine.dispose()
