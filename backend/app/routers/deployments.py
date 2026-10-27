"""Deployments: list, start, read, events, logs, rollback and cancel."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import clock
from app.api.deps import Principal, authorize, get_db, get_notifier, get_principal, wake_worker
from app.api.responses import PageQuery, PageSizeQuery, check_paging, paginate, parse_list, parse_sort, respond
from app.api.serializers import deployment_out, deployment_summary, event_out
from app.domain import CANCELLABLE_DEPLOYMENT_STATUSES, DEPLOYMENT_STATUSES, PROVIDER_KINDS
from app.errors import conflict, not_found
from app.models import Application, ApplicationSpec, Deployment, DeploymentEvent, DeploymentLog
from app.routers.applications import find_app
from app.schemas import DeploymentEventOut, DeploymentOut, DeploymentSummaryOut, LogEntryOut, LogPageOut, Page
from app.services import audit, idempotency
from app.services.deployments import (
    add_event,
    announce,
    finish_unstarted,
    is_approvable,
    is_cancellable,
    is_rollback_target,
    start_deployment,
)
from app.worker.notify import Notifier

router = APIRouter(tags=["deployments"])
LOG_LIMIT = 500


def _find(db: Session, principal: Principal, deployment_id: str) -> Deployment:
    dep = db.scalar(
        select(Deployment).where(
            Deployment.id == deployment_id, Deployment.organization_id == principal.organization_id
        )
    )
    if dep is None:
        raise not_found("deployment")
    return dep


def _render(db: Session, dep: Deployment, principal: Principal) -> DeploymentOut:
    return deployment_out(dep, db.get(Application, dep.application_id), principal)


@router.get("/deployments", response_model=Page[DeploymentSummaryOut])
def list_deployments(
    page: PageQuery = 1,
    page_size: PageSizeQuery = 25,
    application_id: str | None = Query(default=None, max_length=64),
    status: str | None = None,
    provider: str | None = None,
    search: str | None = Query(default=None, max_length=200),
    sort: str | None = None,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> JSONResponse:
    check_paging(page, page_size)
    statuses = parse_list(status, DEPLOYMENT_STATUSES, "status", lower=True)
    providers = parse_list(provider, PROVIDER_KINDS, "provider")
    order = parse_sort(sort, ("created_at", "application_name", "status"))

    query = select(Deployment).where(Deployment.organization_id == principal.organization_id)
    if application_id:
        query = query.where(Deployment.application_id == application_id)
    if statuses:
        query = query.where(Deployment.status.in_(statuses))
    if providers:
        query = query.where(Deployment.provider.in_(providers))
    if search and search.strip():
        term = search.strip()
        query = query.where(
            or_(
                Deployment.application_name.icontains(term, autoescape=True),
                Deployment.id.istartswith(term, autoescape=True),
                Deployment.version.icontains(term, autoescape=True),
                Deployment.source_ref.icontains(term, autoescape=True),
            )
        )
    if order is None:
        query = query.order_by(Deployment.created_at.desc(), Deployment.id)
    else:
        key, descending = order
        column = {
            "created_at": Deployment.created_at,
            "application_name": Deployment.application_name,
            "status": Deployment.status,
        }[key]
        query = query.order_by(
            column.desc() if descending else column.asc(), Deployment.created_at.desc(), Deployment.id
        )
    rows, total = paginate(db, query, page, page_size)
    return respond(
        Page[DeploymentSummaryOut](
            items=[deployment_summary(d) for d in rows], total=total, page=page, page_size=page_size
        )
    )


@router.get("/apps/{application_id}/deployments", response_model=Page[DeploymentSummaryOut])
def list_application_deployments(
    application_id: str,
    page: PageQuery = 1,
    page_size: PageSizeQuery = 25,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> JSONResponse:
    check_paging(page, page_size)
    app = find_app(db, principal, application_id)
    query = (
        select(Deployment)
        .where(Deployment.organization_id == principal.organization_id, Deployment.application_id == app.id)
        .order_by(Deployment.created_at.desc(), Deployment.id)
    )
    rows, total = paginate(db, query, page, page_size)
    return respond(
        Page[DeploymentSummaryOut](
            items=[deployment_summary(d) for d in rows], total=total, page=page, page_size=page_size
        )
    )


@router.post("/apps/{application_id}/deployments", status_code=202, response_model=DeploymentOut)
def create_deployment(
    application_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    notifier: Notifier = Depends(get_notifier),
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    authorize(
        db,
        principal,
        "deployments:create",
        action="deployment.created",
        resource_type="deployment",
        resource_name=app.name,
    )
    ctx = idempotency.context(
        request,
        scope=f"POST /apps/{app.id}/deployments",
        payload={},
        organization_id=principal.organization_id,
        user_id=principal.user.id,
    )
    if (record := idempotency.lookup(db, ctx)) is not None:
        return respond(_render(db, _find(db, principal, record.resource_id), principal), record.status_code)
    spec = db.get(ApplicationSpec, app.id)
    if spec is None:
        raise not_found("application configuration")
    trigger = "api" if principal.session is None else "manual"
    dep = start_deployment(db, app=app, spec=spec, kind="deploy", target=None, actor=principal.user, trigger=trigger)
    announce(db, notifier, dep, app)
    idempotency.remember(db, ctx, status_code=202, resource_type="deployment", resource_id=dep.id)
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action="deployment.created",
        resource_type="deployment",
        resource_id=dep.id,
        resource_name=app.name,
        request_id=principal.request_id,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if (record := idempotency.lookup(db, ctx)) is not None:
            return respond(_render(db, _find(db, principal, record.resource_id), principal), record.status_code)
        raise idempotency.in_flight_conflict() from None
    wake_worker(request)
    return respond(_render(db, dep, principal), 202)


@router.get("/deployments/{deployment_id}", response_model=DeploymentOut)
def get_deployment(
    deployment_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    return respond(_render(db, _find(db, principal, deployment_id), principal))


@router.get("/deployments/{deployment_id}/events", response_model=list[DeploymentEventOut])
def list_events(
    deployment_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    dep = _find(db, principal, deployment_id)
    rows = db.scalars(
        select(DeploymentEvent)
        .where(DeploymentEvent.deployment_id == dep.id)
        .order_by(DeploymentEvent.timestamp, DeploymentEvent.id)
    ).all()
    return respond([event_out(e) for e in rows])


@router.get("/deployments/{deployment_id}/logs", response_model=LogPageOut)
def get_logs(
    deployment_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    dep = _find(db, principal, deployment_id)
    total = db.scalar(select(func.count()).select_from(DeploymentLog).where(DeploymentLog.deployment_id == dep.id)) or 0
    rows = db.scalars(
        select(DeploymentLog)
        .where(DeploymentLog.deployment_id == dep.id)
        .order_by(DeploymentLog.id.desc())
        .limit(LOG_LIMIT)
    ).all()
    entries = [LogEntryOut(timestamp=r.timestamp, stream=r.stream, message=r.message) for r in reversed(rows)]
    return respond(LogPageOut(entries=entries, truncated=total > LOG_LIMIT))


@router.post("/deployments/{deployment_id}/rollback", status_code=202, response_model=DeploymentOut)
def rollback(
    deployment_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> JSONResponse:
    target = _find(db, principal, deployment_id)
    authorize(
        db,
        principal,
        "deployments:rollback",
        action="deployment.rollback_started",
        resource_type="deployment",
        resource_id=target.id,
        resource_name=target.application_name,
    )
    ctx = idempotency.context(
        request,
        scope=f"POST /deployments/{target.id}/rollback",
        payload={},
        organization_id=principal.organization_id,
        user_id=principal.user.id,
    )
    if (record := idempotency.lookup(db, ctx)) is not None:
        return respond(_render(db, _find(db, principal, record.resource_id), principal), record.status_code)
    app = db.scalar(
        select(Application).where(
            Application.id == target.application_id,
            Application.organization_id == principal.organization_id,
            Application.deleted_at.is_(None),
        )
    )
    if app is None or not is_rollback_target(target, app):
        if app is not None and app.active_deployment_id is not None:
            raise conflict("deployment_in_progress", "A deployment for this application is already in progress.")
        raise conflict("rollback_not_permitted", "This deployment cannot be used as a rollback target.")
    dep = start_deployment(db, app=app, spec=None, kind="rollback", target=target, actor=principal.user)
    idempotency.remember(db, ctx, status_code=202, resource_type="deployment", resource_id=dep.id)
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action="deployment.rollback_started",
        resource_type="deployment",
        resource_id=dep.id,
        resource_name=app.name,
        request_id=principal.request_id,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if (record := idempotency.lookup(db, ctx)) is not None:
            return respond(_render(db, _find(db, principal, record.resource_id), principal), record.status_code)
        raise idempotency.in_flight_conflict() from None
    wake_worker(request)
    return respond(_render(db, dep, principal), 202)


@router.post("/deployments/{deployment_id}/cancel", status_code=202, response_model=DeploymentOut)
def cancel(
    deployment_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> JSONResponse:
    dep = _find(db, principal, deployment_id)
    authorize(
        db,
        principal,
        "deployments:cancel",
        action="deployment.cancel_requested",
        resource_type="deployment",
        resource_id=dep.id,
        resource_name=dep.application_name,
    )
    if not is_cancellable(dep):
        raise conflict("not_cancellable", "This deployment can no longer be cancelled.")
    if dep.status == "awaiting_approval":
        # The worker never started it, so it ends right away.
        app = db.get(Application, dep.application_id)
        finish_unstarted(db, dep, app, code="cancelled", message=f"Cancelled by {principal.user.display_name}.")
        _audit_decision(db, principal, dep, "deployment.cancelled")
        db.commit()
        return respond(_render(db, dep, principal), 202)
    now = clock.utcnow()
    # Conditional, so a deployment that finished or moved on in the meantime is not marked.
    claimed = db.execute(
        update(Deployment)
        .where(
            Deployment.id == dep.id,
            Deployment.kind == "deploy",
            Deployment.status.in_(CANCELLABLE_DEPLOYMENT_STATUSES),
            Deployment.cancel_requested_at.is_(None),
        )
        .values(cancel_requested_at=now, next_attempt_at=None)
        .execution_options(synchronize_session=False)
    )
    if getattr(claimed, "rowcount", 0) != 1:
        db.rollback()
        raise conflict("not_cancellable", "This deployment can no longer be cancelled.")
    dep.cancel_requested_at = now
    dep.next_attempt_at = None
    add_event(db, dep, f"Cancellation requested by {principal.user.display_name}.", level="warning")
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action="deployment.cancel_requested",
        resource_type="deployment",
        resource_id=dep.id,
        resource_name=dep.application_name,
        request_id=principal.request_id,
    )
    db.commit()
    wake_worker(request)
    return respond(_render(db, dep, principal), 202)


def _audit_decision(db: Session, principal: Principal, dep: Deployment, action: str) -> None:
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action=action,
        resource_type="deployment",
        resource_id=dep.id,
        resource_name=dep.application_name,
        request_id=principal.request_id,
    )


def _decide(db: Session, principal: Principal, deployment_id: str, action: str) -> Deployment:
    dep = _find(db, principal, deployment_id)
    authorize(
        db,
        principal,
        "deployments:approve",
        action=action,
        resource_type="deployment",
        resource_id=dep.id,
        resource_name=dep.application_name,
    )
    if not is_approvable(dep):
        raise conflict("not_awaiting_approval", "This deployment is not waiting for approval.")
    return dep


@router.post("/deployments/{deployment_id}/approve", status_code=202, response_model=DeploymentOut)
def approve(
    deployment_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> JSONResponse:
    dep = _decide(db, principal, deployment_id, "deployment.approved")
    # Conditional, so two approvers (or an approve racing a cancel) cannot both act.
    claimed = db.execute(
        update(Deployment)
        .where(Deployment.id == dep.id, Deployment.status == "awaiting_approval")
        .values(status="queued", approved_by=principal.user.display_name, approved_at=clock.utcnow())
        .execution_options(synchronize_session=False)
    )
    if getattr(claimed, "rowcount", 0) != 1:
        db.rollback()
        raise conflict("not_awaiting_approval", "This deployment is not waiting for approval.")
    db.refresh(dep)
    add_event(db, dep, f"Approved by {principal.user.display_name}.", stage="queued")
    _audit_decision(db, principal, dep, "deployment.approved")
    db.commit()
    wake_worker(request)
    return respond(_render(db, dep, principal), 202)


@router.post("/deployments/{deployment_id}/reject", response_model=DeploymentOut)
def reject(
    deployment_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> JSONResponse:
    dep = _decide(db, principal, deployment_id, "deployment.rejected")
    app = db.get(Application, dep.application_id)
    finish_unstarted(db, dep, app, code="rejected", message=f"Rejected by {principal.user.display_name}.")
    _audit_decision(db, principal, dep, "deployment.rejected")
    db.commit()
    return respond(_render(db, dep, principal))
