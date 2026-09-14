"""Role policy for human workflows; browser monitoring never receives a human role."""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.config import settings
from app.models import FeedbackStatus
from app.security import AuthenticatedActor

ROLE_LABELS = {
    'admin': 'Администратор',
    'operator': 'Оператор мониторинга',
    'marketing': 'Маркетинг',
    'sales_director': 'РОП',
}


def current_actor(request: Request) -> AuthenticatedActor:
    actor = getattr(request.state, 'actor', None)
    if isinstance(actor, AuthenticatedActor):
        return actor
    if not settings.auth_enabled:
        return AuthenticatedActor(username='stage', role='admin')
    raise HTTPException(status_code=401, detail='authentication required')


def require_roles(request: Request, *roles: str) -> AuthenticatedActor:
    actor = current_actor(request)
    if actor.role not in roles:
        raise HTTPException(status_code=403, detail='your role cannot perform this operation')
    return actor


def can_create_feedback(role: str) -> bool:
    return role in {'admin', 'operator', 'marketing', 'sales_director'}


def can_transition_feedback(
    *, role: str, current: FeedbackStatus, target: FeedbackStatus
) -> bool:
    if role in {'admin', 'operator'}:
        return True
    if role == 'marketing':
        return target in {FeedbackStatus.CHECKING, FeedbackStatus.ASSIGNED, FeedbackStatus.FIXED}
    if role == 'sales_director':
        return current == FeedbackStatus.FIXED and target == FeedbackStatus.CONFIRMED
    return False
