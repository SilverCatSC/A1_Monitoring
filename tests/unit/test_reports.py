from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    AbsenceEpisode,
    Base,
    EngineType,
    ListingObservation,
    ObservationState,
)
from app.service.report import weekend_summary


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
