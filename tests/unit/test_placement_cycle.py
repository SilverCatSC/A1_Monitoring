import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import (
    Base,
    DealerDiscoveryRun,
    DealerListingCandidate,
    EngineType,
    Listing,
    ListingReconciliation,
)
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


def test_active_card_opened_for_id_is_reused_for_current_direct_check(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'network_profile', 'local_browser')
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path / 'evidence'))
    monkeypatch.setattr(settings, 'seller_identity_checks_limit', 80)
    monkeypatch.setattr('app.service.placement_cycle.read_placement_feed_snapshot',
                        lambda _url: _snapshot())
    engine = create_engine(f'sqlite:///{tmp_path / "reuse.db"}')
    Base.metadata.create_all(engine)
    calls = []

    async def inspector(source, url, _progress):
        calls.append((source, url))
        return {
            'state': 'active', 'status_code': 'active',
            'card': {'placement_id': PLACEMENT_ID},
            'evidence': EVIDENCE,
            'evidence_manifest': evidence_manifest_name(EVIDENCE),
        }

    with sessionmaker(bind=engine)() as db:
        db.add(Listing(id='car', vehicle_signature='car', vin=VIN,
                       source_avito=NEW, is_active=True))
        db.add(ListingReconciliation(
            id='current-check', cycle_id='cycle-reuse', batch_id='batch-reuse',
            listing_id='car', source=EngineType.AVITO, state='verified',
            url=NEW, reason='URL in seller catalogue', details={}, candidates=[],
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
            source=EngineType.AVITO, external_key=canonical_listing_key(EngineType.AVITO, NEW),
            dealer_url=avito_run.dealer_url, listing_url=NEW,
            network_profile='local_browser', active=True,
            raw_payload={'discovery_run_id': avito_run.id},
        ))
        db.commit()

        result = PlacementCycleService(db, inspector=inspector, cycle_id='cycle-reuse').run(
            {'batch_id': 'batch-reuse', 'discovery': {'run_ids': [auto_run.id, avito_run.id]},
             'blocked_sources': []}, 'https://docs.google.com/spreadsheets/d/abc/export',
        )

        assert result['status'] == 'complete'
        assert calls == [(EngineType.AVITO, NEW)]
        record = db.get(ListingReconciliation, 'current-check')
        assert record.details['direct_inspection']['evidence'] == EVIDENCE
        report = json.loads((tmp_path / 'evidence' / result['report_path']).read_text())
        assert report['reused_direct_cards'] == 1
    engine.dispose()


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


@pytest.mark.parametrize('record_state,assigned_elsewhere,queued', [
    ('removed', False, True),
    ('verified', False, False),
    ('removed', True, False),
])
def test_cycle_queues_id_candidate_only_for_reviewable_check_without_url_write(
    tmp_path, monkeypatch, record_state, assigned_elsewhere, queued,
):
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
        if assigned_elsewhere:
            db.add(Listing(
                id='other-car', vehicle_signature='other-car', vin='OTHER-VIN',
                brand='Mercedes-Benz', model='V-Class', source_avito=NEW,
            ))
        db.add(ListingReconciliation(
            id='check-1', batch_id='batch-1', listing_id='car', source=EngineType.AVITO,
            state=record_state, url=OLD, reason='Earlier link needs review', candidates=[], details={},
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
        check = db.get(ListingReconciliation, 'check-1')
        if queued:
            assert candidate['operator_review_check_id'] == 'check-1'
            assert len(check.candidates) == 1
            assert check.candidates[0]['url'] == NEW
            assert check.candidates[0]['placement_id'] == PLACEMENT_ID
            assert check.candidates[0]['id_match_basis'] == 'visual_alias'
            assert check.candidates[0]['evidence'] == EVIDENCE
        else:
            assert 'operator_review_check_id' not in candidate
            assert check.candidates == []
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
