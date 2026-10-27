"""Provider capabilities and cloud connections."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import clock, validation
from app.api.deps import Principal, authorize, get_db, get_principal, get_settings, wake_worker
from app.api.responses import respond
from app.api.serializers import connection_out, provider_setup_out, trusted_principal
from app.config import Settings
from app.domain import PROVIDER_KINDS
from app.errors import FieldError, conflict, invalid, not_found
from app.models import Application, ProviderConnection
from app.providers.capabilities import capabilities_for
from app.schemas import CapabilitiesOut, ConnectionOut, ConnectionSetupOut, CreateConnectionIn, FeaturesOut
from app.services import audit, idempotency

router = APIRouter(tags=["providers"])


@router.get("/providers/{provider}/capabilities", response_model=CapabilitiesOut)
def get_capabilities(
    provider: str, _: Principal = Depends(get_principal), settings: Settings = Depends(get_settings)
) -> JSONResponse:
    if provider not in PROVIDER_KINDS:
        raise not_found("provider")
    caps = capabilities_for(provider, settings)
    return respond(CapabilitiesOut(**{**caps.__dict__, "features": FeaturesOut(**caps.features.__dict__)}))


@router.get("/providers/{provider}/setup", response_model=ConnectionSetupOut)
def get_setup(
    provider: str, _: Principal = Depends(get_principal), settings: Settings = Depends(get_settings)
) -> JSONResponse:
    """What the customer's account must trust, before a connection exists. Not secret: connections show it too."""
    if provider not in PROVIDER_KINDS:
        raise not_found("provider")
    return respond(provider_setup_out(provider, settings))


def _find(db: Session, principal: Principal, connection_id: str) -> ProviderConnection:
    conn = db.scalar(
        select(ProviderConnection).where(
            ProviderConnection.id == connection_id, ProviderConnection.organization_id == principal.organization_id
        )
    )
    if conn is None:
        raise not_found("cloud connection")
    return conn


@router.get("/cloud-connections", response_model=list[ConnectionOut])
def list_connections(
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    rows = db.scalars(
        select(ProviderConnection)
        .where(ProviderConnection.organization_id == principal.organization_id)
        .order_by(ProviderConnection.created_at.desc())
    ).all()
    return respond([connection_out(c, settings) for c in rows])


@router.get("/cloud-connections/{connection_id}", response_model=ConnectionOut)
def get_connection(
    connection_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    return respond(connection_out(_find(db, principal, connection_id), settings))


def _validate_create(body: CreateConnectionIn, settings: Settings) -> list[FieldError]:
    errors: list[FieldError] = []
    name = body.name.strip()
    if not name:
        errors.append(FieldError("name", "Name is required."))
    elif len(name) > 100:
        errors.append(FieldError("name", "Name must be 100 characters or fewer."))
    caps = capabilities_for(body.provider, settings)
    if body.default_region not in caps.regions:
        errors.append(FieldError("default_region", "Choose a region the provider supports."))
    if body.provider in ("aws_app_runner", "aws_ecs_express"):
        key = "aws" if body.provider == "aws_app_runner" else "ecs"
        details = body.aws if body.provider == "aws_app_runner" else body.ecs
        if details is None:
            errors.append(FieldError(key, "AWS account details are required."))
        else:
            account = details.account_id.strip()
            role = details.role_arn.strip()
            if problem := validation.aws_account_id(account):
                errors.append(FieldError(f"{key}.account_id", problem))
            if problem := validation.aws_role_arn(role):
                errors.append(FieldError(f"{key}.role_arn", problem))
            elif (match := validation.AWS_ROLE_ARN.match(role)) and match.group(1) != account:
                errors.append(FieldError(f"{key}.role_arn", "The role must belong to the AWS account entered above."))
            cluster = (body.ecs.cluster or "").strip() if body.ecs is not None else ""
            if body.provider == "aws_ecs_express" and cluster and (problem := validation.ecs_cluster(cluster)):
                errors.append(FieldError("ecs.cluster", problem))
    elif body.provider == "kubernetes":
        if body.kubernetes is None:
            errors.append(FieldError("kubernetes", "Cluster details are required."))
        else:
            k = body.kubernetes
            checks = [
                ("api_server", validation.kubernetes_api_server(k.api_server.strip())),
                ("ca_certificate", validation.pem_certificate(k.ca_certificate)),
                ("namespace", validation.kubernetes_namespace(k.namespace.strip())),
            ]
            if k.auth == "aks":
                checks.append(("tenant_id", validation.azure_guid((k.tenant_id or "").strip(), "Tenant ID")))
                checks.append(("client_id", validation.azure_guid((k.client_id or "").strip(), "Client ID")))
            elif not (k.audience or "").strip():
                checks.append(("audience", "Enter the audience your cluster's OIDC authenticator expects."))
            errors += [FieldError(f"kubernetes.{field}", problem) for field, problem in checks if problem]
    elif body.provider == "azure_container_apps":
        if body.azure is None:
            errors.append(FieldError("azure", "Azure subscription details are required."))
        else:
            a = body.azure
            checks = [
                ("tenant_id", validation.azure_guid(a.tenant_id.strip(), "Tenant ID")),
                ("subscription_id", validation.azure_guid(a.subscription_id.strip(), "Subscription ID")),
                ("client_id", validation.azure_guid(a.client_id.strip(), "Client ID")),
                ("resource_group", validation.azure_resource_group(a.resource_group.strip())),
                ("environment_name", validation.azure_environment(a.environment_name.strip())),
            ]
            if a.registry_name and a.registry_name.strip():
                checks.append(("registry_name", validation.azure_registry(a.registry_name.strip())))
            errors += [FieldError(f"azure.{field}", problem) for field, problem in checks if problem]
    else:
        if body.gcp is None:
            errors.append(FieldError("gcp", "Google Cloud project details are required."))
        else:
            if problem := validation.gcp_project_id(body.gcp.project_id.strip()):
                errors.append(FieldError("gcp.project_id", problem))
            if problem := validation.gcp_workload_identity_provider(body.gcp.workload_identity_provider.strip()):
                errors.append(FieldError("gcp.workload_identity_provider", problem))
            if problem := validation.gcp_service_account(body.gcp.service_account_email.strip()):
                errors.append(FieldError("gcp.service_account_email", problem))
    return errors


@router.post("/cloud-connections", status_code=201, response_model=ConnectionOut)
def create_connection(
    body: CreateConnectionIn,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    authorize(
        db, principal, "providers:manage", action="provider_connection.created", resource_type="provider_connection"
    )
    ctx = idempotency.context(
        request,
        scope="POST /cloud-connections",
        payload=body.model_dump(mode="json"),
        organization_id=principal.organization_id,
        user_id=principal.user.id,
    )
    if (record := idempotency.lookup(db, ctx)) is not None:
        return respond(connection_out(_find(db, principal, record.resource_id), settings), record.status_code)

    errors = _validate_create(body, settings)
    if errors:
        raise invalid(errors)
    name = body.name.strip()
    duplicate = db.scalar(
        select(func.count())
        .select_from(ProviderConnection)
        .where(ProviderConnection.organization_id == principal.organization_id, ProviderConnection.name == name)
    )
    if duplicate:
        raise conflict(
            "duplicate_name",
            "A cloud connection with this name already exists.",
            [FieldError("name", "This name is already in use.")],
        )
    aws_details = (
        body.aws if body.provider == "aws_app_runner" else body.ecs if body.provider == "aws_ecs_express" else None
    )
    aws = aws_details is not None
    az = body.azure if body.provider == "azure_container_apps" else None
    gcp = body.gcp if body.provider == "gcp_cloud_run" else None
    k8s = body.kubernetes if body.provider == "kubernetes" else None
    aks = k8s is not None and k8s.auth == "aks"
    conn = ProviderConnection(
        organization_id=principal.organization_id,
        name=name,
        provider=body.provider,
        default_region=body.default_region,
        status="pending_validation",
        account_id=aws_details.account_id.strip() if aws_details else None,
        role_arn=aws_details.role_arn.strip() if aws_details else None,
        project_id=gcp.project_id.strip() if gcp else None,
        workload_identity_provider=gcp.workload_identity_provider.strip() if gcp else None,
        service_account_email=gcp.service_account_email.strip() if gcp else None,
        tenant_id=az.tenant_id.strip().lower()
        if az
        else (k8s.tenant_id or "").strip().lower() or None
        if aks and k8s
        else None,
        subscription_id=az.subscription_id.strip().lower() if az else None,
        client_id=az.client_id.strip().lower()
        if az
        else (k8s.client_id or "").strip().lower() or None
        if aks and k8s
        else None,
        resource_group=az.resource_group.strip() if az else None,
        environment_name=az.environment_name.strip() if az else None,
        registry_name=(az.registry_name or "").strip().lower() or None if az else None,
        ecs_cluster=((body.ecs.cluster or "").strip() or "default")
        if body.provider == "aws_ecs_express" and body.ecs
        else None,
        api_server=k8s.api_server.strip().rstrip("/") if k8s else None,
        ca_certificate=k8s.ca_certificate.strip() if k8s else None,
        namespace=k8s.namespace.strip() if k8s else None,
        kubernetes_auth=k8s.auth if k8s else None,
        token_audience=(k8s.audience or "").strip() or None if k8s and not aks else None,
        # A unique, unguessable external ID per connection (confused-deputy protection for sts:AssumeRole).
        external_id=f"cb-{secrets.token_hex(16)}" if aws else None,
        trusted_principal=trusted_principal(body.provider, settings),
        created_at=clock.utcnow(),
    )
    db.add(conn)
    db.flush()
    idempotency.remember(db, ctx, status_code=201, resource_type="provider_connection", resource_id=conn.id)
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action="provider_connection.created",
        resource_type="provider_connection",
        resource_id=conn.id,
        resource_name=conn.name,
        request_id=principal.request_id,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if (record := idempotency.lookup(db, ctx)) is not None:
            return respond(connection_out(_find(db, principal, record.resource_id), settings), record.status_code)
        raise conflict(
            "duplicate_name",
            "A cloud connection with this name already exists.",
            [FieldError("name", "This name is already in use.")],
        ) from None
    return respond(connection_out(conn, settings), 201)


@router.post("/cloud-connections/{connection_id}/test", status_code=202, response_model=ConnectionOut)
def test_connection(
    connection_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    conn = _find(db, principal, connection_id)
    authorize(
        db,
        principal,
        "providers:manage",
        action="provider_connection.validation_started",
        resource_type="provider_connection",
        resource_id=conn.id,
        resource_name=conn.name,
    )
    if conn.status != "validating":
        now = clock.utcnow()
        conn.status = "validating"
        conn.validation = {
            "stage": None,
            "started_at": clock.iso(now),
            "finished_at": None,
            "checks": [],
            "error_code": None,
        }
        conn.validation_requested_by_id = principal.user.id
        conn.locked_until = None
        audit.record(
            db,
            organization_id=principal.organization_id,
            actor=principal.user,
            action="provider_connection.validation_started",
            resource_type="provider_connection",
            resource_id=conn.id,
            resource_name=conn.name,
            request_id=principal.request_id,
        )
        db.commit()
        wake_worker(request)
    return respond(connection_out(conn, settings), 202)


@router.delete("/cloud-connections/{connection_id}", status_code=204)
def delete_connection(
    connection_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> Response:
    conn = _find(db, principal, connection_id)
    authorize(
        db,
        principal,
        "providers:manage",
        action="provider_connection.deleted",
        resource_type="provider_connection",
        resource_id=conn.id,
        resource_name=conn.name,
    )
    in_use = db.scalar(
        select(func.count())
        .select_from(Application)
        .where(Application.connection_id == conn.id, Application.deleted_at.is_(None))
    )
    if in_use:
        raise conflict("connection_in_use", "Applications still use this connection. Delete or move them first.")
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action="provider_connection.deleted",
        resource_type="provider_connection",
        resource_id=conn.id,
        resource_name=conn.name,
        request_id=principal.request_id,
    )
    db.delete(conn)
    db.commit()
    return Response(status_code=204)
