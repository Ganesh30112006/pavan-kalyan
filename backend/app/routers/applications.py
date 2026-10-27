"""Applications: list, create, read, delete, configuration (spec), validation and runtime logs."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import clock, validation
from app.api.deps import Principal, authorize, get_db, get_principal, get_providers, get_settings, wake_worker
from app.api.responses import PageQuery, PageSizeQuery, check_paging, paginate, parse_list, parse_sort, respond
from app.api.serializers import application_out, application_summary, configuration_out, latest_deployments
from app.config import Settings
from app.domain import APPLICATION_STATUSES, HEALTH_STATUSES, PROVIDER_KINDS
from app.errors import ApiError, FieldError, conflict, invalid, not_found
from app.models import Application, ApplicationSpec, ProviderConnection
from app.providers.base import ConnectionInfo, ProviderError, ProviderRegistry
from app.providers.capabilities import capabilities_for
from app.schemas import (
    AdjustmentOut,
    ApplicationConfigurationOut,
    ApplicationOut,
    ApplicationSummaryOut,
    CreateApplicationIn,
    LogEntryOut,
    LogPageOut,
    Page,
    SpecIssueOut,
    SpecValidationOut,
    UpdateConfigurationIn,
)
from app.services import audit, idempotency, specs
from app.services.apps import request_delete
from app.services.targets import app_target, connection_info

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/apps", tags=["applications"])

HEALTH_ORDER = case({"unhealthy": 0, "degraded": 1, "unknown": 2, "healthy": 3}, value=Application.health, else_=2)
RUNTIME_LOG_LIMIT = 200


def app_connection_info(conn: ProviderConnection, app: Application) -> ConnectionInfo:
    """The connection, targeting the application's region."""
    return connection_info(conn, app.region)


def find_app(db: Session, principal: Principal, application_id: str) -> Application:
    app = db.scalar(
        select(Application).where(
            Application.id == application_id,
            Application.organization_id == principal.organization_id,
            Application.deleted_at.is_(None),
        )
    )
    if app is None:
        raise not_found("application")
    return app


def find_spec(db: Session, app: Application) -> ApplicationSpec:
    spec = db.get(ApplicationSpec, app.id)
    if spec is None:
        raise not_found("application configuration")
    return spec


@router.get("", response_model=Page[ApplicationSummaryOut])
def list_applications(
    page: PageQuery = 1,
    page_size: PageSizeQuery = 25,
    search: str | None = Query(default=None, max_length=200),
    status: str | None = None,
    health: str | None = None,
    provider: str | None = None,
    sort: str | None = None,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> JSONResponse:
    check_paging(page, page_size)
    statuses = parse_list(status, APPLICATION_STATUSES, "status")
    healths = parse_list(health, HEALTH_STATUSES, "health")
    providers = parse_list(provider, PROVIDER_KINDS, "provider")
    order = parse_sort(sort, ("name", "updated_at", "health"))

    query = select(Application).where(
        Application.organization_id == principal.organization_id, Application.deleted_at.is_(None)
    )
    if search and search.strip():
        term = search.strip()
        query = query.where(
            or_(
                Application.name.icontains(term, autoescape=True),
                Application.description.icontains(term, autoescape=True),
            )
        )
    if statuses:
        query = query.where(Application.status.in_(statuses))
    if healths:
        query = query.where(Application.health.in_(healths))
    if providers:
        query = query.where(Application.provider.in_(providers))
    if order is None:
        query = query.order_by(Application.created_at.desc(), Application.id)
    else:
        key, descending = order
        column: Any = {"name": Application.name, "updated_at": Application.updated_at, "health": HEALTH_ORDER}[key]
        query = query.order_by(column.desc() if descending else column.asc(), Application.name, Application.id)

    rows, total = paginate(db, query, page, page_size)
    latest = latest_deployments(db, list(rows))
    items = [application_summary(a, latest.get(a.latest_deployment_id or "")) for a in rows]
    return respond(Page[ApplicationSummaryOut](items=items, total=total, page=page, page_size=page_size))


def _check_create(
    db: Session, principal: Principal, body: CreateApplicationIn, settings: Settings
) -> tuple[ProviderConnection | None, dict[str, Any], specs.Requested, list[FieldError]]:
    errors: list[FieldError] = []
    name = body.name.strip()
    if problem := validation.application_name(name):
        errors.append(FieldError("name", problem))
    description = (body.description or "").strip() or None
    if description and len(description) > 280:
        errors.append(FieldError("description", "Description must be 280 characters or fewer."))
    source, source_errors = specs.normalize_source(body.source)
    errors += source_errors
    requested, spec_errors = specs.normalize_requested(body)
    errors += spec_errors

    conn = db.scalar(
        select(ProviderConnection).where(
            ProviderConnection.id == body.connection_id,
            ProviderConnection.organization_id == principal.organization_id,
        )
    )
    if conn is None:
        errors.append(FieldError("connection_id", "Select a cloud account."))
    elif conn.status != "connected":
        errors.append(
            FieldError(
                "connection_id", "This cloud account is not connected. Validate it on the Cloud providers page first."
            )
        )
    if not body.region.strip():
        errors.append(FieldError("region", "Region is required."))
    if (
        conn is not None
        and conn.provider == "azure_container_apps"
        and len(name) > 32
        and "name" not in {e.field for e in errors}
    ):
        errors.append(FieldError("name", "Azure Container Apps names can be at most 32 characters."))
    if conn is not None:
        findings = specs.findings_for(
            settings,
            conn.provider,
            body.application_type,
            body.region,
            requested,
            source["image"] if source["type"] == "image" and not source_errors else None,
        )
        reported = {e.field for e in errors}
        errors += [e for e in specs.blocking(findings) if e.field not in reported]
    return conn, source, requested, errors


@router.post("", status_code=201, response_model=ApplicationOut)
def create_application(
    body: CreateApplicationIn,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    authorize(db, principal, "applications:create", action="application.created", resource_type="application")
    ctx = idempotency.context(
        request,
        scope="POST /apps",
        payload=body.model_dump(mode="json"),
        organization_id=principal.organization_id,
        user_id=principal.user.id,
    )
    if (record := idempotency.lookup(db, ctx)) is not None:
        return respond(application_out(db, find_app(db, principal, record.resource_id)), record.status_code)

    conn, source, requested, errors = _check_create(db, principal, body, settings)
    if errors or conn is None:
        raise invalid(errors)
    name = body.name.strip()
    duplicate_error = conflict(
        "duplicate_name",
        "An application with this name already exists.",
        [FieldError("name", "This name is already in use.")],
    )
    exists = db.scalar(
        select(func.count())
        .select_from(Application)
        .where(
            Application.organization_id == principal.organization_id,
            Application.name == name,
            Application.deleted_at.is_(None),
        )
    )
    if exists:
        raise duplicate_error

    now = clock.utcnow()
    app = Application(
        organization_id=principal.organization_id,
        name=name,
        description=(body.description or "").strip() or None,
        application_type=body.application_type,
        runtime=body.runtime,
        provider=conn.provider,
        connection_id=conn.id,
        region=body.region,
        status="draft",
        health="unknown",
        source_type=source["type"],
        source_image=source["image"],
        repository_url=source["repository_url"],
        ref_type=source["ref_type"],
        git_ref=source["git_ref"],
        dockerfile_path=source["dockerfile_path"],
        build_context=source["build_context"],
        created_at=now,
        updated_at=now,
        created_by=principal.user.display_name,
        created_by_id=principal.user.id,
    )
    db.add(app)
    db.flush()
    db.add(
        ApplicationSpec(
            application_id=app.id,
            version=1,
            port=requested.port,
            cpu=requested.cpu,
            memory=requested.memory,
            min_instances=requested.min_instances,
            max_instances=requested.max_instances,
            concurrency=requested.concurrency,
            exposure=requested.exposure,
            health_check_path=requested.health_check_path,
            environment=requested.environment,
            secret_refs=requested.secret_refs,
            updated_at=now,
            effective=None,
            adjustments=[],
        )
    )
    idempotency.remember(db, ctx, status_code=201, resource_type="application", resource_id=app.id)
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action="application.created",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
        request_id=principal.request_id,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if (record := idempotency.lookup(db, ctx)) is not None:
            return respond(application_out(db, find_app(db, principal, record.resource_id)), record.status_code)
        raise duplicate_error from None
    return respond(application_out(db, app), 201)


@router.get("/{application_id}", response_model=ApplicationOut)
def get_application(
    application_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    return respond(application_out(db, find_app(db, principal, application_id)))


@router.delete(
    "/{application_id}", status_code=204, responses={202: {"description": "Deletion continues in the background"}}
)
def delete_application(
    application_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> Response:
    app = find_app(db, principal, application_id)
    authorize(
        db,
        principal,
        "applications:delete",
        action="application.deleted",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
    )
    if app.active_deployment_id is not None:
        raise conflict("deployment_in_progress", "Wait for the running deployment to finish before deleting.")
    outcome = request_delete(db, app, principal.user, principal.request_id)
    if outcome == "busy":
        raise conflict("deployment_in_progress", "Wait for the running deployment to finish before deleting.")
    db.commit()
    if outcome == "deleted":
        return Response(status_code=204)
    wake_worker(request)
    return Response(status_code=202)


@router.get("/{application_id}/spec", response_model=ApplicationConfigurationOut)
def get_configuration(
    application_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    return respond(configuration_out(find_spec(db, app)))


@router.put("/{application_id}/spec", response_model=ApplicationConfigurationOut)
def update_configuration(
    application_id: str,
    body: UpdateConfigurationIn,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    authorize(
        db,
        principal,
        "applications:update",
        action="application.configuration_updated",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
    )
    spec = find_spec(db, app)
    if app.delete_requested_at is not None:
        raise conflict("application_deleting", "This application is being deleted.")
    if body.version != spec.version:
        raise conflict(
            "version_conflict",
            "The configuration was changed by someone else. Reload to see the latest version.",
        )
    requested, errors = specs.normalize_requested(body)
    findings = specs.findings_for(
        settings,
        app.provider,
        app.application_type,
        app.region,
        requested,
        app.source_image if app.source_type == "image" else None,
    )
    reported = {e.field for e in errors}
    errors += [e for e in specs.blocking(findings) if e.field not in reported and not e.field.startswith("source.")]
    if errors:
        raise invalid(errors)
    now = clock.utcnow()
    spec.port = requested.port
    spec.cpu = requested.cpu
    spec.memory = requested.memory
    spec.min_instances = requested.min_instances
    spec.max_instances = requested.max_instances
    spec.concurrency = requested.concurrency
    spec.exposure = requested.exposure
    spec.health_check_path = requested.health_check_path
    spec.environment = requested.environment
    spec.secret_refs = requested.secret_refs
    spec.version = spec.version + 1
    spec.updated_at = now
    app.updated_at = now
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action="application.configuration_updated",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
        request_id=principal.request_id,
    )
    # Optimistic lock: the update applies only if nobody saved in between.
    result = db.execute(
        update(ApplicationSpec)
        .where(ApplicationSpec.application_id == app.id, ApplicationSpec.version == body.version)
        .values(version=body.version + 1)
        .execution_options(synchronize_session=False)
    )
    if getattr(result, "rowcount", 0) != 1:
        db.rollback()
        raise conflict(
            "version_conflict", "The configuration was changed by someone else. Reload to see the latest version."
        )
    db.commit()
    return respond(configuration_out(spec))


@router.post("/{application_id}/validate", response_model=SpecValidationOut)
def validate_configuration(
    application_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    providers: ProviderRegistry = Depends(get_providers),
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    spec = find_spec(db, app)
    requested = specs.Requested.from_snapshot(
        {
            "port": spec.port,
            "cpu": spec.cpu,
            "memory": spec.memory,
            "min_instances": spec.min_instances,
            "max_instances": spec.max_instances,
            "concurrency": spec.concurrency,
            "exposure": spec.exposure,
            "health_check_path": spec.health_check_path,
            "environment": spec.environment,
            "secret_refs": spec.secret_refs,
        }
    )
    image = app.source_image if app.source_type == "image" else None
    findings = specs.findings_for(settings, app.provider, app.application_type, app.region, requested, image)
    issues = [
        SpecIssueOut(field=f.field, severity="error" if f.severity == "error" else "warning", message=f.message)
        for f in findings
        if f.severity != "adjusted"
    ]

    conn = db.get(ProviderConnection, app.connection_id)
    if conn is None or conn.status != "connected":
        issues.append(
            SpecIssueOut(
                field="connection_id",
                severity="error",
                message="The application's cloud account is not connected, so the provider could not be checked.",
            )
        )
    else:
        info = app_connection_info(conn, app)
        adapter = providers.get(app.provider)
        try:
            adapter.check_access(info)
            if image and not any(f.field == "source.image" for f in findings):
                adapter.resolve_image(info, image)
        except ProviderError as error:
            field = "source.image" if error.code.startswith(("image_", "unsupported_registry")) else None
            issues.append(
                SpecIssueOut(
                    field=field,
                    severity="warning" if error.retryable else "error",
                    message=error.message
                    if not error.retryable
                    else f"{error.message} The image and permissions could not be checked.",
                )
            )
        if (
            app.source_type == "repository"
            and app.repository_url
            and (problem := validation.repository_host_is_public(app.repository_url))
        ):
            issues.append(SpecIssueOut(field="source.repository_url", severity="error", message=problem))

    adjustments = specs.predicted_adjustments(capabilities_for(app.provider, settings), requested)
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action="application.spec_validated",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
        request_id=principal.request_id,
    )
    db.commit()
    return respond(
        SpecValidationOut(
            valid=not any(i.severity == "error" for i in issues),
            issues=issues,
            adjustments=[AdjustmentOut(**a) for a in adjustments],
        )
    )


@router.get("/{application_id}/logs", response_model=LogPageOut)
def runtime_logs(
    application_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    providers: ProviderRegistry = Depends(get_providers),
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    conn = db.get(ProviderConnection, app.connection_id)
    if app.provider_service_id is None or conn is None:
        return respond(LogPageOut(entries=[], truncated=False))
    try:
        entries, truncated = providers.get(app.provider).runtime_logs(
            app_connection_info(conn, app), app_target(app), RUNTIME_LOG_LIMIT
        )
    except ProviderError as error:
        logger.info("Runtime logs unavailable", extra={"application_id": app.id, "code": error.code})
        raise ApiError(502, "provider_unavailable", "The provider's logs could not be loaded right now.") from None
    return respond(
        LogPageOut(
            entries=[LogEntryOut(timestamp=e.timestamp, stream=e.stream, message=e.message) for e in entries],
            truncated=truncated,
        )
    )
