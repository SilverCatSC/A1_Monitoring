from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import (
    FeedbackEvent,
    FeedbackStatus,
    Listing,
    ListingObservation,
    ManagerFeedback,
    SearchFilter,
)


class FeedbackValidationError(ValueError):
    pass


ALLOWED_TRANSITIONS = {
    FeedbackStatus.NEW: {FeedbackStatus.CHECKING, FeedbackStatus.ASSIGNED},
    FeedbackStatus.CHECKING: {FeedbackStatus.ASSIGNED, FeedbackStatus.FIXED},
    FeedbackStatus.ASSIGNED: {FeedbackStatus.CHECKING, FeedbackStatus.FIXED},
    FeedbackStatus.FIXED: {FeedbackStatus.CHECKING, FeedbackStatus.CONFIRMED},
    FeedbackStatus.CONFIRMED: set(),
}
ALLOWED_SEVERITIES = {'low', 'medium', 'high', 'critical'}
ALLOWED_CATEGORIES = {
    'data_error',
    'photo_error',
    'price_error',
    'description_error',
    'visibility_error',
    'other',
}


class FeedbackService:
    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        message: str,
        listing_id: str | None = None,
        filter_id: str | None = None,
        observed_id: str | None = None,
        severity: str = 'medium',
        category: str = 'other',
        manager_name: str | None = None,
        source: str | None = None,
    ) -> str:
        clean_message = message.strip()
        if not clean_message:
            raise FeedbackValidationError('message is required')
        if severity not in ALLOWED_SEVERITIES:
            raise FeedbackValidationError('unsupported severity')
        if category not in ALLOWED_CATEGORIES:
            raise FeedbackValidationError('unsupported category')
        observation = None
        if observed_id:
            observation = (
                self.db.query(ListingObservation)
                .filter(ListingObservation.id == observed_id)
                .one_or_none()
            )
            if observation is None:
                raise FeedbackValidationError('observation not found')
            if listing_id and listing_id != observation.listing_id:
                raise FeedbackValidationError('listing does not match observation')
            if filter_id and filter_id != observation.filter_id:
                raise FeedbackValidationError('filter does not match observation')
            listing_id = observation.listing_id
            filter_id = observation.filter_id
        if listing_id and not self.db.query(Listing).filter(Listing.id == listing_id).first():
            raise FeedbackValidationError('listing not found')
        if filter_id and not self.db.query(SearchFilter).filter(SearchFilter.id == filter_id).first():
            raise FeedbackValidationError('filter not found')

        record = ManagerFeedback(
            listing_id=listing_id,
            filter_id=filter_id,
            run_id=observation.run_id if observation else None,
            observed_id=observed_id,
            severity=severity,
            category=category,
            message=clean_message,
            source=source,
            manager_name=manager_name,
            status=FeedbackStatus.NEW,
        )
        self.db.add(record)
        self.db.flush()
        self.db.add(
            FeedbackEvent(
                feedback_id=record.id,
                from_status=None,
                to_status=FeedbackStatus.NEW.value,
                actor=manager_name,
                note=clean_message,
            )
        )
        self.db.commit()
        return record.id

    def update_status(
        self,
        feedback_id: str,
        status: str,
        *,
        actor: str,
        note: str | None = None,
        assignee: str | None = None,
    ) -> ManagerFeedback:
        item = self.db.query(ManagerFeedback).filter(ManagerFeedback.id == feedback_id).one_or_none()
        if item is None:
            raise FeedbackValidationError('feedback not found')
        try:
            target = FeedbackStatus(status)
        except ValueError as exc:
            raise FeedbackValidationError('unsupported feedback status') from exc
        if target not in ALLOWED_TRANSITIONS[item.status]:
            raise FeedbackValidationError(
                f'transition {item.status.value} -> {target.value} is not allowed'
            )
        clean_actor = actor.strip()
        if not clean_actor:
            raise FeedbackValidationError('actor is required')

        previous = item.status
        item.status = target
        if assignee is not None:
            item.assignee = assignee.strip() or None
        if target == FeedbackStatus.CONFIRMED:
            item.closed_at = datetime.now(UTC)
        elif item.closed_at is not None:
            item.closed_at = None
        self.db.add(
            FeedbackEvent(
                feedback_id=item.id,
                from_status=previous.value,
                to_status=target.value,
                actor=clean_actor,
                note=(note or '').strip() or None,
            )
        )
        self.db.commit()
        return item
