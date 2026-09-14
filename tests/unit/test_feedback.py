from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import create_reconciliation_feedback
from app.models import (
    Base,
    EngineType,
    FeedbackEvent,
    FeedbackStatus,
    Listing,
    ListingObservation,
    ListingReconciliation,
    ManagerFeedback,
    ScanRun,
    SearchFilter,
)
from app.schemas import OfferFindingFeedbackCreate
from app.security import AuthenticatedActor
from app.service.exception_report import offer_exception_report
from app.service.feedback import FeedbackService, FeedbackValidationError
from app.service.report import feedback_queue_context


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
    result = service.update_status(feedback_id, 'confirmed', actor='Анна', note='Проверено в выдаче')

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
    with pytest.raises(FeedbackValidationError, match='assignee'):
        service.update_status(feedback_id, 'assigned', actor='Анна')
    assert session.query(ManagerFeedback).one().status == FeedbackStatus.NEW

    confirmation_id = service.create(message='Проверить исправление')
    service.update_status(confirmation_id, 'checking', actor='Иван')
    service.update_status(confirmation_id, 'fixed', actor='Иван')
    with pytest.raises(FeedbackValidationError, match='confirmation note'):
        service.update_status(confirmation_id, 'confirmed', actor='Анна')


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

    context = feedback_queue_context(session)
    assert context['total'] == 1
    assert context['status_counts']['new'] == 1
    assert context['rows'][0]['observation'].id == observation.id
    assert context['rows'][0]['filter'].id == search_filter.id
    assert set(context['rows'][0]['allowed_next']) == {'checking', 'assigned'}


def test_feedback_queue_template_escapes_content_and_has_workflow(session):
    feedback_id = FeedbackService(session).create(
        message='<script>alert(1)</script>',
        category='description_error',
        manager_name='<img src=x onerror=alert(1)>',
    )
    template_dir = Path(__file__).parents[2] / 'src' / 'app' / 'templates'
    environment = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(['html']),
    )

    html = environment.get_template('feedback.html').render(
        context=feedback_queue_context(session), auth_enabled=False
    )

    assert feedback_id[:8] in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert '<script>alert(1)</script>' not in html
    assert '&lt;img src=x onerror=alert(1)&gt;' in html
    assert "method:'PATCH'" in html
    assert 'Очередь проверки и подтверждения исправлений' in html


def _reconciliation(session, *, details=None):
    listing = Listing(
        id='reconciliation-listing',
        vehicle_signature='reconciliation-listing',
        source_auto_ru='https://auto.ru/cars/used/sale/mercedes/v/1132311022/',
        price_hint=10_000_000,
        is_active=True,
    )
    session.add(listing)
    session.flush()
    record = ListingReconciliation(
        id='reconciliation',
        batch_id='batch',
        listing_id=listing.id,
        source=EngineType.AUTO_RU,
        state='verified',
        url=listing.source_auto_ru,
        reason='fixture',
        details=details
        or {
            'direct_inspection': {
                'state': 'active',
                'status_code': 'active',
                'evidence': 'direct.png',
                'evidence_manifest': 'direct.png.json',
                'card': {'price': 11_000_000},
            }
        },
    )
    session.add(record)
    session.commit()
    return listing, record


def _actor_request(username='marketing', role='marketing'):
    return SimpleNamespace(
        state=SimpleNamespace(actor=AuthenticatedActor(username=username, role=role))
    )


def test_reconciliation_feedback_requires_current_finding_and_preserves_link(session, monkeypatch):
    _, record = _reconciliation(session)
    monkeypatch.setattr('app.api.settings.auth_enabled', True)

    result = create_reconciliation_feedback(
        record.id,
        OfferFindingFeedbackCreate(finding_code='price_mismatch', message='Проверить карточку'),
        _actor_request(),
        session,
    )

    ticket = session.get(ManagerFeedback, result['feedback_id'])
    assert (ticket.reconciliation_id, ticket.finding_code) == (record.id, 'price_mismatch')
    assert ticket.manager_name == 'marketing'
    assert ticket.category == 'price_error'

    with pytest.raises(HTTPException, match='finding is no longer current'):
        create_reconciliation_feedback(
            record.id,
            OfferFindingFeedbackCreate(finding_code='missing_offer', message='Не должно создаваться'),
            _actor_request(),
            session,
        )


def test_exception_report_keeps_fact_and_feedback_as_separate_layers(session):
    _, record = _reconciliation(session)
    ticket_id = FeedbackService(session).create(
        message='Цена передана в маркетинг',
        reconciliation_id=record.id,
        finding_code='price_mismatch',
        severity='high',
        category='price_error',
        manager_name='marketing',
    )

    report = offer_exception_report(session)
    item = next(row for row in report['exceptions'] if row['code'] == 'price_mismatch')
    assert item['open_feedback_count'] == 1
    assert item['feedback'][0]['id'] == ticket_id
    assert report['summary']['findings_with_feedback'] == 1


def test_feedback_queue_only_offers_transitions_allowed_for_role(session):
    feedback_id = FeedbackService(session).create(message='Проверить цену')
    marketing = feedback_queue_context(session, role='marketing')
    assert set(marketing['rows'][0]['allowed_next']) == {'checking', 'assigned'}

    FeedbackService(session).update_status(feedback_id, 'checking', actor='operator')
    director = feedback_queue_context(session, role='sales_director')
    assert director['rows'][0]['allowed_next'] == []
    FeedbackService(session).update_status(feedback_id, 'fixed', actor='operator')
    director = feedback_queue_context(session, role='sales_director')
    assert director['rows'][0]['allowed_next'] == ['confirmed']
