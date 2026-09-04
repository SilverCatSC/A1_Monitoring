from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import FeedbackStatus, ManagerFeedback


class FeedbackService:
    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        message: str,
        listing_id: str | None = None,
        filter_id: str | None = None,
        severity: str = 'medium',
        manager_name: str | None = None,
        source: str | None = None,
    ) -> str:
        record = ManagerFeedback(
            listing_id=listing_id,
            filter_id=filter_id,
            severity=severity,
            message=message,
            source=source,
            manager_name=manager_name,
            status=FeedbackStatus.NEW,
        )
        self.db.add(record)
        self.db.commit()
        return record.id

    def update_status(self, feedback_id: str, status: str, message: Optional[str] = None) -> None:
        item = self.db.query(ManagerFeedback).filter(ManagerFeedback.id == feedback_id).one_or_none()
        if item is None:
            raise ValueError('feedback not found')
        item.status = FeedbackStatus(status)
        if message:
            item.message = item.message + '\n\n' + message
        if status == FeedbackStatus.CONFIRMED:
            item.closed_at = datetime.utcnow()
        self.db.commit()
