"""An inaccessible dealer catalogue is not evidence that its ads disappeared."""
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import (
    AbsenceEpisode,
    Base,
    EngineType,
    Listing,
    ListingObservation,
    ListingReconciliation,
    ObservationState,
    ScanRun,
    ScanRunStatus,
    SearchFilter,
    VehicleFilterExpectation,
)
from app.scraper.base import ListingHit, ScanResult, canonical_listing_key
from app.service.completion import summarize_cycle_completion
from app.service.dealer_discovery import DealerDiscoveryService
from app.service.monitor import MonitorService
from app.service.pending_checks import build_pending_checks
from app.service.reconciliation import SellerReconciliationService


def test_avito_403_is_reported_end_to_end_without_visiting_stale_cards(tmp_path, monkeypatch):
    for key in ('scan_filter_pause_min_seconds', 'scan_filter_pause_max_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'scan_enabled_engines', 'avito')
    monkeypatch.setattr(settings, 'network_profile', 'local_no_vpn')
    url = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_v-klass_7888141521'
    evidence = 'avito_denied.png'

    class Denied:
        async def scan_filter(self, *args, **kwargs):
            return ScanResult(filter_id='', hits=[], page_count=0,
                scanned_at=datetime.now(UTC), requested_pages=5, complete=False, exhausted=False,
                error='page 1: RuntimeError: HTTP 403',
                diagnostics={'page_1_http_status': 403, 'page_1_state': 'blocked',
                             'page_1_evidence': evidence})

    class NoSearch:
        async def scan_filter(self, *args, **kwargs):
            raise AssertionError('Do not search a source whose current catalogue is blocked')

    async def no_card(*args, **kwargs):
        raise AssertionError('Do not open stale cards after a source access failure')

    engine = create_engine(f'sqlite:///{tmp_path / "access.db"}')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(Listing(id='car', vehicle_signature='car', source_avito=url))
        db.add(SearchFilter(id='filter', source=EngineType.AVITO, external_key='vclass', name='V-Class',
                            raw_url='https://www.avito.ru/moskva/avtomobili/mercedes-benz/v-klass-ASg'))
        db.flush()
        db.add(VehicleFilterExpectation(listing_id='car', filter_id='filter'))
        db.commit()
        discovery = DealerDiscoveryService(db, avito_adapter=Denied(), cycle_id='test-cycle')
        service = SellerReconciliationService(db, discovery=discovery, inspector=no_card, cycle_id='test-cycle')
        preflight = service.run()
        record = db.query(ListingReconciliation).one()
        assert record.state == 'unavailable'
        assert record.details['inspection_skip_reason'] == 'source_unavailable'
        assert record.details['source_failures'][0]['http_status'] == 403
        assert record.details['source_failures'][0]['evidence'] == evidence
        events = []
        search = MonitorService(db, avito_adapter=NoSearch(), preflight=preflight,
                                cycle_id='test-cycle', progress_callback=events.append).run_full_cycle()
        direct = service.inspect_current_cards(preflight)
        assert direct['checked'] == 0
        assert direct['skipped_not_in_catalogue'] == 0
        assert direct['skipped_source_unavailable'] == 1
        result = summarize_cycle_completion(search, direct)
        assert result['status'] == 'partial'
        assert result['technical_errors'] == result['dealer_catalogue_technical_errors'] == 1
        assert result['search_technical_errors'] == 0
        assert result['dealer_catalogue_failures'][0]['evidence'] == evidence
        assert 'dealer_catalogue_unavailable' in result['partial_reasons']
        skipped = next(e for e in events if e['event'] == 'filter_skipped')
        assert 'восстановите доступ' in skipped['reason']
        roster = [{'listing_id': 'car', 'sources': {'avito': canonical_listing_key(EngineType.AVITO, url)},
                   'filters': [{'id': 'filter', 'source': 'avito', 'version': 1}]}]
        pending = build_pending_checks(db, cycle_id='test-cycle', roster=roster, selected_sources={'avito'})
        catalogue = next(x for x in pending if x['phase'] == 'dealer_catalogue')
        assert catalogue['reason'] == 'http_403'
        assert catalogue['evidence'] == evidence
        search_item = next(x for x in pending if x['phase'] == 'search')
        assert search_item['reason'] == 'source_unavailable'
        assert search_item['evidence'] == evidence
        card = next(x for x in pending if x['phase'] == 'direct_card')
        assert card['reason'] == card['disposition'] == 'source_unavailable'
        assert card['action'] == 'restore_source_access'
        assert card['evidence'] == evidence
        assert db.get(Listing, 'car').is_active
        assert db.get(Listing, 'car').source_avito == url
    engine.dispose()


CURRENT_URL = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_v-klass_7888141521'
OTHER_URL = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_v-klass_7888141522'
UNRELATED_URL = 'https://www.avito.ru/moskva/avtomobili/mercedes-benz_v-klass_7000000001'
DEALER_EVIDENCE = 'avito_dealer_denied.png'


class _DeniedCatalogue:
    async def scan_filter(self, *args, **kwargs):
        return ScanResult(filter_id='', hits=[], page_count=0,
            scanned_at=datetime.now(UTC), requested_pages=5, complete=False, exhausted=False,
            error='page 1: RuntimeError: HTTP 403',
            diagnostics={'page_1_http_status': 403, 'page_1_state': 'blocked',
                         'page_1_evidence': DEALER_EVIDENCE})


class _SavedSearch:
    """Returns one precollected result per filter URL and records every call."""

    def __init__(self, results_by_url):
        self.results_by_url = results_by_url
        self.calls = []

    async def scan_filter(self, url, *args, **kwargs):
        self.calls.append(url)
        return self.results_by_url[url]


async def _no_card(*args, **kwargs):
    raise AssertionError('Do not open cards after a source access failure')


def _hit(url, page, position):
    return ListingHit(external_id=url.rsplit('_', 1)[-1], title='Mercedes-Benz V-Class', url=url,
                      page_number=page, position=position, price=9_500_000,
                      raw={'evidence': f'search_p{page}.png'})


def _search(*hits, complete=True, error=None):
    return ScanResult(filter_id='', hits=list(hits), page_count=3 if complete else 2,
                      scanned_at=datetime.now(UTC), requested_pages=5, complete=complete,
                      exhausted=complete, error=error,
                      diagnostics={'page_1_state': 'results', 'page_1_evidence': 'search_p1.png'})


def _filter_url(filter_id):
    return f'https://www.avito.ru/moskva/avtomobili/mercedes-benz/v-klass-ASg?f={filter_id}'


def _run_blocked_dealer_cycle(tmp_path, monkeypatch, listings, filters, searches,
                             network_profile='local_no_vpn', open_episode=False):
    """Collect search first, then hit HTTP 403 on the dealer catalogue, then match."""
    for key in ('scan_filter_pause_min_seconds', 'scan_filter_pause_max_seconds'):
        monkeypatch.setattr(settings, key, 0)
    monkeypatch.setattr(settings, 'scan_enabled_engines', 'avito')
    monkeypatch.setattr(settings, 'network_profile', network_profile)
    monkeypatch.setattr(settings, 'evidence_dir', str(tmp_path))
    engine = create_engine(f'sqlite:///{tmp_path / "saved_search.db"}')
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    for listing_id, url in listings.items():
        db.add(Listing(id=listing_id, vehicle_signature=listing_id, source_avito=url))
    for filter_id in filters:
        db.add(SearchFilter(id=filter_id, source=EngineType.AVITO, external_key=filter_id,
                            name=filter_id, raw_url=_filter_url(filter_id)))
    db.flush()
    for filter_id, listing_ids in filters.items():
        for listing_id in listing_ids:
            db.add(VehicleFilterExpectation(listing_id=listing_id, filter_id=filter_id))
            if open_episode:
                db.add(AbsenceEpisode(listing_id=listing_id, filter_id=filter_id,
                    source=EngineType.AVITO, started_at=datetime.now(UTC), open=True, consecutive_misses=2))
    db.commit()
    search = _SavedSearch({_filter_url(filter_id): result for filter_id, result in searches.items()})
    events = []
    monitor = MonitorService(db, avito_adapter=search, cycle_id='test-cycle', progress_callback=events.append)
    monitor.collect_search_results()
    discovery = DealerDiscoveryService(db, avito_adapter=_DeniedCatalogue(), cycle_id='test-cycle')
    reconciliation = SellerReconciliationService(db, discovery=discovery, inspector=_no_card, cycle_id='test-cycle')
    preflight = reconciliation.run()
    assert preflight['blocked_sources'] == ['avito']
    monitor.preflight = preflight
    summary = monitor.run_full_cycle()
    direct = reconciliation.inspect_current_cards(preflight)
    roster = [{'listing_id': listing_id, 'sources': {'avito': canonical_listing_key(EngineType.AVITO, url)},
               'filters': [{'id': filter_id, 'source': 'avito', 'version': 1}
                           for filter_id, listing_ids in filters.items() if listing_id in listing_ids]}
              for listing_id, url in listings.items()]
    pending = build_pending_checks(db, cycle_id='test-cycle', roster=roster, selected_sources={'avito'})
    return db, engine, search, events, summary, direct, pending


def test_url_only_position_does_not_close_production_absence_episode(tmp_path, monkeypatch):
    db, engine, *_ = _run_blocked_dealer_cycle(tmp_path, monkeypatch,
        listings={'car': CURRENT_URL}, filters={'filter': ['car']},
        searches={'filter': _search(_hit(CURRENT_URL, 1, 1))},
        network_profile='local_browser', open_episode=True)
    try:
        episode = db.query(AbsenceEpisode).one()
        assert episode.open and episode.ended_at is None
        assert db.query(ListingObservation).one().state == ObservationState.FOUND
        assert db.get(Listing, 'car').last_seen_at is None
        run = ScanRun(id='older-run', source=EngineType.AVITO, network_profile='local_browser')
        db.add(run)
        db.flush()
        db.add(ListingObservation(listing_id='car', filter_id='filter', run_id=run.id,
            source=EngineType.AVITO, state=ObservationState.ABSENT_UNCERTAIN, found=False,
            observed_at=datetime(2020, 1, 1, tzinfo=UTC), page_number=0, position_in_page=0))
        db.commit()
        # The provisional hit neither closes nor resets the incident history.
        assert MonitorService(db)._previous_miss_streak('car', 'filter', EngineType.AVITO, 'local_browser')[0] == 1
    finally:
        db.close()
        engine.dispose()


def test_saved_search_position_survives_blocked_dealer_catalogue_without_verifying_identity(tmp_path, monkeypatch):
    db, engine, search, events, summary, direct, pending = _run_blocked_dealer_cycle(
        tmp_path, monkeypatch,
        listings={'car': CURRENT_URL, 'car2': OTHER_URL},
        filters={'filter': ['car', 'car2']},
        searches={'filter': _search(_hit(UNRELATED_URL, 1, 1), _hit(CURRENT_URL, 1, 2),
                                    _hit(CURRENT_URL, 2, 1))},
    )
    try:
        # Collection ran once before the block; matching never searched again.
        assert search.calls == [_filter_url('filter')]
        assert summary['found'] == 1
        assert summary['review_required'] == 1
        assert summary['missed_confirmed'] == summary['missed_uncertain'] == 0
        observations = {row.listing_id: row for row in db.query(ListingObservation).all()}
        assert db.query(ListingObservation).count() == 2

        found = observations['car']
        assert found.state == ObservationState.FOUND
        assert (found.page_number, found.position_in_page, found.absolute_position) == (1, 2, 2)
        assert found.listing_url == CURRENT_URL
        assert found.matched_by == 'canonical_current_url'
        payload = found.raw_payload
        assert payload['evidence'] == 'search_p1.png'
        assert payload['scan_diagnostics']['page_1_evidence'] == 'search_p1.png'
        assert payload['scan_diagnostics']['search_complete'] is True
        assert payload['scan_diagnostics']['collection_order'] == 'search_before_active_cards'
        assert payload['position_provenance'] == {
            'basis': 'saved_search_canonical_current_url',
            'matched_by': 'canonical_current_url',
            'seller_preflight_error': 'source_unavailable',
            'seller_catalogue_verified': False,
            'card_identity_verified': False,
            'unique_id_read': False,
        }
        assert payload['seller_preflight']['state'] == 'unavailable'

        unfound = observations['car2']
        assert unfound.state == ObservationState.REVIEW_REQUIRED
        assert unfound.raw_payload['scan_diagnostics']['error'] == 'source_unavailable'
        assert db.query(AbsenceEpisode).count() == 0
        # Position is not activity: nothing marks the car as freshly seen or changes it.
        car = db.get(Listing, 'car')
        assert car.is_active and car.last_seen_at is None and car.source_avito == CURRENT_URL

        run = db.query(ScanRun).one()
        assert run.status == ScanRunStatus.PARTIAL
        assert run.filters_ok == 0
        assert run.pages_scanned == 3
        finished = [e for e in events if e['event'] == 'filter_finished' and not e.get('collection_only')]
        assert len(finished) == 1
        assert finished[0]['status'] == 'partial'
        assert (finished[0]['found'], finished[0]['links_rejected']) == (1, 1)
        assert finished[0]['unverified_seller_positions'] == 1

        assert direct['checked'] == 0
        assert direct['skipped_source_unavailable'] == 2
        result = summarize_cycle_completion(summary, direct)
        assert result['status'] == 'partial'
        assert 'dealer_catalogue_unavailable' in result['partial_reasons']
        assert result['search_technical_errors'] == 0

        search_items = {x['listing_id']: x for x in pending if x['phase'] == 'search'}
        assert set(search_items) == {'car2'}
        assert search_items['car2']['reason'] == 'source_unavailable'
        cards = {x['listing_id']: x for x in pending if x['phase'] == 'direct_card'}
        assert set(cards) == {'car', 'car2'}
        assert cards['car']['disposition'] == 'source_unavailable'
        catalogue = [x for x in pending if x['phase'] == 'dealer_catalogue']
        assert catalogue and catalogue[0]['reason'] == 'http_403'
    finally:
        db.close()
        engine.dispose()


def test_unrelated_url_filter_or_source_cannot_create_saved_search_position(tmp_path, monkeypatch):
    same_id_other_source = 'https://auto.ru/cars/used/sale/mercedes/v_class/7888141521-abcd/'
    db, engine, search, events, summary, direct, pending = _run_blocked_dealer_cycle(
        tmp_path, monkeypatch,
        listings={'car_a': CURRENT_URL, 'car_b': OTHER_URL},
        filters={'filter-a': ['car_a'], 'filter-b': ['car_b']},
        searches={
            # car_b is ranked only in filter-a, car_a only in filter-b.
            'filter-a': _search(_hit(OTHER_URL, 1, 1), _hit(UNRELATED_URL, 1, 2),
                                _hit(same_id_other_source, 1, 3)),
            'filter-b': _search(_hit(CURRENT_URL, 1, 1)),
        },
    )
    try:
        assert len(search.calls) == 2
        assert summary['found'] == 0
        assert summary['review_required'] == 2
        rows = db.query(ListingObservation).all()
        assert len(rows) == 2
        assert {row.state for row in rows} == {ObservationState.REVIEW_REQUIRED}
        assert all('position_provenance' not in row.raw_payload for row in rows)
        assert {row.raw_payload['scan_diagnostics']['error'] for row in rows} == {'source_unavailable'}
        assert {(x['listing_id'], x['filter_id']) for x in pending if x['phase'] == 'search'} == {
            ('car_a', 'filter-a'), ('car_b', 'filter-b')}
        assert db.query(ScanRun).one().pages_scanned == 0
        assert summarize_cycle_completion(summary, direct)['status'] == 'partial'
    finally:
        db.close()
        engine.dispose()


def test_incomplete_saved_search_hit_is_not_a_full_check(tmp_path, monkeypatch):
    db, engine, search, events, summary, direct, pending = _run_blocked_dealer_cycle(
        tmp_path, monkeypatch,
        listings={'car': CURRENT_URL},
        filters={'filter': ['car']},
        searches={'filter': _search(_hit(CURRENT_URL, 1, 1), complete=False,
                                    error='page 3: TimeoutError: navigation timeout')},
    )
    try:
        assert search.calls == [_filter_url('filter')]
        assert summary['found'] == 0
        assert summary['technical_errors'] == 1
        assert summary['missed_confirmed'] == summary['missed_uncertain'] == 0
        observation = db.query(ListingObservation).one()
        assert observation.state == ObservationState.REVIEW_REQUIRED
        assert 'position_provenance' not in observation.raw_payload
        run = db.query(ScanRun).one()
        assert run.status != ScanRunStatus.SUCCESS
        assert run.filters_ok == 0
        assert run.pages_scanned == 2
        result = summarize_cycle_completion(summary, direct)
        assert result['status'] == 'partial'
        assert 'search_technical_errors' in result['partial_reasons']
        assert [x['listing_id'] for x in pending if x['phase'] == 'search'] == ['car']
        assert db.query(AbsenceEpisode).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_saved_url_without_rank_evidence_stays_unverified(tmp_path, monkeypatch):
    hit = _hit(CURRENT_URL, 1, 1)
    hit.raw.clear()
    search_result = _search(hit)
    search_result.diagnostics.clear()
    db, engine, search, events, summary, direct, pending = _run_blocked_dealer_cycle(
        tmp_path, monkeypatch, listings={'car': CURRENT_URL},
        filters={'filter': ['car']}, searches={'filter': search_result},
    )
    try:
        assert summary['found'] == 0
        assert db.query(ListingObservation).one().state == ObservationState.REVIEW_REQUIRED
        assert any(item['phase'] == 'search' for item in pending)
        assert summarize_cycle_completion(summary, direct)['status'] == 'partial'
    finally:
        db.close()
        engine.dispose()


def test_incomplete_inventory_stays_partial_without_inventing_a_network_failure():
    result = summarize_cycle_completion({'dealer_catalogue_failures': [
        {'source': 'avito', 'error': 'Catalogue end not proven', 'technical_error': False}
    ]}, {})
    assert result['status'] == 'partial'
    assert result['technical_errors'] == 0
    assert result['partial_reasons'] == ['dealer_catalogue_incomplete']
