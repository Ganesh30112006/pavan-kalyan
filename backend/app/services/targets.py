"""How database rows are presented to provider adapters."""

from __future__ import annotations

from app.models import Application, ApplicationSpec, ProviderConnection
from app.providers.base import AppTarget, ConnectionInfo


def connection_info(conn: ProviderConnection, region: str | None = None) -> ConnectionInfo:
    """The connection, targeting `region` (its default region when omitted)."""
    return ConnectionInfo(
        id=conn.id,
        provider=conn.provider,
        region=region or conn.default_region,
        account_id=conn.account_id,
        role_arn=conn.role_arn,
        external_id=conn.external_id,
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
        ca_certificate=conn.ca_certificate,
        namespace=conn.namespace,
        kubernetes_auth=conn.kubernetes_auth,
        token_audience=conn.token_audience,
    )


def app_target(app: Application, spec: ApplicationSpec | None = None) -> AppTarget:
    return AppTarget(
        id=app.id,
        name=app.name,
        region=app.region,
        provider_service_id=app.provider_service_id,
        application_type=app.application_type,
        min_instances=spec.min_instances if spec is not None else 1,
    )
