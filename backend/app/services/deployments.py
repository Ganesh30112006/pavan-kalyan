"""Starting deployments and deciding which actions a deployment allows."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from sqlalchemy import update
from sqlalchemy.orm import Session

from app import clock
from app.clock import new_id
from app.domain import CANCELLABLE_DEPLOYMENT_STATUSES, STAGE_NAMES
from app.errors import conflict
from app.models import Application, ApplicationSpec, Deployment, DeploymentEvent, DeploymentLog, User
from app.providers.images import parse_image


def spec_snapshot(spec: ApplicationSpec) -> dict[str, Any]:
    return {
        "version": spec.version,
        "port": spec.port,
        "cpu": spec.cpu,
        "memory": spec.memory,
        "min_instances": spec.min_instances,
        "max_instances": spec.max_instances,
        "concurrency": spec.concurrency,
        "exposure": spec.exposure,
        "health_check_path": spec.health_check_path,
        "environment": [dict(e) for e in spec.environment],
        "secret_refs": [dict(s) for s in spec.secret_refs],
    }


def source_snapshot(app: Application) -> dict[str, Any]:
    return {
        "type": app.source_type,
        "image": app.source_image,
        "repository_url": app.repository_url,
        "ref_type": app.ref_type,
        "git_ref": app.git_ref,
        "dockerfile_path": app.dockerfile_path,
        "build_context": app.build_context,
        "config_file": bool(app.config_from_repo and app.source_type == "repository"),
    }


def initial_stages(now: dt.datetime) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "status": "active" if name == "queued" else "pending",
            "started_at": clock.iso(now) if name == "queued" else None,
            "finished_at": None,
        }
        for name in STAGE_NAMES
    ]


def source_ref(source: dict[str, Any]) -> str | None:
    if source.get("type") == "image" and source.get("image"):
        ref = parse_image(str(source["image"]))
        name = ref.path.rsplit("/", 1)[-1]
        if ref.tag:
            return f"{name}:{ref.tag}"
        return f"{name}@{ref.digest[:19]}" if ref.digest else name
    if source.get("git_ref"):
        git_ref = str(source["git_ref"])
        return git_ref[:12] if source.get("ref_type") == "commit" else git_ref
    return None


def is_rollback_target(dep: Deployment, app: Application) -> bool:
    """A revision that served traffic successfully and is not serving now can be restored."""
    restorable = dep.status == "succeeded" or (dep.kind == "rollback" and dep.status == "rolled_back")
    return (
        restorable
        and dep.image_digest is not None
        and dep.image_uri is not None
        and bool(dep.spec_snapshot)
        and app.current_deployment_id != dep.id
        and app.active_deployment_id is None
        and app.deleted_at is None
        and app.delete_requested_at is None
    )


def is_cancellable(dep: Deployment) -> bool:
    return dep.kind == "deploy" and dep.status in CANCELLABLE_DEPLOYMENT_STATUSES and dep.cancel_requested_at is None


def is_approvable(dep: Deployment) -> bool:
    return dep.status == "awaiting_approval"


def needs_approval(app: Application, kind: str) -> bool:
    """Production deployments wait for an approver when the application asks for it. Rollbacks restore a
    version that was already approved and live, so they never wait."""
    return kind == "deploy" and app.requires_approval and app.environment == "production"


def announce(db: Session, notifier: Any, dep: Deployment, app: Application) -> None:
    """Tells approvers when a deployment waits for them."""
    if dep.status != "awaiting_approval":
        return
    notifier.notify(
        db,
        app.organization_id,
        "approval_requested",
        f"{app.name}: version {dep.version} is waiting for approval",
        f"{dep.initiated_by or 'Someone'} started version {dep.version} of {app.name} "
        f"({dep.source_ref or 'saved source'}). An owner or admin must approve it before it deploys.",
        application=app,
    )


def finish_unstarted(db: Session, dep: Deployment, app: Application | None, *, code: str, message: str) -> None:
    """Ends a deployment the worker never started (rejected, or cancelled while awaiting approval)."""
    now = clock.utcnow()
    dep.status = "cancelled"
    dep.finished_at = now
    dep.failure_code = code
    dep.failure_message = message
    dep.next_attempt_at = None
    dep.stages = [
        dict(s, status="cancelled", finished_at=clock.iso(now)) if s["status"] in ("active", "pending") else dict(s)
        for s in dep.stages
    ]
    add_event(db, dep, message, level="warning", stage="queued")
    if app is not None and app.active_deployment_id == dep.id:
        app.active_deployment_id = None
        app.status = "running" if app.current_version else "draft"
        app.updated_at = now


def add_event(db: Session, dep: Deployment, message: str, *, level: str = "info", stage: str | None = None) -> None:
    db.add(DeploymentEvent(deployment_id=dep.id, timestamp=clock.utcnow(), level=level, stage=stage, message=message))
    db.add(
        DeploymentLog(
            deployment_id=dep.id,
            timestamp=clock.utcnow(),
            stream="stderr" if level == "error" else "system",
            message=message,
        )
    )


def start_deployment(
    db: Session,
    *,
    app: Application,
    spec: ApplicationSpec | None,
    kind: Literal["deploy", "rollback"],
    target: Deployment | None,
    actor: User | None,
    actor_label: str | None = None,
    trigger: str = "manual",
    source_override: dict[str, Any] | None = None,
    source_ref_label: str | None = None,
) -> Deployment:
    """Creates a queued deployment (or one awaiting approval). Claims the application atomically, so two
    concurrent requests cannot both start one (the loser gets 409).

    `actor` is None for deployments CloudBridge starts itself (a GitHub push); `actor_label` then says who
    or what caused it. `source_override` deploys a different source than the application's saved one: a
    pushed commit, or a pinned image being promoted from another environment.
    """
    if app.paused:
        raise conflict("application_paused", "This application is paused. Resume it before deploying.")
    now = clock.utcnow()
    awaiting = needs_approval(app, kind)
    dep = Deployment(
        id=new_id(),
        organization_id=app.organization_id,
        application_id=app.id,
        application_name=app.name,
        provider=app.provider,
        region=app.region,
        kind=kind,
        status="rolling_back" if kind == "rollback" else "awaiting_approval" if awaiting else "queued",
        initiated_by=actor.display_name if actor is not None else actor_label,
        initiated_by_id=actor.id if actor is not None else None,
        trigger=trigger,
        created_at=now,
        stages=initial_stages(now),
        state={},
    )
    if kind == "rollback":
        if target is None:
            raise ValueError("A rollback needs the deployment to roll back to.")
        dep.version = target.version
        dep.image_uri = target.image_uri
        dep.image_digest = target.image_digest
        dep.source_ref = target.source_ref
        dep.rollback_of = target.id
        dep.spec_snapshot = dict(target.spec_snapshot)
        dep.source_snapshot = dict(target.source_snapshot)
    else:
        if spec is None:
            raise ValueError("A deployment needs the application's configuration.")
        dep.version = f"r{app.revision_counter + 1}"
        dep.spec_snapshot = spec_snapshot(spec)
        dep.source_snapshot = dict(source_override) if source_override is not None else source_snapshot(app)
        dep.source_ref = source_ref_label or source_ref(dep.source_snapshot)

    claimed = db.execute(
        update(Application)
        .where(
            Application.id == app.id,
            Application.active_deployment_id.is_(None),
            Application.deleted_at.is_(None),
            Application.delete_requested_at.is_(None),
        )
        .values(active_deployment_id=dep.id)
        .execution_options(synchronize_session=False)
    )
    if getattr(claimed, "rowcount", 0) != 1:
        db.rollback()
        if app.delete_requested_at is not None or app.deleted_at is not None:
            raise conflict("application_deleting", "This application is being deleted.")
        raise conflict("deployment_in_progress", "A deployment for this application is already in progress.")

    db.add(dep)
    app.active_deployment_id = dep.id
    app.latest_deployment_id = dep.id
    app.status = "deploying"
    app.updated_at = now
    if kind == "deploy":
        app.revision_counter += 1
    db.flush()
    if kind == "rollback":
        add_event(db, dep, f"Rollback to version {dep.version} queued.", stage="queued")
    elif awaiting:
        add_event(db, dep, f"Version {dep.version} is waiting for approval before it deploys.", stage="queued")
    else:
        add_event(db, dep, f"Deployment of version {dep.version} queued.", stage="queued")
    return dep
