from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    EngineType,
    Listing,
    ListingObservation,
    ObservationState,
    ScanRun,
    ScanRunStatus,
    SearchFilter,
    SourceImportSnapshot,
)
from app.service.report import dashboard_context, operational_status


def _session(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "status.db"}')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def test_status_reports_healthy_recent_cycle(tmp_path):
    session, engine = _session(tmp_path)
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    try:
        session.add(
            SourceImportSnapshot(
                started_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
                blocked_by_schema_drift=False,
            )
        )
        session.add(
            SearchFilter(
                source=EngineType.AUTO_RU,
                external_key='health-filter',
                name='Health filter',
                raw_url='https://auto.ru/moskva/cars/all/',
                active=True,
            )
        )
        session.add(
            ScanRun(
                source=EngineType.AUTO_RU,
                started_at=now - timedelta(minutes=3),
                finished_at=now - timedelta(minutes=2),
                status=ScanRunStatus.SUCCESS,
                filters_total=1,
                filters_ok=1,
            )
        )
        session.commit()

        result = operational_status(
            session, now=now, interval_minutes=30, enabled_sources=['auto_ru']
        )

        assert result['overall'] == 'healthy'
        assert result['import']['state'] == 'healthy'
        assert result['sources'][0]['state'] == 'healthy'
    finally:
        session.close()
        engine.dispose()


def test_status_separates_unconfigured_source_from_overdue_import(tmp_path):
    session, engine = _session(tmp_path)
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    try:
        session.add(
            SourceImportSnapshot(
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=2),
                blocked_by_schema_drift=False,
            )
        )
        session.commit()

        result = operational_status(
            session, now=now, interval_minutes=30, enabled_sources=['avito']
        )

        assert result['overall'] == 'action_required'
        assert result['import']['state'] == 'overdue'
        assert result['sources'][0]['state'] == 'not_configured'
        assert result['sources'][0]['active_filters'] == 0
    finally:
        session.close()
        engine.dispose()


def test_dashboard_operational_metrics_ignore_disabled_filter_history(tmp_path):
    session, engine = _session(tmp_path)
    now = datetime.now(UTC)
    try:
        listing = Listing(
            id='listing',
            vehicle_signature='signature',
            vin='W1VVNLTZ5S4556796',
            is_active=True,
        )
        disabled_filter = SearchFilter(
            id='disabled-filter',
            source=EngineType.AUTO_RU,
            external_key='disabled',
            name='Disabled legacy filter',
            active=False,
        )
        run = ScanRun(
            id='run',
            source=EngineType.AUTO_RU,
            started_at=now,
            finished_at=now,
            status=ScanRunStatus.FAILED,
        )
        session.add_all([listing, disabled_filter, run])
        session.flush()
        session.add(
            ListingObservation(
                run_id=run.id,
                listing_id=listing.id,
                filter_id=disabled_filter.id,
                source=EngineType.AUTO_RU,
                page_number=0,
                position_in_page=0,
                found=False,
                state=ObservationState.TECHNICAL_ERROR,
                observed_at=now,
            )
        )
        session.commit()

        context = dashboard_context(session)

        assert context['observation_counts']['technical_error'] == 0
        assert context['filter_statistics'] == []
    finally:
        session.close()
        engine.dispose()
