import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    AbsenceEpisode,
    Base,
    EngineType,
    ListingObservation,
    MonitoringCycle,
    ObservationState,
    ScanRun,
    ScanRunStatus,
)
from app.service.report import latest_scan_runs_status, recent_monitoring_cycles, weekend_summary


def test_marketing_display_never_prefers_vin_or_guesses_ambiguous_url_rows(tmp_path, monkeypatch):
    from app.models import Listing
    from app.service.report import _head_records_for_listings

    url = 'https://www.avito.ru/moskva/avtomobili/v_8176328839'
    listing = Listing(id='car', vin='SAME-VIN', vehicle_signature='car', source_avito=url)
    rows = [
        {'row_number': 2, 'vin': 'SAME-VIN', 'active': True, 'status': 'Актуально',
         'avito_url': url.replace('8839', '8840'), 'price': 1},
        {'row_number': 3, 'vin': 'OTHER-VIN', 'active': True, 'status': 'Актуально',
         'avito_url': url, 'price': 2},
    ]
    path = tmp_path / 'latest.json'
    path.write_text(json.dumps({'records': rows}))
    monkeypatch.setattr('app.service.report.settings.head_table_audit_dir', str(tmp_path))
    matched, unmatched = _head_records_for_listings([listing])
    assert matched['car']['row_number'] == 3
    assert [row['row_number'] for row in unmatched] == [2]
    duplicate = Listing(id='other', vehicle_signature='other', source_avito=url)
    matched, unmatched = _head_records_for_listings([listing, duplicate])
    assert matched == {} and len(unmatched) == 2
    rows.append({**rows[1], 'row_number': 4})
    path.write_text(json.dumps({'records': rows}))
    matched, unmatched = _head_records_for_listings([listing])
    assert matched == {}
    assert len(unmatched) == 3  # All Актуально rows remain visible for review.


def test_url_only_positions_are_labelled_in_statistics_dashboard_and_history(tmp_path, monkeypatch):
    from app.models import Listing, SearchFilter
    from app.service.report import dashboard_context, filter_statistics, kpi_overview, listing_detail_context

    monkeypatch.setattr('app.service.report.settings.head_table_audit_dir', str(tmp_path))
    engine = create_engine(f'sqlite:///{tmp_path / "report-caveat.db"}')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        listing = Listing(id='car', vehicle_signature='car', source_avito=
                          'https://www.avito.ru/moskva/avtomobili/v_8176328839')
        search = SearchFilter(id='filter', source=EngineType.AVITO, external_key='filter', name='V-Class',
                              raw_url='https://www.avito.ru/moskva/avtomobili')
        run = ScanRun(id='run', source=EngineType.AVITO, network_profile='local_browser')
        db.add_all([listing, search, run])
        db.flush()
        now = datetime.now(UTC)
        for identifier, raw in [('url-only', {'position_provenance': {'card_identity_verified': False}}),
                                ('regular', {})]:
            db.add(ListingObservation(id=identifier, listing_id='car', filter_id='filter', run_id='run',
                source=EngineType.AVITO, state=ObservationState.FOUND, found=True,
                observed_at=now if identifier == 'url-only' else now - timedelta(seconds=1),
                page_number=2, position_in_page=1,
                listing_url=listing.source_avito, raw_payload=raw))
        db.commit()
        assert kpi_overview(db)['observed_found'] == 2
        assert kpi_overview(db)['unverified_positions'] == 1
        assert filter_statistics(db, datetime(2020, 1, 1, tzinfo=UTC))[0]['unverified_positions'] == 1
        detail = listing_detail_context(db, 'car')
        assert detail['page_statistics']['overall']['found_total'] == 2
        assert detail['page_statistics']['overall']['unverified_positions'] == 1
        dashboard = dashboard_context(db)
        assert dashboard['unverified_positions'] == 1
        assert dashboard['latest_found_notes']['url-only'] == 'Позиция по текущей ссылке; unique_id и активность не проверены'
        environment = Environment(loader=FileSystemLoader(Path(__file__).parents[2] / 'src/app/templates'),
                                  autoescape=select_autoescape(['html']))
        for name, context in [('listing_detail.html', detail), ('operations.html', dashboard)]:
            html = environment.get_template(name).render(context=context, auth_enabled=False)
            assert 'Без проверки unique_id: 1' in html or 'Из них без проверки unique_id: 1' in html
            assert 'unique_id и активность не проверены' in html
    engine.dispose()


def test_active_marketing_vehicle_retains_separate_removed_and_blocked_platform_states(tmp_path):
    from app.models import Listing, ListingReconciliation
    from app.service.report import _listing_catalog_card

    engine = create_engine(f'sqlite:///{tmp_path / "platform-states.db"}')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        listing = Listing(id='active-car', vehicle_signature='car', is_active=True,
                          source_auto_ru='https://auto.ru/cars/used/sale/mercedes/v/1133372535-a/',
                          source_avito='https://www.avito.ru/moskva/avtomobili/v_8176328839')
        removed = ListingReconciliation(id='removed', batch_id='batch', listing_id=listing.id, source=EngineType.AUTO_RU,
                                       state='removed', reason='Снятие подтверждено', details={'direct_inspection': {
                                           'state': 'removed', 'status_code': 'unpublished'}})
        blocked = ListingReconciliation(id='blocked', batch_id='batch', listing_id=listing.id, source=EngineType.AVITO,
                                       state='review_required', reason='CAPTCHA', details={'direct_inspection': {
                                           'state': 'blocked', 'status_code': 'blocked'}})
        db.add_all([listing, removed, blocked])
        db.commit()
        card = _listing_catalog_card(db, listing, {}, {}, {}, {
            (listing.id, EngineType.AUTO_RU): removed,
            (listing.id, EngineType.AVITO): blocked,
        }, {'status': 'Актуально'})
        assert card['listing'].is_active
        assert card['marketing']['status'] == 'Актуально'
        assert card['platforms']['auto_ru']['direct_label'] == 'Объявление снято'
        assert card['platforms']['auto_ru']['direct_class'] == 'absent_confirmed'
        assert card['platforms']['avito']['direct_class'] == 'technical_error'
    engine.dispose()


def test_url_only_position_keeps_catalogue_and_identity_caveat_in_report(tmp_path):
    from app.models import Listing, ListingReconciliation
    from app.service.report import _listing_catalog_card

    engine = create_engine(f'sqlite:///{tmp_path / "position-proof.db"}')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        listing = Listing(id='car', vehicle_signature='car', source_avito=
                          'https://www.avito.ru/moskva/avtomobili/v_8176328839')
        found = ListingObservation(run_id='run', listing_id='car', filter_id='filter',
            source=EngineType.AVITO, state=ObservationState.FOUND, found=True,
            page_number=2, position_in_page=3, listing_url=listing.source_avito,
            observed_at=datetime.now(UTC), raw_payload={'position_provenance': {
                'card_identity_verified': False, 'seller_catalogue_verified': False,
            }})
        blocked = ListingReconciliation(id='blocked', batch_id='batch', listing_id='car',
            source=EngineType.AVITO, state='unavailable', reason='HTTP 403', details={})
        db.add_all([listing, found, blocked])
        db.commit()
        key = ('car', EngineType.AVITO)
        card = _listing_catalog_card(db, listing, {key: found}, {key: found}, {},
                                     {key: blocked}, {'status': 'Актуально'})
        platform = card['platforms']['avito']
        assert platform['observation'].page_number == 2
        assert platform['position_note'] == 'Позиция по текущей ссылке; unique_id и активность не проверены'
        assert listing.last_seen_at is None
    engine.dispose()


def test_weekend_summary_counts_episodes_overlapping_each_day(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "reports.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add(
            AbsenceEpisode(
                listing_id='listing',
                filter_id='filter',
                source=EngineType.AUTO_RU,
                started_at=datetime(2026, 9, 4, 12, tzinfo=UTC),
                ended_at=datetime(2026, 9, 7, 8, tzinfo=UTC),
                open=False,
            )
        )
        session.add(
            ListingObservation(
                run_id='run',
                listing_id='listing',
                filter_id='filter',
                source=EngineType.AUTO_RU,
                page_number=0,
                position_in_page=0,
                found=False,
                state=ObservationState.TECHNICAL_ERROR,
                observed_at=datetime(2026, 9, 5, 9, tzinfo=UTC),
            )
        )
        session.commit()

        rows = weekend_summary(
            session,
            days=2,
            now=datetime(2026, 9, 6, 12, tzinfo=UTC),
        )
        assert [row['date'] for row in rows] == ['2026-09-05', '2026-09-06']
        assert [row['absence_episodes_overlapping'] for row in rows] == [1, 1]
        assert [row['technical_errors'] for row in rows] == [1, 0]
    finally:
        session.close()
        engine.dispose()


def test_latest_scan_status_exposes_outcomes_and_unique_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr('app.service.report.settings.scan_pages_limit', 3)
    monkeypatch.setattr('app.service.report.settings.scan_enabled_engines', 'auto_ru')
    engine = create_engine(f'sqlite:///{tmp_path / "latest-scan.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        run = ScanRun(
            id='run-1',
            source=EngineType.AUTO_RU,
            network_profile='cloud_no_vpn',
            status=ScanRunStatus.SUCCESS,
            filters_total=1,
            filters_ok=1,
            pages_scanned=3,
            technical_errors=0,
            started_at=datetime(2026, 9, 5, 9, tzinfo=UTC),
            finished_at=datetime(2026, 9, 5, 9, 2, tzinfo=UTC),
        )
        session.add(run)
        diagnostics = {
            'page_1_evidence': '/app/artifacts/page1.png',
            'page_2_evidence': '/app/artifacts/page2.png',
        }
        for index, state in enumerate(
            (ObservationState.FOUND, ObservationState.ABSENT_UNCERTAIN), start=1
        ):
            session.add(
                ListingObservation(
                    id=f'observation-{index}',
                    run_id=run.id,
                    listing_id=f'listing-{index}',
                    filter_id='filter-1',
                    source=EngineType.AUTO_RU,
                    page_number=index if state == ObservationState.FOUND else 0,
                    position_in_page=index if state == ObservationState.FOUND else 0,
                    found=state == ObservationState.FOUND,
                    state=state,
                    raw_payload={'scan_diagnostics': diagnostics},
                )
            )
        session.commit()

        payload = latest_scan_runs_status(session)

        assert payload['requested_pages'] == 3
        assert payload['cycle_id'] is None
        assert payload['runs'][0] == {
            'id': 'run-1',
            'cycle_id': None,
            'source': 'auto_ru',
            'status': 'success',
            'network_profile': 'cloud_no_vpn',
            'started_at': run.started_at,
            'finished_at': run.finished_at,
            'filters_total': 1,
            'filters_ok': 1,
            'pages_scanned': 3,
            'technical_errors': 0,
            'observations': 2,
            'state_counts': {
                'found': 1,
                    'absent_confirmed': 0,
                    'absent_uncertain': 1,
                    'filter_mismatch': 0,
                    'review_required': 0,
                    'technical_error': 0,
            },
            'evidence_files': 2,
            'evidence_pages': [1, 2],
            'notes': None,
        }
    finally:
        session.close()
        engine.dispose()


def test_cycle_scoped_scan_status_does_not_mix_runs(tmp_path, monkeypatch):
    monkeypatch.setattr('app.service.report.settings.scan_enabled_engines', 'auto_ru')
    engine = create_engine(f'sqlite:///{tmp_path / "cycle-scan.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add_all([
            ScanRun(id='old', cycle_id='old-cycle', source=EngineType.AUTO_RU,
                    network_profile='local_browser', started_at=datetime(2026, 9, 1, tzinfo=UTC)),
            ScanRun(id='new', cycle_id='new-cycle', source=EngineType.AUTO_RU,
                    network_profile='local_browser', started_at=datetime(2026, 9, 2, tzinfo=UTC)),
        ])
        session.commit()

        payload = latest_scan_runs_status(session, cycle_id='old-cycle')

        assert payload['cycle_id'] == 'old-cycle'
        assert payload['runs'][0]['id'] == 'old'
        assert payload['runs'][0]['cycle_id'] == 'old-cycle'
    finally:
        session.close()
        engine.dispose()


def test_recent_monitoring_cycles_exposes_partial_reasons(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "cycles.db"}')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add_all([
            MonitoringCycle(
                id='old', status='completed', network_profile='local_browser', app_version='0.10.0',
                started_at=datetime(2026, 9, 1, tzinfo=UTC), summary={'status': 'completed'},
            ),
            MonitoringCycle(
                id='partial', status='partial', network_profile='local_browser', app_version='0.10.0',
                started_at=datetime(2026, 9, 2, tzinfo=UTC),
                summary={'status': 'partial', 'partial_reasons': ['links_need_review']},
            ),
        ])
        session.commit()

        payload = recent_monitoring_cycles(session)

        assert [row['id'] for row in payload['cycles']] == ['partial', 'old']
        assert payload['cycles'][0]['summary']['partial_reasons'] == ['links_need_review']
    finally:
        session.close()
        engine.dispose()
