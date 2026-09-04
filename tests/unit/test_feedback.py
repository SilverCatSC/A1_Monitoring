import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, FeedbackEvent, FeedbackStatus, ManagerFeedback
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
