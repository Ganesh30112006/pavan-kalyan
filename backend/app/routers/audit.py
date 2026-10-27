"""The activity log (requires audit:read)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import Principal, authorize, get_db, get_principal
from app.api.responses import PageQuery, PageSizeQuery, check_paging, paginate, parse_list, respond
from app.api.serializers import audit_out
from app.clock import as_utc
from app.domain import AUDIT_OUTCOMES
from app.errors import FieldError, invalid
from app.models import AuditEvent
from app.schemas import AuditEventOut, Page

router = APIRouter(tags=["activity"])


def _instant(value: str | None, field: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        return as_utc(dt.datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        raise invalid([FieldError(field, "Use an ISO 8601 date and time.")], "A date filter is not valid.") from None


@router.get("/audit-events", response_model=Page[AuditEventOut])
def list_audit_events(
    page: PageQuery = 1,
    page_size: PageSizeQuery = 25,
    search: str | None = Query(default=None, max_length=200),
    actor: str | None = Query(default=None, max_length=200),
    action: str | None = Query(default=None, max_length=64),
    outcome: str | None = None,
    resource_type: str | None = Query(default=None, max_length=32),
    from_: str | None = Query(
        default=None, alias="from", max_length=40, description="ISO 8601 date and time; empty means no limit"
    ),
    to: str | None = Query(default=None, max_length=40, description="ISO 8601 date and time; empty means no limit"),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> JSONResponse:
    authorize(db, principal, "audit:read", action="audit.read", resource_type="organization")
    check_paging(page, page_size)
    outcomes = parse_list(outcome, AUDIT_OUTCOMES, "outcome")
    start, end = _instant(from_, "from"), _instant(to, "to")

    query = select(AuditEvent).where(AuditEvent.organization_id == principal.organization_id)
    if search and search.strip():
        term = search.strip()
        query = query.where(
            or_(
                AuditEvent.action.icontains(term, autoescape=True),
                AuditEvent.resource_name.icontains(term, autoescape=True),
                AuditEvent.actor_display_name.icontains(term, autoescape=True),
                AuditEvent.resource_id.istartswith(term, autoescape=True),
            )
        )
    if actor and actor.strip():
        term = actor.strip()
        query = query.where(
            or_(
                AuditEvent.actor_display_name.icontains(term, autoescape=True),
                AuditEvent.actor_email.icontains(term, autoescape=True),
            )
        )
    if action:
        query = query.where(AuditEvent.action == action)
    if outcomes:
        query = query.where(AuditEvent.outcome.in_(outcomes))
    if resource_type:
        query = query.where(AuditEvent.resource_type == resource_type)
    if start:
        query = query.where(AuditEvent.occurred_at >= start)
    if end:
        query = query.where(AuditEvent.occurred_at <= end)
    query = query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id)
    rows, total = paginate(db, query, page, page_size)
    return respond(Page[AuditEventOut](items=[audit_out(e) for e in rows], total=total, page=page, page_size=page_size))
