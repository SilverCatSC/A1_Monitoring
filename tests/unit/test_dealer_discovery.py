from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, DealerDiscoveryRun, DealerListingCandidate, EngineType
from app.scraper.base import ListingHit, ScanResult
from app.service.dealer_discovery import DealerDiscoveryService


class FakeAdapter:
    def __init__(self, result):
        self.result = result

    async def scan_filter(self, _url, _pages):
        return self.result


def _result(*hits, complete=True, error=None):
    return ScanResult(
        filter_id='',
        page_count=3 if complete else 1,
        hits=list(hits),
        diagnostics={'fixture': True},
        scanned_at=datetime.now(UTC),
        requested_pages=3,
        complete=complete,
        error=error,
    )


def _hit(url, title='Mercedes-Benz V-Class'):
    return ListingHit(
        external_id=url,
        title=title,
        url=url,
        page_number=1,
        position=1,
        price=15_000_000,
        raw={'fixture': True},
    )


def test_complete_dealer_snapshot_upserts_candidates(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "dealer.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    url = 'https://auto.ru/diler/cars/all/a1_avto_moskva/'
    listing_url = 'https://auto.ru/cars/used/sale/mercedes/v_class/1234567890-a/'
    monkeypatch.setattr('app.service.dealer_discovery.settings.dealer_auto_urls', url)
    monkeypatch.setattr('app.service.dealer_discovery.settings.dealer_avito_urls', '')
    try:
        service = DealerDiscoveryService(
            session,
            auto_adapter=FakeAdapter(_result(_hit(listing_url))),
            avito_adapter=FakeAdapter(_result()),
        )
        summary = service.run()
        candidate = session.query(DealerListingCandidate).one()

        assert summary == {'sources': 1, 'complete': 1, 'failed': 0, 'candidates': 1}
        assert candidate.external_key == 'auto_ru:1234567890'
        assert candidate.active is True
        assert session.query(DealerDiscoveryRun).one().complete is True
    finally:
        session.close()
        engine.dispose()


def test_incomplete_dealer_snapshot_does_not_deactivate_last_good_catalog(
    tmp_path, monkeypatch
):
    engine = create_engine(f'sqlite:///{tmp_path / "dealer-fail.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    url = 'https://auto.ru/diler/cars/all/a1_avto_moskva/'
    monkeypatch.setattr('app.service.dealer_discovery.settings.dealer_auto_urls', url)
    monkeypatch.setattr('app.service.dealer_discovery.settings.dealer_avito_urls', '')
    try:
        candidate = DealerListingCandidate(
            source=EngineType.AUTO_RU,
            external_key='auto_ru:1234567890',
            dealer_url=url,
            listing_url='https://auto.ru/cars/used/sale/mercedes/v_class/1234567890-a/',
            active=True,
        )
        session.add(candidate)
        session.commit()
        service = DealerDiscoveryService(
            session,
            auto_adapter=FakeAdapter(_result(complete=False, error='HTTP 429')),
            avito_adapter=FakeAdapter(_result()),
        )

        summary = service.run()

        assert summary['failed'] == 1
        assert session.query(DealerListingCandidate).one().active is True
    finally:
        session.close()
        engine.dispose()


def test_partial_dealer_snapshot_keeps_observed_new_candidates(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "dealer-partial.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    url = 'https://auto.ru/diler/cars/all/a1_avto_moskva/'
    listing_url = 'https://auto.ru/cars/used/sale/mercedes/v_class/1234567890-a/'
    monkeypatch.setattr('app.service.dealer_discovery.settings.dealer_auto_urls', url)
    monkeypatch.setattr('app.service.dealer_discovery.settings.dealer_avito_urls', '')
    try:
        service = DealerDiscoveryService(
            session,
            auto_adapter=FakeAdapter(
                _result(_hit(listing_url), complete=False, error='page 2 uncertain')
            ),
            avito_adapter=FakeAdapter(_result()),
        )

        summary = service.run()

        assert summary['failed'] == 1
        assert summary['candidates'] == 1
        assert session.query(DealerListingCandidate).one().active is True
    finally:
        session.close()
        engine.dispose()


def test_dealer_candidate_keeps_the_page_evidence_that_proved_its_presence(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "dealer-evidence.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    url = 'https://auto.ru/diler/cars/all/a1_avto_moskva/'
    listing_url = 'https://auto.ru/cars/used/sale/mercedes/v_class/1234567890-a/'
    monkeypatch.setattr('app.service.dealer_discovery.settings.dealer_auto_urls', url)
    monkeypatch.setattr('app.service.dealer_discovery.settings.dealer_avito_urls', '')
    try:
        result = _result(_hit(listing_url))
        result.diagnostics.update(
            page_1_evidence='catalogue.png',
            page_1_evidence_manifest='catalogue.png.json',
        )
        service = DealerDiscoveryService(
            session,
            auto_adapter=FakeAdapter(result),
            avito_adapter=FakeAdapter(_result()),
        )

        service.run()

        candidate = session.query(DealerListingCandidate).one()
        assert candidate.raw_payload['page_evidence'] == 'catalogue.png'
        assert candidate.raw_payload['page_evidence_manifest'] == 'catalogue.png.json'
    finally:
        session.close()
        engine.dispose()


def test_overlapping_dealer_discovery_is_rejected(tmp_path):
    import app.service.dealer_discovery as discovery_module

    engine = create_engine(f'sqlite:///{tmp_path / "dealer-lock.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    discovery_module._local_discovery_lock.acquire()
    try:
        try:
            DealerDiscoveryService(session).run()
            raise AssertionError('expected DiscoveryAlreadyRunning')
        except discovery_module.DiscoveryAlreadyRunning:
            pass
    finally:
        discovery_module._local_discovery_lock.release()
        session.close()
        engine.dispose()
