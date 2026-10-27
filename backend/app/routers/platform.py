"""Per-application platform features: automation (auto-deploy, previews, approvals, environments),
promotion, sleep schedules and pausing, metrics, cost estimates and custom domains."""

from __future__ import annotations

import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import clock, validation
from app.api.deps import Principal, authorize, get_db, get_principal, get_providers, get_settings, wake_worker
from app.api.responses import respond
from app.api.serializers import application_summary, configuration_out, deployment_out, latest_deployments
from app.config import Settings
from app.errors import ApiError, FieldError, conflict, invalid, not_found
from app.models import Application, ApplicationSpec, CustomDomain, Deployment, ProviderConnection
from app.providers.base import ProviderError, ProviderRegistry
from app.providers.capabilities import capabilities_for
from app.routers.applications import find_app, find_spec
from app.schemas import (
    ApplicationConfigurationOut,
    ApplicationSummaryOut,
    AutomationIn,
    AutomationOut,
    ConfigFileIn,
    CostEstimateIn,
    CostEstimateOut,
    CostLineOut,
    DeploymentOut,
    DnsRecordOut,
    DomainIn,
    DomainOut,
    MetricPointOut,
    MetricSeriesOut,
    MetricsOut,
    RequestedConfigurationIn,
    ScheduleIO,
    ScheduleOut,
    WebhookOut,
    WebhookSecretOut,
)
from app.services import apps, audit, config_file, costs, idempotency, schedules, specs
from app.services.deployments import source_snapshot, spec_snapshot, start_deployment
from app.services.targets import app_target, connection_info
from app.worker.tasks import pause_state

logger = logging.getLogger(__name__)
router = APIRouter(tags=["platform"])

RANGES: dict[str, tuple[int, int]] = {"1h": (60, 60), "6h": (360, 300), "24h": (1440, 900), "7d": (10080, 3600)}
_DOMAIN = re.compile(r"(?a)^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_PROVIDER_HOST_SUFFIXES = (
    ".awsapprunner.com",
    ".azurecontainerapps.io",
    ".run.app",
    ".amazonaws.com",
    ".azure.com",
    ".windows.net",
)


def _webhook_url(request: Request, app: Application) -> str:
    return str(request.url_for("github_webhook", application_id=app.id))


def _automation(request: Request, app: Application) -> AutomationOut:
    return AutomationOut(
        environment=app.environment,
        promote_to_id=app.promote_to_id,
        auto_deploy=app.auto_deploy,
        auto_deploy_branch=app.auto_deploy_branch,
        previews_enabled=app.previews_enabled,
        config_from_repo=app.config_from_repo,
        requires_approval=app.requires_approval,
        webhook=WebhookOut(url=_webhook_url(request, app), secret_configured=bool(app.webhook_secret)),
    )


def _audit(db: Session, principal: Principal, app: Application, action: str) -> None:
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action=action,
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
        request_id=principal.request_id,
    )


# ---------- automation ----------


@router.get("/apps/{application_id}/automation", response_model=AutomationOut)
def get_automation(
    application_id: str, request: Request, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    return respond(_automation(request, find_app(db, principal, application_id)))


@router.put("/apps/{application_id}/automation", response_model=AutomationOut)
def update_automation(
    application_id: str,
    body: AutomationIn,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    authorize(
        db,
        principal,
        "applications:update",
        action="application.automation_updated",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
    )
    if body.requires_approval != app.requires_approval:
        # Only approvers may add or remove the approval gate.
        authorize(
            db,
            principal,
            "deployments:approve",
            action="application.automation_updated",
            resource_type="application",
            resource_id=app.id,
            resource_name=app.name,
        )
    errors: list[FieldError] = []
    if app.environment == "preview" and body.environment != "preview":
        errors.append(FieldError("environment", "Pull request previews stay previews."))
    if body.environment == "preview" and app.environment != "preview":
        errors.append(FieldError("environment", "Previews are created from pull requests."))
    repository = app.source_type == "repository"
    if (body.auto_deploy or body.previews_enabled or body.config_from_repo) and not repository:
        errors.append(FieldError("auto_deploy", "Auto-deploy, previews and cloudbridge.yaml need a repository source."))
    branch = (body.auto_deploy_branch or "").strip() or None
    if branch and (problem := validation.git_branch(branch)):
        errors.append(FieldError("auto_deploy_branch", problem))
    if body.auto_deploy and not branch and app.ref_type != "branch":
        errors.append(FieldError("auto_deploy_branch", "Choose the branch whose pushes deploy this application."))
    target: Application | None = None
    if body.promote_to_id:
        target = db.scalar(
            select(Application).where(
                Application.id == body.promote_to_id,
                Application.organization_id == principal.organization_id,
                Application.deleted_at.is_(None),
            )
        )
        if target is None or target.id == app.id:
            errors.append(FieldError("promote_to_id", "Choose another application to promote to."))
        elif target.provider != app.provider:
            errors.append(
                FieldError("promote_to_id", "Promotion copies the exact image, so both apps need the same provider.")
            )
    if errors:
        raise invalid(errors)
    app.environment = body.environment
    app.promote_to_id = target.id if target is not None else None
    app.auto_deploy = body.auto_deploy
    app.auto_deploy_branch = branch
    app.previews_enabled = body.previews_enabled
    app.config_from_repo = body.config_from_repo
    app.requires_approval = body.requires_approval
    if (app.auto_deploy or app.previews_enabled) and not app.webhook_secret:
        app.webhook_secret = apps.new_webhook_secret()
    app.updated_at = clock.utcnow()
    _audit(db, principal, app, "application.automation_updated")
    db.commit()
    return respond(_automation(request, app))


@router.post("/apps/{application_id}/webhook-secret", response_model=WebhookSecretOut)
def rotate_webhook_secret(
    application_id: str, request: Request, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    """Creates a new GitHub webhook secret. It is shown only in this response."""
    app = find_app(db, principal, application_id)
    authorize(
        db,
        principal,
        "applications:update",
        action="application.webhook_secret_rotated",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
    )
    if app.source_type != "repository":
        raise conflict("not_a_repository", "Only applications built from a repository receive GitHub webhooks.")
    secret = apps.new_webhook_secret()
    app.webhook_secret = secret
    app.updated_at = clock.utcnow()
    _audit(db, principal, app, "application.webhook_secret_rotated")
    db.commit()
    return respond(WebhookSecretOut(url=_webhook_url(request, app), secret=secret))


@router.put("/apps/{application_id}/config-file", response_model=ApplicationConfigurationOut)
def apply_config_file(
    application_id: str,
    body: ConfigFileIn,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Applies a cloudbridge.yaml (as the CLI's `apply` command sends it) to the requested configuration."""
    app = find_app(db, principal, application_id)
    authorize(
        db,
        principal,
        "applications:update",
        action="application.updated",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
    )
    spec = find_spec(db, app)
    if body.version != spec.version:
        raise conflict(
            "version_conflict", "The configuration changed since it was loaded. Reload it and apply the file again."
        )
    try:
        merged = config_file.parse(body.content, spec_snapshot(spec))
        parsed = RequestedConfigurationIn.model_validate(merged)
    except config_file.ConfigFileError as error:
        raise invalid(error.errors) from None
    except ValueError as error:
        raise invalid([FieldError("config", str(error).splitlines()[0])]) from None
    requested, errors = specs.normalize_requested(parsed)
    image = app.source_image if app.source_type == "image" else None
    errors += specs.blocking(
        specs.findings_for(settings, app.provider, app.application_type, app.region, requested, image)
    )
    if errors:
        raise invalid(errors)
    for key in ("port", "cpu", "memory", "min_instances", "max_instances", "concurrency", "exposure"):
        setattr(spec, key, getattr(requested, key))
    spec.health_check_path = requested.health_check_path
    spec.environment = requested.environment
    spec.secret_refs = requested.secret_refs
    spec.version += 1
    spec.updated_at = clock.utcnow()
    app.updated_at = spec.updated_at
    _audit(db, principal, app, "application.updated")
    db.commit()
    return respond(configuration_out(spec))


# ---------- previews and promotion ----------


@router.get("/apps/{application_id}/previews", response_model=list[ApplicationSummaryOut])
def list_previews(
    application_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    rows = list(
        db.scalars(
            select(Application)
            .where(Application.preview_of_id == app.id, Application.deleted_at.is_(None))
            .order_by(Application.preview_number.desc())
        ).all()
    )
    latest = latest_deployments(db, rows)
    return respond([application_summary(a, latest.get(a.latest_deployment_id or "")) for a in rows])


@router.post("/apps/{application_id}/promote", status_code=202, response_model=DeploymentOut)
def promote(
    application_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Deploys the exact image this application runs (pinned by digest) to its promotion target, with the
    target's own configuration."""
    app = find_app(db, principal, application_id)
    if not app.promote_to_id:
        raise conflict("no_promotion_target", "Choose an application to promote to first.")
    target = find_app(db, principal, app.promote_to_id)
    authorize(
        db,
        principal,
        "deployments:create",
        action="deployment.created",
        resource_type="deployment",
        resource_name=target.name,
    )
    ctx = idempotency.context(
        request,
        scope=f"POST /apps/{app.id}/promote",
        payload={},
        organization_id=principal.organization_id,
        user_id=principal.user.id,
    )
    if (record := idempotency.lookup(db, ctx)) is not None:
        # Render the replay with the application the deployment belongs to: the promotion target may have
        # been changed since the first request.
        dep = db.get(Deployment, record.resource_id)
        promoted_to = db.get(Application, dep.application_id) if dep is not None else None
        if dep is None or promoted_to is None or promoted_to.organization_id != principal.organization_id:
            raise not_found("deployment")
        return respond(deployment_out(dep, promoted_to, principal), record.status_code)
    if not app.image_uri or not app.image_digest or app.current_version is None:
        raise conflict("nothing_to_promote", f"{app.name} has no successful deployment to promote yet.")
    spec = find_spec(db, target)
    image = f"{app.image_uri}@{app.image_digest}"
    source = {**source_snapshot(target), "type": "image", "image": image, "config_file": False}
    dep = start_deployment(
        db,
        app=target,
        spec=spec,
        kind="deploy",
        target=None,
        actor=principal.user,
        trigger="promotion",
        source_override=source,
        source_ref_label=f"{app.name} {app.current_version} ({app.image_digest[:19]})",
    )
    idempotency.remember(db, ctx, status_code=202, resource_type="deployment", resource_id=dep.id)
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action="deployment.promoted",
        resource_type="deployment",
        resource_id=dep.id,
        resource_name=target.name,
        request_id=principal.request_id,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise idempotency.in_flight_conflict() from None
    wake_worker(request)
    return respond(deployment_out(dep, target, principal), 202)


# ---------- sleep schedules and pausing ----------


def _schedule_out(app: Application) -> ScheduleOut:
    schedule = schedules.Schedule.from_json(app.sleep_schedule)
    return ScheduleOut(
        schedule=ScheduleIO(**schedule.to_json()) if schedule else None,
        paused=app.paused,
        paused_by_schedule=app.paused_by_schedule,
        asleep_now=schedule.asleep_at(clock.utcnow()) if schedule else False,
    )


@router.get("/apps/{application_id}/schedule", response_model=ScheduleOut)
def get_schedule(
    application_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    return respond(_schedule_out(find_app(db, principal, application_id)))


@router.put("/apps/{application_id}/schedule", response_model=ScheduleOut)
def set_schedule(
    application_id: str,
    body: ScheduleIO,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    providers: ProviderRegistry = Depends(get_providers),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    authorize(
        db,
        principal,
        "applications:update",
        action="application.schedule_updated",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
    )
    if not capabilities_for(app.provider, settings).features.pause:
        raise conflict("not_supported", f"{providers.get(app.provider).label} cannot pause services yet.")
    schedule, errors = schedules.parse(body.timezone, body.days, body.sleep_at, body.wake_at)
    if errors or schedule is None:
        raise invalid(errors)
    app.sleep_schedule = schedule.to_json()
    app.updated_at = clock.utcnow()
    _audit(db, principal, app, "application.schedule_updated")
    db.commit()
    wake_worker(request)
    return respond(_schedule_out(app))


@router.delete("/apps/{application_id}/schedule", response_model=ScheduleOut)
def delete_schedule(
    application_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    authorize(
        db,
        principal,
        "applications:update",
        action="application.schedule_updated",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
    )
    app.sleep_schedule = None
    # A pause the schedule made becomes a manual one: removing the schedule never starts traffic by itself.
    app.paused_by_schedule = False
    app.updated_at = clock.utcnow()
    _audit(db, principal, app, "application.schedule_updated")
    db.commit()
    return respond(_schedule_out(app))


def _set_paused(
    db: Session, principal: Principal, providers: ProviderRegistry, application_id: str, paused: bool
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    action = "application.paused" if paused else "application.resumed"
    authorize(
        db,
        principal,
        "deployments:create",
        action=action,
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
    )
    if app.active_deployment_id is not None or app.delete_requested_at is not None:
        raise conflict("deployment_in_progress", "Wait for the running deployment to finish first.")
    if app.current_deployment_id is None or app.provider_service_id is None:
        raise conflict("not_deployed", "Deploy the application before pausing it.")
    if app.paused == paused:
        return respond(_schedule_out(app))
    conn = db.get(ProviderConnection, app.connection_id)
    if conn is None or conn.status != "connected":
        raise conflict("connection_unavailable", "The application's cloud account is not connected.")
    try:
        providers.get(app.provider).set_paused(
            connection_info(conn, app.region), app_target(app, db.get(ApplicationSpec, app.id)), paused
        )
    except ProviderError as error:
        raise _provider_error(error) from None
    pause_state(app, paused, by_schedule=False)
    _audit(db, principal, app, action)
    db.commit()
    return respond(_schedule_out(app))


@router.post("/apps/{application_id}/pause", response_model=ScheduleOut)
def pause(
    application_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    providers: ProviderRegistry = Depends(get_providers),
) -> JSONResponse:
    return _set_paused(db, principal, providers, application_id, True)


@router.post("/apps/{application_id}/resume", response_model=ScheduleOut)
def resume(
    application_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    providers: ProviderRegistry = Depends(get_providers),
) -> JSONResponse:
    return _set_paused(db, principal, providers, application_id, False)


def _provider_error(error: ProviderError) -> ApiError:
    if error.code == "not_supported":
        return ApiError(409, "not_supported", error.message)
    if error.code in ("not_deployed", "service_name_taken", "invalid_request", "conflict", "provider_busy"):
        return ApiError(409, error.code, error.message)
    if error.code in ("insufficient_permissions", "invalid_credentials"):
        return ApiError(502, error.code, error.message)
    return ApiError(502, "provider_unavailable", "The provider could not be reached. Try again shortly.")


# ---------- metrics and cost ----------


@router.get("/apps/{application_id}/metrics", response_model=MetricsOut)
def get_metrics(
    application_id: str,
    range: Literal["1h", "6h", "24h", "7d"] = "1h",
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    providers: ProviderRegistry = Depends(get_providers),
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    minutes, step = RANGES[range]
    conn = db.get(ProviderConnection, app.connection_id)
    reason: str | None = None
    series: list[MetricSeriesOut] = []
    if app.provider_service_id is None:
        reason = "The application has not been deployed yet."
    elif conn is None or conn.status != "connected":
        reason = "The application's cloud account is not connected."
    else:
        try:
            result = providers.get(app.provider).metrics(
                connection_info(conn, app.region), app_target(app), minutes, step
            )
        except ProviderError as error:
            logger.info("Metrics unavailable", extra={"application_id": app.id, "code": error.code})
            reason = (
                error.message
                if error.code in ("not_supported", "insufficient_permissions")
                else "The provider's metrics could not be loaded right now."
            )
        else:
            series = [
                MetricSeriesOut(
                    name=s.name,
                    unit=s.unit,
                    points=[MetricPointOut(timestamp=p.timestamp, value=round(p.value, 4)) for p in s.points],
                )
                for s in result
            ]
    return respond(MetricsOut(range=range, step_seconds=step, series=series, unavailable_reason=reason))


def _cost_out(estimate: costs.CostEstimate) -> CostEstimateOut:
    return CostEstimateOut(
        provider=estimate.provider,
        currency="USD",
        monthly_low=estimate.monthly_low,
        monthly_high=estimate.monthly_high,
        lines=[CostLineOut(item=line.item, low=line.low, high=line.high) for line in estimate.lines],
        assumptions=estimate.assumptions,
    )


@router.get("/apps/{application_id}/cost-estimate", response_model=CostEstimateOut)
def application_cost(
    application_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    spec = find_spec(db, app)
    return respond(
        _cost_out(costs.estimate(app.provider, spec.cpu, spec.memory, spec.min_instances, spec.max_instances))
    )


@router.post("/cost-estimate", response_model=CostEstimateOut)
def estimate_cost(
    body: CostEstimateIn, _: Principal = Depends(get_principal), settings: Settings = Depends(get_settings)
) -> JSONResponse:
    caps = capabilities_for(body.provider, settings)
    errors = []
    if body.cpu not in caps.cpu_options:
        errors.append(FieldError("cpu", "Choose a CPU size the provider offers."))
    if body.memory not in caps.memory_options:
        errors.append(FieldError("memory", "Choose a memory size the provider offers."))
    if not 0 <= body.min_instances <= body.max_instances <= 1000:
        errors.append(FieldError("max_instances", "Instances must be 0–1000, with the maximum at least the minimum."))
    if errors:
        raise invalid(errors)
    return respond(
        _cost_out(costs.estimate(body.provider, body.cpu, body.memory, body.min_instances, body.max_instances))
    )


# ---------- custom domains ----------


def _domain_out(domain: CustomDomain) -> DomainOut:
    return DomainOut(
        id=domain.id,
        domain=domain.domain,
        status=domain.status,
        records=[DnsRecordOut(**r) for r in domain.records or []],
        message=domain.message,
        created_at=domain.created_at,
        checked_at=domain.checked_at,
    )


@router.get("/apps/{application_id}/domains", response_model=list[DomainOut])
def list_domains(
    application_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    rows = db.scalars(
        select(CustomDomain).where(CustomDomain.application_id == app.id).order_by(CustomDomain.created_at)
    ).all()
    return respond([_domain_out(d) for d in rows])


@router.post("/apps/{application_id}/domains", status_code=201, response_model=DomainOut)
def add_domain(
    application_id: str,
    body: DomainIn,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    providers: ProviderRegistry = Depends(get_providers),
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    authorize(
        db,
        principal,
        "applications:update",
        action="application.domain_added",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
    )
    name = body.domain.strip().lower().rstrip(".")
    if not _DOMAIN.match(name) or name.endswith(_PROVIDER_HOST_SUFFIXES):
        raise invalid([FieldError("domain", "Enter a domain you own, such as api.example.com.")])
    exists = db.scalar(
        select(func.count())
        .select_from(CustomDomain)
        .where(CustomDomain.organization_id == principal.organization_id, CustomDomain.domain == name)
    )
    if exists:
        raise conflict("duplicate_domain", "This domain is already added.", [FieldError("domain", "Already added.")])
    if app.provider_service_id is None:
        raise conflict("not_deployed", "Deploy the application before adding a domain.")
    conn = db.get(ProviderConnection, app.connection_id)
    if conn is None or conn.status != "connected":
        raise conflict("connection_unavailable", "The application's cloud account is not connected.")
    try:
        state = providers.get(app.provider).add_domain(connection_info(conn, app.region), app_target(app), name)
    except ProviderError as error:
        raise _provider_error(error) from None
    now = clock.utcnow()
    domain = CustomDomain(
        organization_id=principal.organization_id,
        application_id=app.id,
        domain=name,
        status=state.status,
        records=[dict(r.__dict__) for r in state.records],
        message=state.message,
        created_at=now,
        checked_at=now,
    )
    db.add(domain)
    _audit(db, principal, app, "application.domain_added")
    db.commit()
    return respond(_domain_out(domain), 201)


def _find_domain(db: Session, app: Application, domain_id: str) -> CustomDomain:
    domain = db.scalar(select(CustomDomain).where(CustomDomain.id == domain_id, CustomDomain.application_id == app.id))
    if domain is None:
        raise not_found("domain")
    return domain


@router.post("/apps/{application_id}/domains/{domain_id}/check", response_model=DomainOut)
def check_domain(
    application_id: str,
    domain_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    providers: ProviderRegistry = Depends(get_providers),
) -> JSONResponse:
    app = find_app(db, principal, application_id)
    domain = _find_domain(db, app, domain_id)
    if domain.status == "removing":
        return respond(_domain_out(domain))
    conn = db.get(ProviderConnection, app.connection_id)
    if conn is None or conn.status != "connected":
        raise conflict("connection_unavailable", "The application's cloud account is not connected.")
    try:
        state = providers.get(app.provider).domain_status(
            connection_info(conn, app.region), app_target(app), domain.domain
        )
    except ProviderError as error:
        raise _provider_error(error) from None
    domain.status = state.status
    domain.records = [dict(r.__dict__) for r in state.records]
    domain.message = state.message
    domain.checked_at = clock.utcnow()
    db.commit()
    return respond(_domain_out(domain))


@router.delete("/apps/{application_id}/domains/{domain_id}", status_code=202)
def remove_domain(
    application_id: str,
    domain_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> Response:
    app = find_app(db, principal, application_id)
    authorize(
        db,
        principal,
        "applications:update",
        action="application.domain_removed",
        resource_type="application",
        resource_id=app.id,
        resource_name=app.name,
    )
    domain = _find_domain(db, app, domain_id)
    domain.status = "removing"
    domain.locked_until = None
    _audit(db, principal, app, "application.domain_removed")
    db.commit()
    wake_worker(request)
    return Response(status_code=202)
