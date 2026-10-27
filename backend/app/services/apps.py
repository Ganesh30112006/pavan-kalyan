"""Application lifecycle shared by the API and webhooks: deletion requests and pull-request previews."""

from __future__ import annotations

import secrets
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import clock
from app.models import Application, ApplicationSpec, CustomDomain, User
from app.services import audit

PREVIEW_NAME_LIMIT = {"azure_container_apps": 32}
DEFAULT_NAME_LIMIT = 40


def new_webhook_secret() -> str:
    return secrets.token_hex(32)


def request_delete(
    db: Session, app: Application, actor: User | None, request_id: str | None = None
) -> Literal["deleted", "deleting", "busy"]:
    """Starts deleting `app`. "deleted" when nothing existed on the provider, "deleting" when the worker
    continues in the background, "busy" when a deployment is running. The caller commits."""
    if app.delete_requested_at is not None or app.deleted_at is not None:
        return "deleting"
    now = clock.utcnow()
    # Claim the application atomically, so a deployment started at the same moment cannot slip in.
    claimed = db.execute(
        update(Application)
        .where(
            Application.id == app.id,
            Application.active_deployment_id.is_(None),
            Application.delete_requested_at.is_(None),
            Application.deleted_at.is_(None),
        )
        .values(delete_requested_at=now)
        .execution_options(synchronize_session=False)
    )
    if getattr(claimed, "rowcount", 0) != 1:
        db.rollback()
        db.refresh(app)
        if app.delete_requested_at is not None or app.deleted_at is not None:
            return "deleting"
        return "busy"
    if app.provider_service_id is None:
        # Nothing exists on the provider: remove it now.
        app.delete_requested_at = None
        app.deleted_at = now
        app.status = "deleting"
        app.updated_at = now
        forget_domains(db, app)
        audit.record(
            db,
            organization_id=app.organization_id,
            actor=actor,
            action="application.deleted",
            resource_type="application",
            resource_id=app.id,
            resource_name=app.name,
            request_id=request_id,
        )
        return "deleted"
    app.status = "deleting"
    app.delete_requested_at = now
    app.delete_requested_by_id = actor.id if actor is not None else None
    app.delete_attempts = 0
    app.updated_at = now
    app.locked_until = None
    audit.record(
        db,
        organization_id=app.organization_id,
        actor=actor,
        action="application.delete_requested",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
        request_id=request_id,
    )
    return "deleting"


def forget_domains(db: Session, app: Application) -> None:
    """The provider removes a service's domains with the service; drop CloudBridge's records of them."""
    for domain in db.scalars(select(CustomDomain).where(CustomDomain.application_id == app.id)).all():
        db.delete(domain)


def preview_name(base: Application, number: int) -> str:
    limit = PREVIEW_NAME_LIMIT.get(base.provider, DEFAULT_NAME_LIMIT)
    suffix = f"-pr-{number}"
    return f"{base.name[: limit - len(suffix)].rstrip('-')}{suffix}"


def find_preview(db: Session, base: Application, number: int) -> Application | None:
    return db.scalar(
        select(Application).where(
            Application.preview_of_id == base.id,
            Application.preview_number == number,
            Application.deleted_at.is_(None),
        )
    )


def create_preview(db: Session, base: Application, number: int, head_sha: str, title: str) -> Application:
    """A short-lived copy of `base` that runs a pull request's head commit."""
    now = clock.utcnow()
    preview = Application(
        organization_id=base.organization_id,
        name=preview_name(base, number),
        description=f"Preview of pull request #{number}: {title}"[:280],
        application_type=base.application_type,
        runtime=base.runtime,
        provider=base.provider,
        connection_id=base.connection_id,
        region=base.region,
        status="draft",
        health="unknown",
        source_type="repository",
        repository_url=base.repository_url,
        ref_type="commit",
        git_ref=head_sha,
        dockerfile_path=base.dockerfile_path,
        build_context=base.build_context,
        environment="preview",
        preview_of_id=base.id,
        preview_number=number,
        config_from_repo=base.config_from_repo,
        created_at=now,
        updated_at=now,
        created_by=f"GitHub pull request #{number}",
    )
    db.add(preview)
    db.flush()
    spec = db.get(ApplicationSpec, base.id)
    if spec is None:
        raise ValueError(f"Application {base.id} has no configuration to copy into its preview.")
    fields: dict[str, Any] = {
        key: getattr(spec, key)
        for key in (
            "port",
            "cpu",
            "memory",
            "min_instances",
            "max_instances",
            "concurrency",
            "exposure",
            "health_check_path",
        )
    }
    db.add(
        ApplicationSpec(
            application_id=preview.id,
            version=1,
            environment=[dict(e) for e in spec.environment],
            secret_refs=[dict(s) for s in spec.secret_refs],
            updated_at=now,
            adjustments=[],
            **fields,
        )
    )
    db.flush()
    audit.record(
        db,
        organization_id=base.organization_id,
        actor=None,
        action="application.created",
        resource_type="application",
        resource_id=preview.id,
        resource_name=preview.name,
    )
    return preview
