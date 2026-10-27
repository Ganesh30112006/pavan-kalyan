"""Connection validation, application deletion, health monitoring, sleep schedules and custom domains."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import clock
from app.config import Settings
from app.models import (
    Application,
    ApplicationSpec,
    CustomDomain,
    Deployment,
    ProviderConnection,
    User,
    UserSession,
    WebhookDelivery,
)
from app.providers.base import CheckResult, ProviderError, ProviderRegistry
from app.services import apps, audit, idempotency, schedules
from app.services.deployments import start_deployment
from app.services.targets import app_target, connection_info
from app.worker import http_check
from app.worker.notify import Notifier

logger = logging.getLogger(__name__)
MAX_DELETE_ATTEMPTS = 10


def _checks(checks: list[CheckResult]) -> list[dict[str, Any]]:
    return [{"name": c.name, "status": c.status, "detail": c.detail} for c in checks]


def validate_connection(
    db: Session, conn: ProviderConnection, settings: Settings, registry: ProviderRegistry, notifier: Notifier
) -> None:
    started = dict(conn.validation or {}).get("started_at") or clock.iso(clock.utcnow())
    started_at = dt.datetime.fromisoformat(str(started).replace("Z", "+00:00"))
    actor = db.get(User, conn.validation_requested_by_id) if conn.validation_requested_by_id else None

    if clock.utcnow() - started_at > dt.timedelta(seconds=settings.connection_validation_timeout_seconds):
        # A previous attempt was interrupted (for example by a restart) and never finished.
        _finish(
            db,
            conn,
            "timed_out",
            "timed_out",
            list(dict(conn.validation or {}).get("checks", [])),
            started,
            actor,
            notifier,
        )
        return

    def progress(stage: str, checks: list[CheckResult]) -> None:
        conn.validation = {
            "stage": stage,
            "started_at": started,
            "finished_at": None,
            "checks": _checks(checks),
            "error_code": None,
        }
        db.commit()

    try:
        result = registry.get(conn.provider).validate_connection(connection_info(conn, conn.default_region), progress)
        status, error_code, checks = result.status, result.error_code, _checks(result.checks)
    except ProviderError as error:
        status, error_code = error.connection_status, error.code
        checks = list(dict(conn.validation or {}).get("checks", []))
    _finish(db, conn, status, error_code, checks, started, actor, notifier)


def _finish(
    db: Session,
    conn: ProviderConnection,
    status: str,
    error_code: str | None,
    checks: list[dict[str, Any]],
    started: str,
    actor: User | None,
    notifier: Notifier,
) -> None:
    now = clock.utcnow()
    conn.status = status
    conn.last_validated_at = now
    conn.validation = {
        "stage": None,
        "started_at": started,
        "finished_at": clock.iso(now),
        "checks": checks,
        "error_code": error_code,
    }
    conn.locked_until = None
    audit.record(
        db,
        organization_id=conn.organization_id,
        actor=actor,
        action="provider_connection.validated",
        resource_type="provider_connection",
        resource_id=conn.id,
        resource_name=conn.name,
        outcome="success" if status == "connected" else "failure",
    )
    if status != "connected":
        notifier.notify(
            db,
            conn.organization_id,
            "provider_connection_failed",
            f"Cloud connection {conn.name} failed validation",
            f"CloudBridge could not validate the cloud connection {conn.name} ({status.replace('_', ' ')}).",
        )


def delete_application(db: Session, app: Application, registry: ProviderRegistry, notifier: Notifier) -> float | None:
    """One step of deleting an application's provider resources. Returns seconds until the next step."""
    conn = db.get(ProviderConnection, app.connection_id)
    actor = db.get(User, app.delete_requested_by_id) if app.delete_requested_by_id else None
    target = app_target(app)
    if conn is None:
        _give_up(db, app, actor, "The application's cloud connection no longer exists.")
        return None
    info = connection_info(conn, app.region)
    adapter = registry.get(app.provider)
    try:
        handle = dict(app.delete_state or {})
        if not handle:
            handle = adapter.start_delete(info, target)
            app.delete_state = handle
            return 0.0 if handle.get("done") else 10.0
        status = adapter.poll_delete(info, handle)
        app.delete_state = handle
    except ProviderError as error:
        app.delete_attempts += 1
        if error.retryable and app.delete_attempts < MAX_DELETE_ATTEMPTS:
            return min(300.0, 10.0 * float(2**app.delete_attempts))
        _give_up(db, app, actor, error.message)
        return None
    if status.state == "running":
        return 10.0
    if status.state == "failed":
        _give_up(db, app, actor, status.message or "The provider could not delete the service.")
        return None
    now = clock.utcnow()
    app.deleted_at = now
    app.updated_at = now
    app.provider_service_id = None
    app.locked_until = None
    apps.forget_domains(db, app)
    # Previews of a deleted application go with it.
    for preview in db.scalars(
        select(Application).where(Application.preview_of_id == app.id, Application.deleted_at.is_(None))
    ).all():
        if preview.active_deployment_id is None:
            apps.request_delete(db, preview, actor)
    audit.record(
        db,
        organization_id=app.organization_id,
        actor=actor,
        action="application.deleted",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
    )
    notifier.notify(
        db,
        app.organization_id,
        "application_deleted",
        f"{app.name} was deleted",
        f"The application {app.name} and its service on the provider were deleted.",
        application=app,
    )
    return None


def _give_up(db: Session, app: Application, actor: User | None, message: str) -> None:
    logger.warning("Application deletion failed: %s", message, extra={"application_id": app.id})
    app.delete_requested_at = None
    app.delete_state = None
    app.delete_attempts = 0
    app.status = "running" if app.current_version else "failed"
    app.updated_at = clock.utcnow()
    audit.record(
        db,
        organization_id=app.organization_id,
        actor=actor,
        action="application.deleted",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
        outcome="failure",
    )


def check_health(
    db: Session,
    app: Application,
    registry: ProviderRegistry,
    http_client_factory: Any = None,
    notifier: Notifier | None = None,
) -> None:
    before = app.health
    _check_health(db, app, registry, http_client_factory)
    if notifier is not None and app.health == "unhealthy" and before != "unhealthy":
        notifier.notify(
            db,
            app.organization_id,
            "application_unhealthy",
            f"{app.name} is unhealthy",
            f"CloudBridge's health check for {app.name} (version {app.current_version or 'unknown'}) is failing.",
            application=app,
        )


def _check_health(db: Session, app: Application, registry: ProviderRegistry, http_client_factory: Any) -> None:
    conn = db.get(ProviderConnection, app.connection_id)
    app.health_checked_at = clock.utcnow()
    if conn is None or conn.status != "connected":
        app.health = "unknown"
        return
    adapter = registry.get(app.provider)
    target = app_target(app)
    try:
        service = adapter.check_service(connection_info(conn, app.region), target)
    except ProviderError:
        app.health = "unknown"
        return
    if service.running is None:
        return
    if not service.running:
        app.health = "unhealthy"
        return
    # Check what is deployed, not configuration edits that have not been deployed yet.
    current = db.get(Deployment, app.current_deployment_id) if app.current_deployment_id else None
    spec = db.get(ApplicationSpec, app.id)
    deployed: dict[str, Any] = (current.spec_snapshot if current else None) or (spec.effective if spec else None) or {}
    if deployed.get("exposure", "public") == "private" or not app.service_url:
        app.health = "healthy"
        return
    path = deployed.get("health_check_path")
    if app.application_type == "worker":
        app.health = "healthy"
        return
    result = http_check.check(
        app.service_url, path, adapter.service_host_allowed, http_client_factory, allow_http=adapter.plain_http_allowed
    )
    if result.outcome == "passed":
        slow = result.seconds is not None and result.seconds > http_check.SLOW_SECONDS
        app.health = "degraded" if slow else "healthy"
    elif result.outcome == "failed":
        app.health = "degraded"
    else:
        app.health = "unhealthy"


def run_pending(db: Session, app: Application) -> None:
    """Starts what a webhook could not start while a deployment was running: a pushed commit, or deleting
    a closed pull request's preview."""
    pending = dict(app.pending_auto_deploy or {})
    app.pending_auto_deploy = None
    if not pending:
        return
    if pending.get("delete"):
        apps.request_delete(db, app, None)
        return
    spec = db.get(ApplicationSpec, app.id)
    if spec is None or app.paused or not pending.get("source"):
        return
    start_deployment(
        db,
        app=app,
        spec=spec,
        kind="deploy",
        target=None,
        actor=None,
        actor_label=str(pending.get("actor") or "GitHub"),
        trigger=str(pending.get("trigger") or "push"),
        source_override=dict(pending["source"]),
        source_ref_label=pending.get("label"),
    )


def apply_schedule(db: Session, app: Application, registry: ProviderRegistry, now: dt.datetime) -> None:
    """Pauses an application when its sleep window starts and resumes it when the window ends. Only pauses
    the schedule made are undone by it; a manual pause stays until someone resumes the application."""
    schedule = schedules.Schedule.from_json(app.sleep_schedule)
    if schedule is None:
        return
    asleep = schedule.asleep_at(now)
    if asleep == app.paused or (not asleep and not app.paused_by_schedule):
        return
    conn = db.get(ProviderConnection, app.connection_id)
    if conn is None or conn.status != "connected":
        return
    spec = db.get(ApplicationSpec, app.id)
    try:
        registry.get(app.provider).set_paused(connection_info(conn, app.region), app_target(app, spec), asleep)
    except ProviderError as error:
        logger.info(
            "Scheduled %s failed: %s", "pause" if asleep else "resume", error.code, extra={"application_id": app.id}
        )
        return
    pause_state(app, asleep, by_schedule=True)
    audit.record(
        db,
        organization_id=app.organization_id,
        actor=None,
        action="application.paused" if asleep else "application.resumed",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
    )


def pause_state(app: Application, paused: bool, *, by_schedule: bool) -> None:
    app.paused = paused
    app.paused_by_schedule = paused and by_schedule
    app.status = "stopped" if paused else "running"
    app.health = "unknown"
    app.health_checked_at = None
    app.updated_at = clock.utcnow()


def refresh_domain(db: Session, domain: CustomDomain, registry: ProviderRegistry) -> bool:
    """Advances a custom domain: re-reads its provider status, or removes it when removal was requested.
    Returns True when the domain was removed."""
    app = db.get(Application, domain.application_id)
    conn = db.get(ProviderConnection, app.connection_id) if app is not None else None
    domain.checked_at = clock.utcnow()
    if app is None or conn is None:
        if domain.status == "removing":
            db.delete(domain)
            return True
        return False
    adapter = registry.get(app.provider)
    info = connection_info(conn, app.region)
    try:
        if domain.status == "removing":
            adapter.remove_domain(info, app_target(app), domain.domain)
            db.delete(domain)
            return True
        state = adapter.domain_status(info, app_target(app), domain.domain)
    except ProviderError as error:
        if not error.retryable:
            domain.message = error.message
        return False
    domain.status = state.status
    domain.records = [dict(r.__dict__) for r in state.records]
    domain.message = state.message
    return False


def housekeeping(db: Session) -> None:
    idempotency.purge_expired(db)
    db.execute(delete(WebhookDelivery).where(WebhookDelivery.received_at <= clock.utcnow() - dt.timedelta(days=7)))
    db.execute(delete(UserSession).where(UserSession.expires_at <= clock.utcnow()))
