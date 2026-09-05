from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    AbsenceEpisode,
    Base,
    EngineType,
    ListingObservation,
    ObservationState,
    ScanRun,
    ScanRunStatus,
)
from app.service.report import latest_scan_runs_status, weekend_summary


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
        assert payload['runs'][0] == {
            'id': 'run-1',
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
                'technical_error': 0,
            },
            'evidence_files': 2,
            'evidence_pages': [1, 2],
            'notes': None,
        }
    finally:
        session.close()
        engine.dispose()
