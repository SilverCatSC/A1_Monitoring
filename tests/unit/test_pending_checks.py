import json
from datetime import UTC, datetime

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
    ListingObservation,
    ListingReconciliation,
    MonitoringCycle,
    ObservationState,
    ScanRun,
    SearchFilter,
    VehicleFilterExpectation,
)
from app.scraper.base import canonical_listing_key, evidence_manifest_name
from app.scraper.seller import SELLER_SOURCES
from app.service.cycle_ledger import CycleLedgerService
from app.service.pending_checks import PendingChecksError, load_cycle_roster

AUTO_URL = 'https://auto.ru/cars/used/sale/mercedes/v_class/1133334954-b51707f0/'
AVITO_URL = 'https://www.avito.ru/moskva/avtomobili/mercedes_8176281881'


def _database(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "pending.db"}')
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _roster(db, sources):
    listing = Listing(id='car-1', vehicle_signature='vehicle-one',
                      source_auto_ru=AUTO_URL if 'auto_ru' in sources else None,
                      source_avito=AVITO_URL if 'avito' in sources else None)
    db.add(listing)
    for source in sources:
        search_filter = SearchFilter(id=f'filter-{source}', source=EngineType(source),
                                     external_key=source, name=source,
                                     raw_url=f'https://example.test/{source}')
        db.add(search_filter)
        db.flush()
        db.add(VehicleFilterExpectation(listing_id=listing.id, filter_id=search_filter.id))
    db.commit()
    return listing


def _catalogue_runs(db, cycle_id, source, *, complete):
    for url in SELLER_SOURCES[source]:
        db.add(DealerDiscoveryRun(cycle_id=cycle_id, source=EngineType(source),
                                  dealer_url=url, network_profile='local_browser',
                                  complete=complete, error=None if complete else 'HTTP 429',
                                  diagnostics={} if complete else {'page_1_http_status': 429}))


def _observation(db, cycle_id, source, state, diagnostics):
    kind = EngineType(source)
    url = AUTO_URL if source == 'auto_ru' else AVITO_URL
    run = ScanRun(cycle_id=cycle_id, source=kind, network_profile='local_browser')
    db.add(run)
    db.flush()
    db.add(ListingObservation(
        run_id=run.id, listing_id='car-1', filter_id=f'filter-{source}', source=kind,
        page_number=0, position_in_page=0, found=state == ObservationState.FOUND,
        state=state, raw_payload={
            'expected_listing_key': canonical_listing_key(kind, url),
            'filter_version': 1, 'scan_diagnostics': diagnostics,
        }, observed_at=datetime.now(UTC),
    ))
    return run


def _direct(db, cycle_id, source, inspection):
    db.add(ListingReconciliation(
        cycle_id=cycle_id, batch_id=f'batch-{source}', listing_id='car-1',
        source=EngineType(source), state='verified',
        url=AUTO_URL if source == 'auto_ru' else AVITO_URL,
        reason='test', details={'direct_inspection': inspection},
        checked_at=datetime.now(UTC),
    ))


def test_pending_report_keeps_captcha_and_first_request_429_unfinished(tmp_path, monkeypatch):
    engine, db = _database(tmp_path)
    monkeypatch.setattr(settings, 'scan_enabled_engines', 'auto_ru,avito')
    try:
        _roster(db, ('auto_ru', 'avito'))
        extra = SearchFilter(id='filter-auto-extra', source=EngineType.AUTO_RU,
                             external_key='auto-extra', name='Auto extra',
                             raw_url='https://example.test/auto-extra')
        db.add(extra)
        db.flush()
        db.add(VehicleFilterExpectation(listing_id='car-1', filter_id=extra.id))
        db.commit()
        ledger = CycleLedgerService(db, evidence_dir=str(tmp_path / 'evidence'))
        cycle = ledger.start()
        sealed = ledger.seal_roster(cycle.id, 'snapshot')
        _catalogue_runs(db, cycle.id, 'auto_ru', complete=True)
        _catalogue_runs(db, cycle.id, 'avito', complete=False)
        _observation(db, cycle.id, 'auto_ru', ObservationState.TECHNICAL_ERROR, {
            'error': 'blocked page 2: showcaptcha',
            'page_2_final_url': 'https://auto.ru/showcaptcha',
            'page_2_evidence': 'auto-captcha.png',
            'page_2_captcha_evidence': 'auto-captcha-before-operator.png',
        })
        _observation(db, cycle.id, 'avito', ObservationState.TECHNICAL_ERROR, {
            'error': 'source scan halted after marketplace challenge',
            'page_1_http_status': 429, 'page_1_evidence': 'avito-429.png',
        })
        auto_evidence = 'auto-card.png'
        _direct(db, cycle.id, 'auto_ru', {'state': 'active', 'evidence': auto_evidence,
                                         'evidence_manifest': evidence_manifest_name(auto_evidence)})
        _direct(db, cycle.id, 'avito', {'state': 'blocked', 'status_code': 'skipped_after_block'})
        db.commit()

        summary = {'status': 'completed', 'partial_reasons': []}
        completed = ledger.complete(cycle.id, summary)
        assert completed['status'] == 'partial'
        assert summary['pending_checks']['count'] == 5
        report_path = tmp_path / 'evidence' / summary['pending_checks']['report_path']
        report = json.loads(report_path.read_text(encoding='utf-8'))
        assert report['roster_sha256'] == sealed['roster_sha256']
        assert {(item['phase'], item['source'], item['reason']) for item in report['items']} == {
            ('search', 'auto_ru', 'captcha'),
            ('search', 'auto_ru', 'not_run'),
            ('search', 'avito', 'http_429'),
            ('dealer_catalogue', 'avito', 'http_429'),
            ('direct_card', 'avito', 'skipped_after_block'),
        }
        auto = next(item for item in report['items'] if item['phase'] == 'search'
                    and item['source'] == 'auto_ru' and item['reason'] == 'captcha')
        assert auto['filter_id'] == 'filter-auto_ru'
        assert auto['blocked_page'] == 2
        assert auto['evidence'] == 'auto-captcha-before-operator.png'
        assert db.get(MonitoringCycle, cycle.id).summary['pending_checks']['count'] == 5
    finally:
        db.close()
        engine.dispose()


def test_complete_cycle_writes_empty_pending_report(tmp_path, monkeypatch):
    engine, db = _database(tmp_path)
    monkeypatch.setattr(settings, 'scan_enabled_engines', 'auto_ru')
    try:
        _roster(db, ('auto_ru',))
        ledger = CycleLedgerService(db, evidence_dir=str(tmp_path / 'evidence'))
        cycle = ledger.start()
        ledger.seal_roster(cycle.id, 'snapshot')
        _catalogue_runs(db, cycle.id, 'auto_ru', complete=True)
        _observation(db, cycle.id, 'auto_ru', ObservationState.FOUND, {})
        evidence = 'auto-card.png'
        _direct(db, cycle.id, 'auto_ru', {'state': 'active', 'evidence': evidence,
                                         'evidence_manifest': evidence_manifest_name(evidence)})
        db.commit()

        summary = {'status': 'completed', 'partial_reasons': []}
        assert ledger.complete(cycle.id, summary)['status'] == 'completed'
        assert summary['pending_checks']['count'] == 0
    finally:
        db.close()
        engine.dispose()


def test_modified_sealed_roster_cannot_generate_pending_report(tmp_path, monkeypatch):
    engine, db = _database(tmp_path)
    monkeypatch.setattr(settings, 'scan_enabled_engines', 'auto_ru')
    try:
        _roster(db, ('auto_ru',))
        ledger = CycleLedgerService(db, evidence_dir=str(tmp_path / 'evidence'))
        cycle = ledger.start()
        sealed = ledger.seal_roster(cycle.id, 'snapshot')
        manifest_path = tmp_path / 'evidence' / sealed['manifest_path']
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['roster'][0]['listing_id'] = 'changed'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with pytest.raises(PendingChecksError, match='hash mismatch'):
            load_cycle_roster(tmp_path / 'evidence', sealed['manifest_path'], cycle.id)
    finally:
        db.close()
        engine.dispose()


def test_unopened_discovered_identity_card_is_named_in_pending_report(tmp_path, monkeypatch):
    engine, db = _database(tmp_path)
    monkeypatch.setattr(settings, 'scan_enabled_engines', 'auto_ru')
    try:
        _roster(db, ('auto_ru',))
        ledger = CycleLedgerService(db, evidence_dir=str(tmp_path / 'evidence'))
        cycle = ledger.start()
        ledger.seal_roster(cycle.id, 'snapshot')
        _catalogue_runs(db, cycle.id, 'auto_ru', complete=True)
        db.flush()
        run = db.query(DealerDiscoveryRun).filter_by(cycle_id=cycle.id).first()
        key = canonical_listing_key(EngineType.AUTO_RU, AUTO_URL)
        db.add(DealerListingCandidate(
            source=EngineType.AUTO_RU, external_key=key,
            dealer_url=run.dealer_url, listing_url=AUTO_URL,
            network_profile='local_browser', raw_payload={'discovery_run_id': run.id},
        ))
        db.commit()
        report_path = tmp_path / 'evidence' / 'cycles' / cycle.id / 'placement.json'
        report_path.write_text(json.dumps({'catalogue_complete': {'auto_ru': False},
                                           'observed_cards': []}), encoding='utf-8')

        summary = {'status': 'partial', 'partial_reasons': [],
                   'placement_reconciliation': {'status': 'partial',
                                                'report_path': f'cycles/{cycle.id}/placement.json'}}
        ledger.complete(cycle.id, summary)
        report = json.loads((tmp_path / 'evidence' / summary['pending_checks']['report_path']).read_text())
        assert ('identity_card', 'auto_ru', key) in {
            (item['phase'], item['source'], item.get('listing_key')) for item in report['items']
        }
        assert ('identity_coverage', 'auto_ru') in {
            (item['phase'], item['source']) for item in report['items']
        }
    finally:
        db.close()
        engine.dispose()
