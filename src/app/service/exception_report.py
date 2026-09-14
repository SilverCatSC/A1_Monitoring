"""Read-only operator report joining current Offer findings to feedback tickets."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.models import FeedbackStatus, ManagerFeedback
from app.service.offer_reconciliation import offer_review_queue


def offer_exception_report(db: Session) -> dict[str, Any]:
    """Return current M4 findings with their historical and open feedback links.

    The finding remains the source fact. A ticket documents a human workflow and
    never makes an outdated finding current again.
    """
    queue = offer_review_queue(db)
    tickets = (
        db.query(ManagerFeedback)
        .options(selectinload(ManagerFeedback.events))
        .filter(ManagerFeedback.reconciliation_id.is_not(None))
        .order_by(ManagerFeedback.created_at.desc(), ManagerFeedback.id)
        .all()
    )
    ticket_map: dict[tuple[str, str], list[ManagerFeedback]] = defaultdict(list)
    for ticket in tickets:
        if ticket.reconciliation_id and ticket.finding_code:
            ticket_map[(ticket.reconciliation_id, ticket.finding_code)].append(ticket)

    items: list[dict[str, Any]] = []
    for finding in queue['findings']:
        reconciliation_id = finding.get('reconciliation_id')
        key = (reconciliation_id, finding['code']) if reconciliation_id else None
        linked = ticket_map.get(key, []) if key else []
        items.append(
            {
                **finding,
                'feedback': [_ticket_summary(ticket) for ticket in linked],
                'open_feedback_count': sum(
                    ticket.status != FeedbackStatus.CONFIRMED for ticket in linked
                ),
            }
        )
    return {
        'schema_version': 1,
        'generated_at': queue['generated_at'],
        'summary': {
            **queue['summary'],
            'findings_with_feedback': sum(bool(item['feedback']) for item in items),
            'open_feedback': sum(item['open_feedback_count'] for item in items),
            'feedback_by_status': dict(Counter(ticket.status.value for ticket in tickets)),
        },
        'exceptions': items,
    }


def _ticket_summary(ticket: ManagerFeedback) -> dict[str, Any]:
    return {
        'id': ticket.id,
        'status': ticket.status.value,
        'severity': ticket.severity,
        'category': ticket.category,
        'manager_name': ticket.manager_name,
        'assignee': ticket.assignee,
        'created_at': ticket.created_at.isoformat() if ticket.created_at else None,
        'closed_at': ticket.closed_at.isoformat() if ticket.closed_at else None,
        'last_event': (
            {
                'to': latest.to_status,
                'actor': latest.actor,
                'created_at': latest.created_at.isoformat() if latest.created_at else None,
            }
            if (latest := max(ticket.events, key=lambda event: (event.created_at, event.id), default=None))
            else None
        ),
    }
