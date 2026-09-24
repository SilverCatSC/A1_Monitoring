import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Base, DealerDiscoveryRun, DealerListingCandidate, EngineType, Listing
from app.scraper.base import canonical_listing_key, evidence_manifest_name
from app.service.placement_cycle import PlacementCycleService, _card_has_usable_id_claim
from app.service.placement_feed_snapshot import PlacementFeedError, PlacementFeedSnapshot

OLD = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_v-klass_8047929828'
NEW = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_v-klass_8176281881'
PLACEMENT_ID = 'MBVC011220262508260009'
VIN = 'W1VVNLTZ4T4788708'
EVIDENCE = 'artifacts/evidence/avito-card.png'


def test_catalogue_card_needs_exact_or_narrow_alias_id_claim_for_complete_coverage():
    assert _card_has_usable_id_claim({
        'card': {'placement_id': None, 'placement_id_raw': 'МBVC011220262508260009'},
    })
    assert not _card_has_usable_id_claim({'card': {'placement_id': None}})
    assert not _card_has_usable_id_claim({'card': {'placement_id_raw': 'BAD-CARD-ID'}})


def test_missing_feed_stops_before_any_catalogue_card_inspection(monkeypatch):
    def unavailable(_url):
        raise PlacementFeedError('feed export unavailable')

    monkeypatch.setattr('app.service.placement_cycle.read_placement_feed_snapshot', unavailable)
    result = PlacementCycleService(
        None, inspector=lambda *_args: 1 / 0, cycle_id='cycle-3',
    ).run({'batch_id': 'batch-3', 'discovery': {'run_ids': ['run-3']}}, 'https://docs.google.com')

    assert result == {'status': 'unavailable', 'reason': 'feed export unavailable', 'findings': 0}


def _snapshot():
    return PlacementFeedSnapshot(
        rows_by_sheet={
            'autoru-feed-all': [],
            'avito-feed-new': [{'Id': PLACEMENT_ID, 'VIN': VIN, 'AvitoId': '8047929828'}],
            'avito-feed-used': [],
        },
        sha256_by_sheet={
            'autoru-feed-all': 'a' * 64,
            'avito-feed-new': 'b' * 64,
            'avito-feed-used': 'c' * 64,
        },
        first_data_rows={'autoru-feed-all': 3, 'avito-feed-new': 4, 'avito-feed-used': 4},
    )


def test_cycle_opens_new_catalogue_card_and_reports_republication_without_url_write(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'network_profile', 'local_browser')
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path / 'evidence'))
    monkeypatch.setattr(settings, 'seller_identity_checks_limit', 10)
    monkeypatch.setattr('app.service.placement_cycle.read_placement_feed_snapshot',
                        lambda _url: _snapshot())
    engine = create_engine(f'sqlite:///{tmp_path / "placement.db"}')
    Base.metadata.create_all(engine)
    calls = []

    async def inspector(source, url, _progress):
        calls.append((source, url))
        return {
            'state': 'active',
            'card': {'placement_id': None, 'placement_id_raw': 'МBVC011220262508260009'},
            'evidence': EVIDENCE,
            'evidence_manifest': evidence_manifest_name(EVIDENCE),
        }

    with sessionmaker(bind=engine)() as db:
        db.add(Listing(
            id='car', vehicle_signature='car', vin=VIN,
            brand='Mercedes-Benz', model='V-Class', source_avito=OLD,
        ))
        auto_run = DealerDiscoveryRun(
            source=EngineType.AUTO_RU, dealer_url='https://auto.ru/diler/cars/all/a1/',
            network_profile='local_browser', complete=True,
        )
        avito_run = DealerDiscoveryRun(
            source=EngineType.AVITO, dealer_url='https://www.avito.ru/brands/a1/all/avtomobili',
            network_profile='local_browser', complete=True,
        )
        db.add_all([auto_run, avito_run])
        db.flush()
        db.add(DealerListingCandidate(
            source=EngineType.AVITO,
            external_key=canonical_listing_key(EngineType.AVITO, NEW),
            dealer_url=avito_run.dealer_url, listing_url=NEW,
            network_profile='local_browser', active=True,
            raw_payload={'discovery_run_id': avito_run.id},
        ))
        db.commit()

        result = PlacementCycleService(db, inspector=inspector, cycle_id='cycle-1').run(
            {'batch_id': 'batch-1', 'discovery': {'run_ids': [auto_run.id, avito_run.id]},
             'blocked_sources': []},
            'https://docs.google.com/spreadsheets/d/abc/export?format=csv',
        )

        assert result['status'] == 'complete'
        assert result['codes'] == {'mixed_script_id': 1, 'republication_candidate': 1}
        assert calls == [(EngineType.AVITO, NEW)]
        assert db.get(Listing, 'car').source_avito == OLD
        report = json.loads((tmp_path / 'evidence' / result['report_path']).read_text())
        candidate = next(item for item in report['findings']
                         if item['code'] == 'republication_candidate')
        assert candidate['observed_urls'] == [NEW]
        assert candidate['current_url'] == OLD
        assert candidate['id_match_basis'] == 'visual_alias'
        assert not candidate['platform_id_confirmed']
    engine.dispose()


def test_cycle_does_not_claim_complete_coverage_when_card_limit_is_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'network_profile', 'local_browser')
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path / 'evidence'))
    monkeypatch.setattr(settings, 'seller_identity_checks_limit', 0)
    monkeypatch.setattr('app.service.placement_cycle.read_placement_feed_snapshot',
                        lambda _url: _snapshot())
    engine = create_engine(f'sqlite:///{tmp_path / "placement.db"}')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        run = DealerDiscoveryRun(
            source=EngineType.AVITO, dealer_url='https://www.avito.ru/brands/a1/all/avtomobili',
            network_profile='local_browser', complete=True,
        )
        db.add(run)
        db.flush()
        db.add(DealerListingCandidate(
            source=EngineType.AVITO,
            external_key=canonical_listing_key(EngineType.AVITO, NEW),
            dealer_url=run.dealer_url, listing_url=NEW,
            network_profile='local_browser', active=True,
            raw_payload={'discovery_run_id': run.id},
        ))
        db.commit()

        result = PlacementCycleService(
            db, inspector=lambda *_args: 1 / 0, cycle_id='cycle-2'
        ).run({'batch_id': 'batch-2', 'discovery': {'run_ids': [run.id]},
               'blocked_sources': []}, 'https://docs.google.com/spreadsheets/d/abc/export')

        assert result['status'] == 'partial'
        assert result['codes'] == {'not_verified': 1}
        report = json.loads((tmp_path / 'evidence' / result['report_path']).read_text())
        assert report['catalogue_complete']['avito'] is False
        assert report['skipped_cards_by_source']['avito'] == 1
    engine.dispose()
