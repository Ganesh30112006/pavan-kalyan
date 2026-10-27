"""ORM rows -> wire models. Kept in one place so the shapes stay aligned with the frontend contract."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.config import Settings
from app.models import (
    Application,
    ApplicationSpec,
    AuditEvent,
    Deployment,
    DeploymentEvent,
    Organization,
    ProviderConnection,
    User,
)
from app.schemas import (
    AdjustmentOut,
    AllowedActionsOut,
    ApplicationConfigurationOut,
    ApplicationOut,
    ApplicationSummaryOut,
    AuditActorOut,
    AuditEventOut,
    AwsSetupOut,
    ConnectionConfigOut,
    ConnectionOut,
    ConnectionSetupOut,
    ConnectionValidationOut,
    DeploymentEventOut,
    DeploymentOut,
    DeploymentRefOut,
    DeploymentSummaryOut,
    EffectiveConfigurationOut,
    EnvironmentVariableOut,
    FailureOut,
    Features,
    MemberOut,
    OrganizationOut,
    OrganizationRef,
    Preferences,
    SecretReferenceOut,
    SessionOut,
    SourceOut,
    StageOut,
    UserOut,
    ValidationCheckOut,
)
from app.services.deployments import is_approvable, is_cancellable, is_rollback_target


def _time(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        preferences=Preferences(timezone=user.timezone, time_format=user.time_format),
    )


def session_out(principal: Principal, settings: Settings) -> SessionOut:
    return SessionOut(
        user=user_out(principal.user),
        organization=OrganizationRef(id=principal.organization.id, name=principal.organization.name),
        permissions=sorted(principal.permissions),
        features=Features(organization_settings=True, notification_preferences=settings.notifications_enabled),
    )


def organization_out(org: Organization) -> OrganizationOut:
    return OrganizationOut(id=org.id, name=org.name, created_at=org.created_at)


def member_out(user: User) -> MemberOut:
    return MemberOut(
        id=user.id,
        display_name=user.display_name,
        email=user.email,
        role=user.role,
        joined_at=user.created_at,
    )


# ---------- Applications ----------


def latest_deployments(db: Session, apps: list[Application]) -> dict[str, Deployment]:
    ids = [a.latest_deployment_id for a in apps if a.latest_deployment_id]
    if not ids:
        return {}
    rows = db.scalars(select(Deployment).where(Deployment.id.in_(ids))).all()
    return {d.id: d for d in rows}


def application_summary(app: Application, latest: Deployment | None) -> ApplicationSummaryOut:
    return ApplicationSummaryOut(**_summary_fields(app, latest))


def _summary_fields(app: Application, latest: Deployment | None) -> dict[str, Any]:
    return {
        "id": app.id,
        "name": app.name,
        "description": app.description,
        "application_type": app.application_type,
        "runtime": app.runtime,
        "provider": app.provider,
        "connection_id": app.connection_id,
        "region": app.region,
        "status": app.status,
        "health": app.health,
        "current_version": app.current_version,
        "latest_deployment": (
            DeploymentRefOut(id=latest.id, status=latest.status, created_at=latest.created_at) if latest else None
        ),
        "updated_at": app.updated_at,
    }


def application_out(db: Session, app: Application) -> ApplicationOut:
    latest = db.get(Deployment, app.latest_deployment_id) if app.latest_deployment_id else None
    return ApplicationOut(
        **_summary_fields(app, latest),
        source=SourceOut(
            type=app.source_type,
            image=app.source_image,
            repository_url=app.repository_url,
            ref_type=app.ref_type,
            git_ref=app.git_ref,
            dockerfile_path=app.dockerfile_path,
            build_context=app.build_context,
        ),
        image_digest=app.image_digest,
        service_url=app.service_url,
        created_at=app.created_at,
        created_by=app.created_by,
        environment=app.environment,
        promote_to_id=app.promote_to_id,
        preview_of_id=app.preview_of_id,
        preview_number=app.preview_number,
        paused=app.paused,
        requires_approval=app.requires_approval,
        auto_deploy=app.auto_deploy,
    )


def configuration_out(spec: ApplicationSpec) -> ApplicationConfigurationOut:
    effective = None
    if spec.effective:
        e = spec.effective
        effective = EffectiveConfigurationOut(
            port=int(e["port"]),
            cpu=str(e["cpu"]),
            memory=str(e["memory"]),
            min_instances=int(e["min_instances"]),
            max_instances=int(e["max_instances"]),
            concurrency=None if e.get("concurrency") is None else int(e["concurrency"]),
            exposure=e["exposure"],
            health_check_path=e.get("health_check_path"),
            observed_at=_time(e["observed_at"]),
        )
    return ApplicationConfigurationOut(
        application_id=spec.application_id,
        version=spec.version,
        port=spec.port,
        cpu=spec.cpu,
        memory=spec.memory,
        min_instances=spec.min_instances,
        max_instances=spec.max_instances,
        concurrency=spec.concurrency,
        exposure=spec.exposure,
        health_check_path=spec.health_check_path,
        environment=[EnvironmentVariableOut(name=e["name"], value=e["value"]) for e in spec.environment],
        secret_refs=[SecretReferenceOut(name=s["name"], reference=s["reference"]) for s in spec.secret_refs],
        updated_at=spec.updated_at,
        effective=effective,
        adjustments=[AdjustmentOut(**a) for a in spec.adjustments],
    )


# ---------- Deployments ----------


def deployment_summary(dep: Deployment) -> DeploymentSummaryOut:
    return DeploymentSummaryOut(**_deployment_fields(dep))


def _deployment_fields(dep: Deployment) -> dict[str, Any]:
    return {
        "id": dep.id,
        "application_id": dep.application_id,
        "application_name": dep.application_name,
        "provider": dep.provider,
        "region": dep.region,
        "status": dep.status,
        "kind": dep.kind,
        "version": dep.version,
        "image_digest": dep.image_digest,
        "source_ref": dep.source_ref,
        "initiated_by": dep.initiated_by,
        "created_at": dep.created_at,
        "finished_at": dep.finished_at,
        "trigger": dep.trigger or "manual",
    }


def deployment_out(dep: Deployment, app: Application | None, principal: Principal) -> DeploymentOut:
    rollback = app is not None and principal.can("deployments:rollback") and is_rollback_target(dep, app)
    cancel = principal.can("deployments:cancel") and is_cancellable(dep)
    approve = principal.can("deployments:approve") and is_approvable(dep)
    return DeploymentOut(
        **_deployment_fields(dep),
        rollback_of=dep.rollback_of,
        started_at=dep.started_at,
        stages=[
            StageOut(
                name=s["name"],
                status=s["status"],
                started_at=_time(s.get("started_at")),
                finished_at=_time(s.get("finished_at")),
            )
            for s in dep.stages
        ],
        failure=FailureOut(code=dep.failure_code, message=dep.failure_message or "The deployment failed.")
        if dep.failure_code
        else None,
        service_url=dep.service_url,
        progress_percent=None,
        allowed_actions=AllowedActionsOut(rollback=rollback, cancel=cancel, approve=approve),
        approved_by=dep.approved_by,
        approved_at=dep.approved_at,
    )


def event_out(event: DeploymentEvent) -> DeploymentEventOut:
    return DeploymentEventOut(
        id=str(event.id),
        timestamp=event.timestamp,
        level=event.level,
        stage=event.stage,
        message=event.message,
    )


# ---------- Connections ----------


AZURE_TOKEN_AUDIENCE = "api://AzureADTokenExchange"


def trusted_principal(provider: str, settings: Settings) -> str | None:
    if provider in ("aws_app_runner", "aws_ecs_express"):
        return settings.aws_principal_arn
    if provider in ("azure_container_apps", "kubernetes"):
        return settings.azure_federation_subject
    return settings.gcp_trusted_principal


def federation_audience(conn: ProviderConnection) -> str | None:
    """The audience of CloudBridge's token: Entra ID's for Azure and AKS, the cluster's own for OIDC clusters."""
    if conn.provider == "azure_container_apps" or (conn.provider == "kubernetes" and conn.kubernetes_auth == "aks"):
        return AZURE_TOKEN_AUDIENCE
    if conn.provider == "kubernetes":
        return conn.token_audience
    return None


def aws_setup(provider: str, settings: Settings) -> AwsSetupOut | None:
    if provider not in ("aws_app_runner", "aws_ecs_express"):
        return None
    return AwsSetupOut(
        ecr_repository_prefix=settings.aws_ecr_repository_prefix,
        apprunner_access_role=settings.aws_apprunner_access_role_name,
        apprunner_instance_role=settings.aws_apprunner_instance_role_name,
        codebuild_role=settings.aws_codebuild_role_name,
        ecs_execution_role=settings.aws_ecs_execution_role_name,
        ecs_infrastructure_role=settings.aws_ecs_infrastructure_role_name,
    )


def provider_setup_out(provider: str, settings: Settings) -> ConnectionSetupOut:
    """What a customer's account must trust before any connection exists (Azure and AKS start here)."""
    federated = provider in ("azure_container_apps", "kubernetes")
    return ConnectionSetupOut(
        external_id=None,
        trusted_principal=trusted_principal(provider, settings),
        federation_issuer=settings.azure_federation_issuer if federated else None,
        federation_audience=AZURE_TOKEN_AUDIENCE if federated else None,
        aws=aws_setup(provider, settings),
    )


def connection_out(conn: ProviderConnection, settings: Settings) -> ConnectionOut:
    federated = conn.provider in ("azure_container_apps", "kubernetes")
    validation = None
    if conn.validation:
        v = conn.validation
        validation = ConnectionValidationOut(
            stage=v.get("stage"),
            started_at=_time(v.get("started_at")),
            finished_at=_time(v.get("finished_at")),
            checks=[
                ValidationCheckOut(name=c["name"], status=c["status"], detail=c.get("detail"))
                for c in v.get("checks", [])
            ],
            error_code=v.get("error_code"),
        )
    return ConnectionOut(
        id=conn.id,
        name=conn.name,
        provider=conn.provider,
        status=conn.status,
        default_region=conn.default_region,
        config=ConnectionConfigOut(
            account_id=conn.account_id,
            role_arn=conn.role_arn,
            project_id=conn.project_id,
            workload_identity_provider=conn.workload_identity_provider,
            service_account_email=conn.service_account_email,
            tenant_id=conn.tenant_id,
            subscription_id=conn.subscription_id,
            client_id=conn.client_id,
            resource_group=conn.resource_group,
            environment_name=conn.environment_name,
            registry_name=conn.registry_name,
            ecs_cluster=conn.ecs_cluster,
            api_server=conn.api_server,
            namespace=conn.namespace,
            kubernetes_auth=conn.kubernetes_auth if conn.kubernetes_auth in ("aks", "oidc") else None,
            token_audience=conn.token_audience,
        ),
        setup=ConnectionSetupOut(
            external_id=conn.external_id,
            trusted_principal=trusted_principal(conn.provider, settings) or conn.trusted_principal,
            federation_issuer=settings.azure_federation_issuer if federated else None,
            federation_audience=federation_audience(conn),
            aws=aws_setup(conn.provider, settings),
        ),
        validation=validation,
        created_at=conn.created_at,
        last_validated_at=conn.last_validated_at,
    )


# ---------- Audit ----------


def audit_out(event: AuditEvent) -> AuditEventOut:
    actor = None
    if event.actor_id:
        actor = AuditActorOut(
            id=event.actor_id, display_name=event.actor_display_name or "Unknown user", email=event.actor_email
        )
    return AuditEventOut(
        id=event.id,
        occurred_at=event.occurred_at,
        actor=actor,
        action=event.action,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        resource_name=event.resource_name,
        outcome=event.outcome,
        request_id=event.request_id,
    )
