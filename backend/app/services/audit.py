"""The append-only activity log. Entries never contain secret values or provider raw output."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app import clock
from app.domain import AuditOutcome
from app.models import AuditEvent, User


def record(
    db: Session,
    *,
    organization_id: str,
    actor: User | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    resource_name: str | None = None,
    outcome: AuditOutcome = "success",
    request_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        organization_id=organization_id,
        occurred_at=clock.utcnow(),
        actor_id=actor.id if actor else None,
        actor_display_name=actor.display_name if actor else None,
        actor_email=actor.email if actor else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=(resource_name or None) and resource_name[:100],
        outcome=outcome,
        request_id=request_id,
    )
    db.add(event)
    return event
