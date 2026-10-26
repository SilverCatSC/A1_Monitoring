"""Pending direct-card items separate stale-link skips from real verification work."""
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import (
    Base,
    DealerDiscoveryRun,
    EngineType,
    Listing,
    ListingObservation,
    ListingReconciliation,
    ObservationState,
    ScanRun,
    SearchFilter,
    VehicleFilterExpectation,
)
from app.scraper.base import canonical_listing_key, evidence_manifest_name
from app.scraper.seller import SELLER_SOURCES
from app.service.cycle_ledger import CycleLedgerService


def _database(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "dispositions.db"}')
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _avito_url(number):
    return f'https://www.avito.ru/moskva/avtomobili/mercedes-benz_v-klass_{number}'


ACTIVE_URL = _avito_url(8176281881)
STALE_URL = _avito_url(8176281882)
HTTP_URL = _avito_url(8176281883)


def _listing(db, cycle_id, listing_id, url):
    db.add(Listing(id=listing_id, vehicle_signature=f'vehicle-{listing_id}', source_avito=url))
    if url is None:
        db.commit()
        return
    search_filter = SearchFilter(id=f'filter-{listing_id}', source=EngineType.AVITO,
                                 external_key=listing_id, name=listing_id,
                                 raw_url='https://example.test/avito')
    db.add(search_filter)
    db.flush()
    db.add(VehicleFilterExpectation(listing_id=listing_id, filter_id=search_filter.id))
    run = ScanRun(cycle_id=cycle_id, source=EngineType.AVITO, network_profile='local_browser')
    db.add(run)
    db.flush()
    db.add(ListingObservation(
        run_id=run.id, listing_id=listing_id, filter_id=search_filter.id,
        source=EngineType.AVITO, page_number=0, position_in_page=0, found=True,
        state=ObservationState.FOUND,
        raw_payload={
            'expected_listing_key': canonical_listing_key(EngineType.AVITO, url),
            'filter_version': 1, 'scan_diagnostics': {},
        },
        observed_at=datetime.now(UTC),
    ))
    db.commit()


def _direct(db, cycle_id, listing_id, url, *, state='verified', details=None):
    db.add(ListingReconciliation(
        cycle_id=cycle_id, batch_id='batch-avito', listing_id=listing_id,
        source=EngineType.AVITO, state=state, url=url, reason='test',
        details=details or {}, checked_at=datetime.now(UTC),
    ))


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    engine, db = _database(tmp_path)
    monkeypatch.setattr(settings, 'scan_enabled_engines', 'avito')
    ledger = CycleLedgerService(db, evidence_dir=str(tmp_path / 'evidence'))
    cycle = ledger.start()
    _listing(db, cycle.id, 'car-active', ACTIVE_URL)
    _listing(db, cycle.id, 'car-stale', STALE_URL)
    _listing(db, cycle.id, 'car-http', HTTP_URL)
    _listing(db, cycle.id, 'car-nolink', None)
    ledger.seal_roster(cycle.id, 'snapshot')
    for url in SELLER_SOURCES['avito']:
        db.add(DealerDiscoveryRun(cycle_id=cycle.id, source=EngineType.AVITO,
                                  dealer_url=url, network_profile='local_browser',
                                  complete=True, error=None, diagnostics={}))
    evidence = 'avito-card.png'
    _direct(db, cycle.id, 'car-active', ACTIVE_URL, details={'direct_inspection': {
        'state': 'active', 'status_code': 'active', 'evidence': evidence,
        'evidence_manifest': evidence_manifest_name(evidence),
    }})
    # The active_catalogue_only policy skip, as written by the preflight stage.
    _direct(db, cycle.id, 'car-stale', STALE_URL, details={
        'inspection_skip_reason': 'not_in_active_catalogue',
    })
    # The 2026-10-07 HTTP 439 transport anomaly with parsed active content.
    _direct(db, cycle.id, 'car-http', HTTP_URL, details={'direct_inspection': {
        'state': 'unknown', 'status_code': 'http_error', 'http_status': 439,
        'content_state': 'active',
    }})
    db.commit()
    try:
        yield db, ledger, cycle, tmp_path
    finally:
        db.close()
        engine.dispose()


def test_pending_report_separates_skip_divergence_and_unfinished_checks(scenario):
    db, ledger, cycle, tmp_path = scenario
    summary = {'status': 'completed', 'partial_reasons': []}

    completed = ledger.complete(cycle.id, summary)

    # Nothing is deleted to look successful: the cycle stays partial.
    assert completed['status'] == 'partial'
    assert summary['pending_checks']['count'] == 3
    assert summary['pending_checks']['by_phase'] == {'direct_card': 3}
    assert summary['pending_checks']['direct_card_dispositions'] == {
        'intentional_skip': 1, 'link_divergence': 1, 'unfinished_check': 1,
    }
    report = json.loads(
        (tmp_path / 'evidence' / summary['pending_checks']['report_path']).read_text(encoding='utf-8')
    )
    items = {(item['listing_id']): item for item in report['items']}

    stale = items['car-stale']
    assert stale['reason'] == 'not_in_active_catalogue'
    assert stale['disposition'] == 'intentional_skip'
    assert stale['action'] == 'registry_link_reconciliation'

    http = items['car-http']
    assert http['reason'] == 'http_error'
    assert http['disposition'] == 'unfinished_check'
    assert http['action'] == 'verify_card'
    assert http['http_status'] == 439
    assert http['content_state'] == 'active'

    nolink = items['car-nolink']
    assert nolink['reason'] == 'link_missing'
    assert nolink['disposition'] == 'link_divergence'
    assert nolink['action'] == 'registry_link_confirmation'

    # A verified active card with evidence stays out of the pending report.
    assert 'car-active' not in items


def test_blocked_direct_card_is_never_presented_as_intentional_skip(scenario):
    db, ledger, cycle, tmp_path = scenario
    record = db.query(ListingReconciliation).filter_by(listing_id='car-http').one()
    details = dict(record.details or {})
    details['direct_inspection'] = {
        'state': 'blocked', 'status_code': 'skipped_after_block',
    }
    record.details = details
    db.commit()

    summary = {'status': 'completed', 'partial_reasons': []}
    ledger.complete(cycle.id, summary)

    report = json.loads(
        (tmp_path / 'evidence' / summary['pending_checks']['report_path']).read_text(encoding='utf-8')
    )
    item = next(item for item in report['items'] if item['listing_id'] == 'car-http')
    assert item['reason'] == 'skipped_after_block'
    assert item['disposition'] == 'unfinished_check'
    assert item['action'] == 'verify_card'


def test_complete_inactivity_and_no_declared_placement_are_not_pending_work(scenario):
    db, ledger, cycle, tmp_path = scenario
    record = db.query(ListingReconciliation).filter_by(listing_id='car-stale').one()
    record.details = {**record.details, 'catalogue_complete': True, 'registry_url': STALE_URL}
    _direct(db, cycle.id, 'car-nolink', None, state='missing_link', details={'registry_url': None})
    db.commit()
    summary = {'status': 'completed', 'partial_reasons': []}
    ledger.complete(cycle.id, summary)
    report = json.loads((tmp_path / 'evidence' / summary['pending_checks']['report_path']).read_text())
    assert [(item['listing_id'], item['reason']) for item in report['items']] == [('car-http', 'http_error')]
