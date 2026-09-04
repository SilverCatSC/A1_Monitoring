import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    EngineType,
    FeedbackEvent,
    FeedbackStatus,
    Listing,
    ListingObservation,
    ManagerFeedback,
    ScanRun,
    SearchFilter,
)
from app.service.feedback import FeedbackService, FeedbackValidationError


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "feedback.db"}')
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    yield db
    db.close()
    engine.dispose()


def test_feedback_lifecycle_is_audited(session):
    service = FeedbackService(session)
    feedback_id = service.create(
        message='Цена в объявлении не совпадает',
        severity='high',
        category='price_error',
        manager_name='Анна',
        source='dashboard',
    )
    service.update_status(
        feedback_id,
        'checking',
        actor='Иван',
        note='Проверяю источник',
        assignee='Иван',
    )
    service.update_status(feedback_id, 'fixed', actor='Иван', note='Цена исправлена')
    result = service.update_status(
        feedback_id, 'confirmed', actor='Анна', note='Проверено в выдаче'
    )

    assert result.status == FeedbackStatus.CONFIRMED
    assert result.closed_at is not None
    assert session.query(FeedbackEvent).count() == 4
    events = (
        session.query(FeedbackEvent)
        .filter(FeedbackEvent.feedback_id == feedback_id)
        .order_by(FeedbackEvent.created_at, FeedbackEvent.id)
        .all()
    )
    assert {event.actor for event in events} == {'Анна', 'Иван'}


def test_feedback_rejects_invalid_data_and_transition(session):
    service = FeedbackService(session)
    with pytest.raises(FeedbackValidationError, match='message'):
        service.create(message='   ')
    with pytest.raises(FeedbackValidationError, match='severity'):
        service.create(message='test', severity='urgent')

    feedback_id = service.create(message='Проверить фото', category='photo_error')
    with pytest.raises(FeedbackValidationError, match='not allowed'):
        service.update_status(feedback_id, 'confirmed', actor='Анна')
    assert session.query(ManagerFeedback).one().status == FeedbackStatus.NEW


def test_feedback_can_be_bound_to_exact_observation(session):
    listing = Listing(id='listing', vehicle_signature='signature', is_active=True)
    search_filter = SearchFilter(
        id='filter',
        source=EngineType.AUTO_RU,
        external_key='filter',
        name='Filter',
        active=True,
    )
    run = ScanRun(id='run', source=EngineType.AUTO_RU)
    session.add_all([listing, search_filter, run])
    session.flush()
    observation = ListingObservation(
        id='observation',
        run_id=run.id,
        listing_id=listing.id,
        filter_id=search_filter.id,
        source=EngineType.AUTO_RU,
        page_number=2,
        position_in_page=3,
        absolute_position=23,
        found=True,
    )
    session.add(observation)
    session.commit()

    feedback_id = FeedbackService(session).create(
        message='На карточке неверная цена',
        observed_id=observation.id,
        category='price_error',
        manager_name='Анна',
    )

    feedback = session.query(ManagerFeedback).filter(ManagerFeedback.id == feedback_id).one()
    assert feedback.observed_id == observation.id
    assert feedback.listing_id == listing.id
    assert feedback.filter_id == search_filter.id
    assert feedback.run_id == run.id
