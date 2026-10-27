"""Request and response bodies. Field names and shapes match docs/api-contract.md and
frontend/src/types/api.ts exactly; the frontend decodes every response against them at runtime.

Request models are strict (a string is never coerced into a number) and ignore unknown fields.
"""

from __future__ import annotations

import datetime as dt
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain import (
    AlertChannelKind,
    AppEnvironment,
    ApplicationType,
    AuditOutcome,
    CheckStatus,
    ConnectionStatus,
    DeploymentStatus,
    DeploymentTrigger,
    DomainStatus,
    Exposure,
    HealthStatus,
    Permission,
    ProviderKind,
    RuntimeType,
    StageName,
    StageStatus,
    UserRole,
    ValidationStage,
)

T = TypeVar("T")


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=False)


class In(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", str_strip_whitespace=False)


class Page(Out, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class ErrorField(Out):
    field: str
    message: str


class ErrorBody(Out):
    code: str
    message: str
    request_id: str | None
    fields: list[ErrorField] = []


class ErrorEnvelope(Out):
    error: ErrorBody


# ---------- Session ----------


class Preferences(Out):
    timezone: str | None
    time_format: Literal["12h", "24h"]


class UserOut(Out):
    id: str
    email: str
    display_name: str
    role: UserRole
    preferences: Preferences


class OrganizationRef(Out):
    id: str
    name: str


class Features(Out):
    organization_settings: bool
    notification_preferences: bool


class SessionOut(Out):
    user: UserOut
    organization: OrganizationRef
    permissions: list[Permission]
    features: Features


class LoginIn(In):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


# ---------- Providers ----------


class FeaturesOut(Out):
    repository_builds: bool
    metrics: bool
    custom_domains: bool
    pause: bool


class CapabilitiesOut(Out):
    provider: ProviderKind
    regions: list[str]
    cpu_options: list[str]
    memory_options: list[str]
    min_instances_floor: int
    max_instances_limit: int
    supports_concurrency: bool
    max_concurrency: int | None
    supports_private_exposure: bool
    supported_application_types: list[ApplicationType]
    supports_secret_references: bool
    notes: list[str]
    features: FeaturesOut


class ValidationCheckOut(Out):
    name: str
    status: CheckStatus
    detail: str | None


class ConnectionValidationOut(Out):
    stage: ValidationStage | None
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    checks: list[ValidationCheckOut]
    error_code: str | None


class AwsSetupOut(Out):
    """AWS: the names CloudBridge uses in the customer's account (the setup script creates these roles)."""

    ecr_repository_prefix: str
    apprunner_access_role: str
    apprunner_instance_role: str
    codebuild_role: str
    ecs_execution_role: str
    ecs_infrastructure_role: str


class ConnectionSetupOut(Out):
    external_id: str | None
    trusted_principal: str | None
    federation_issuer: str | None = None
    """Azure: the issuer the customer's federated credential must trust (trusted_principal is its subject)."""
    federation_audience: str | None = None
    aws: AwsSetupOut | None = None


class ConnectionConfigOut(Out):
    account_id: str | None
    role_arn: str | None
    project_id: str | None
    workload_identity_provider: str | None
    service_account_email: str | None
    tenant_id: str | None = None
    subscription_id: str | None = None
    client_id: str | None = None
    resource_group: str | None = None
    environment_name: str | None = None
    registry_name: str | None = None
    ecs_cluster: str | None = None
    api_server: str | None = None
    namespace: str | None = None
    kubernetes_auth: Literal["aks", "oidc"] | None = None
    token_audience: str | None = None


class ConnectionOut(Out):
    id: str
    name: str
    provider: ProviderKind
    status: ConnectionStatus
    default_region: str
    config: ConnectionConfigOut
    setup: ConnectionSetupOut | None
    validation: ConnectionValidationOut | None
    created_at: dt.datetime
    last_validated_at: dt.datetime | None


class AwsConnectionIn(In):
    account_id: str = Field(max_length=64)
    role_arn: str = Field(max_length=2048)


class GcpConnectionIn(In):
    project_id: str = Field(max_length=64)
    workload_identity_provider: str = Field(max_length=300)
    service_account_email: str = Field(max_length=320)


class AzureConnectionIn(In):
    tenant_id: str = Field(max_length=64)
    subscription_id: str = Field(max_length=64)
    client_id: str = Field(max_length=64)
    resource_group: str = Field(max_length=120)
    environment_name: str = Field(max_length=120)
    registry_name: str | None = Field(default=None, max_length=120)


class EcsConnectionIn(AwsConnectionIn):
    cluster: str | None = Field(default=None, max_length=255)


class KubernetesConnectionIn(In):
    api_server: str = Field(max_length=512)
    ca_certificate: str = Field(max_length=20000)
    namespace: str = Field(max_length=100)
    auth: Literal["aks", "oidc"]
    tenant_id: str | None = Field(default=None, max_length=64)
    client_id: str | None = Field(default=None, max_length=64)
    audience: str | None = Field(default=None, max_length=200)


class CreateConnectionIn(In):
    name: str = Field(max_length=200)
    provider: ProviderKind
    default_region: str = Field(max_length=64)
    aws: AwsConnectionIn | None = None
    gcp: GcpConnectionIn | None = None
    azure: AzureConnectionIn | None = None
    ecs: EcsConnectionIn | None = None
    kubernetes: KubernetesConnectionIn | None = None


# ---------- Applications ----------


class SourceIO(In):
    type: Literal["image", "repository"]
    image: str | None = Field(default=None, max_length=600)
    repository_url: str | None = Field(default=None, max_length=600)
    ref_type: Literal["branch", "commit"] | None = None
    git_ref: str | None = Field(default=None, max_length=300)
    dockerfile_path: str | None = Field(default=None, max_length=300)
    build_context: str | None = Field(default=None, max_length=300)


class EnvironmentVariableIO(In):
    name: str = Field(max_length=200)
    value: str = Field(max_length=4096)


class SecretReferenceIO(In):
    name: str = Field(max_length=200)
    reference: str = Field(max_length=2048)


class RequestedConfigurationIn(In):
    port: int
    cpu: str = Field(max_length=16)
    memory: str = Field(max_length=16)
    min_instances: int
    max_instances: int
    concurrency: int | None = None
    exposure: Exposure
    health_check_path: str | None = Field(default=None, max_length=300)
    environment: list[EnvironmentVariableIO] = Field(default_factory=list, max_length=100)
    secret_refs: list[SecretReferenceIO] = Field(default_factory=list, max_length=50)

    @field_validator("port")
    @classmethod
    def _port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("Container port must be between 1 and 65535.")
        return value

    @field_validator("min_instances")
    @classmethod
    def _min(cls, value: int) -> int:
        if not 0 <= value <= 1000:
            raise ValueError("Minimum instances must be between 0 and 1000.")
        return value

    @field_validator("max_instances")
    @classmethod
    def _max(cls, value: int) -> int:
        if not 1 <= value <= 1000:
            raise ValueError("Maximum instances must be between 1 and 1000.")
        return value

    @field_validator("concurrency")
    @classmethod
    def _concurrency(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 1000:
            raise ValueError("Request concurrency must be between 1 and 1000.")
        return value


class CreateApplicationIn(RequestedConfigurationIn):
    name: str = Field(max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    application_type: ApplicationType
    runtime: RuntimeType
    connection_id: str = Field(max_length=64)
    region: str = Field(max_length=64)
    source: SourceIO


class UpdateConfigurationIn(RequestedConfigurationIn):
    version: int


class DeploymentRefOut(Out):
    id: str
    status: DeploymentStatus
    created_at: dt.datetime


class ApplicationSummaryOut(Out):
    id: str
    name: str
    description: str | None
    application_type: ApplicationType
    runtime: RuntimeType
    provider: ProviderKind
    connection_id: str
    region: str
    status: str
    health: HealthStatus
    current_version: str | None
    latest_deployment: DeploymentRefOut | None
    updated_at: dt.datetime


class SourceOut(Out):
    type: Literal["image", "repository"]
    image: str | None
    repository_url: str | None
    ref_type: Literal["branch", "commit"] | None
    git_ref: str | None
    dockerfile_path: str | None
    build_context: str | None


class ApplicationOut(ApplicationSummaryOut):
    source: SourceOut
    image_digest: str | None
    service_url: str | None
    created_at: dt.datetime
    created_by: str | None
    environment: AppEnvironment
    promote_to_id: str | None
    preview_of_id: str | None
    preview_number: int | None
    paused: bool
    requires_approval: bool
    auto_deploy: bool


class EnvironmentVariableOut(Out):
    name: str
    value: str


class SecretReferenceOut(Out):
    name: str
    reference: str


class EffectiveConfigurationOut(Out):
    port: int
    cpu: str
    memory: str
    min_instances: int
    max_instances: int
    concurrency: int | None
    exposure: Exposure
    health_check_path: str | None
    observed_at: dt.datetime


class AdjustmentOut(Out):
    field: str
    requested: str
    effective: str
    reason: str


class ApplicationConfigurationOut(Out):
    application_id: str
    version: int
    port: int
    cpu: str
    memory: str
    min_instances: int
    max_instances: int
    concurrency: int | None
    exposure: Exposure
    health_check_path: str | None
    environment: list[EnvironmentVariableOut]
    secret_refs: list[SecretReferenceOut]
    updated_at: dt.datetime
    effective: EffectiveConfigurationOut | None
    adjustments: list[AdjustmentOut]


class SpecIssueOut(Out):
    field: str | None
    severity: Literal["error", "warning"]
    message: str


class SpecValidationOut(Out):
    valid: bool
    issues: list[SpecIssueOut]
    adjustments: list[AdjustmentOut]


class LogEntryOut(Out):
    timestamp: dt.datetime
    stream: Literal["stdout", "stderr", "system"]
    message: str


class LogPageOut(Out):
    entries: list[LogEntryOut]
    truncated: bool


# ---------- Deployments ----------


class DeploymentSummaryOut(Out):
    id: str
    application_id: str
    application_name: str
    provider: ProviderKind
    region: str | None
    status: DeploymentStatus
    kind: Literal["deploy", "rollback"]
    version: str | None
    image_digest: str | None
    source_ref: str | None
    initiated_by: str | None
    created_at: dt.datetime
    finished_at: dt.datetime | None
    trigger: DeploymentTrigger


class StageOut(Out):
    name: StageName
    status: StageStatus
    started_at: dt.datetime | None
    finished_at: dt.datetime | None


class FailureOut(Out):
    code: str
    message: str


class AllowedActionsOut(Out):
    rollback: bool
    cancel: bool
    approve: bool


class DeploymentOut(DeploymentSummaryOut):
    rollback_of: str | None
    started_at: dt.datetime | None
    stages: list[StageOut]
    failure: FailureOut | None
    service_url: str | None
    progress_percent: float | None
    allowed_actions: AllowedActionsOut
    approved_by: str | None
    approved_at: dt.datetime | None


class DeploymentEventOut(Out):
    id: str
    timestamp: dt.datetime
    level: Literal["info", "warning", "error"]
    stage: StageName | None
    message: str


# ---------- Audit ----------


class AuditActorOut(Out):
    id: str
    display_name: str
    email: str | None


class AuditEventOut(Out):
    id: str
    occurred_at: dt.datetime
    actor: AuditActorOut | None
    action: str
    resource_type: str
    resource_id: str | None
    resource_name: str | None
    outcome: AuditOutcome
    request_id: str | None


# ---------- Settings ----------


class PreferencesIn(In):
    timezone: str | None = Field(default=None, max_length=64)
    time_format: Literal["12h", "24h"]


class UpdateProfileIn(In):
    display_name: str = Field(max_length=200)
    preferences: PreferencesIn


class OrganizationOut(Out):
    id: str
    name: str
    created_at: dt.datetime


class UpdateOrganizationIn(In):
    name: str = Field(max_length=200)


class MemberOut(Out):
    id: str
    display_name: str
    email: str
    role: UserRole
    joined_at: dt.datetime


class NotificationPreferencesIO(In):
    email_enabled: bool
    events: dict[str, bool]


# ---------- Automation: auto-deploy, previews, approvals, environments ----------


class WebhookOut(Out):
    url: str
    secret_configured: bool


class AutomationOut(Out):
    environment: AppEnvironment
    promote_to_id: str | None
    auto_deploy: bool
    auto_deploy_branch: str | None
    previews_enabled: bool
    config_from_repo: bool
    requires_approval: bool
    webhook: WebhookOut


class AutomationIn(In):
    environment: AppEnvironment
    promote_to_id: str | None = Field(default=None, max_length=64)
    auto_deploy: bool
    auto_deploy_branch: str | None = Field(default=None, max_length=255)
    previews_enabled: bool
    config_from_repo: bool
    requires_approval: bool


class WebhookSecretOut(Out):
    url: str
    secret: str
    """Shown once. GitHub signs each delivery with it; CloudBridge rejects unsigned or mis-signed ones."""


class ConfigFileIn(In):
    content: str = Field(max_length=70000)
    version: int


# ---------- Sleep schedules and pausing ----------


class ScheduleIO(In):
    timezone: str = Field(max_length=64)
    days: list[int] = Field(max_length=7)
    sleep_at: str = Field(max_length=5)
    wake_at: str = Field(max_length=5)


class ScheduleOut(Out):
    schedule: ScheduleIO | None
    paused: bool
    paused_by_schedule: bool
    asleep_now: bool


# ---------- Metrics and cost ----------


class MetricPointOut(Out):
    timestamp: dt.datetime
    value: float


class MetricSeriesOut(Out):
    name: Literal["requests", "errors_4xx", "errors_5xx", "latency_ms", "instances", "cpu_percent", "memory_percent"]
    unit: str
    points: list[MetricPointOut]


class MetricsOut(Out):
    range: Literal["1h", "6h", "24h", "7d"]
    step_seconds: int
    series: list[MetricSeriesOut]
    unavailable_reason: str | None


class CostLineOut(Out):
    item: str
    low: float
    high: float


class CostEstimateOut(Out):
    provider: ProviderKind
    currency: Literal["USD"]
    monthly_low: float | None
    monthly_high: float | None
    lines: list[CostLineOut]
    assumptions: list[str]


class CostEstimateIn(In):
    provider: ProviderKind
    cpu: str = Field(max_length=16)
    memory: str = Field(max_length=16)
    min_instances: int
    max_instances: int


# ---------- Custom domains ----------


class DnsRecordOut(Out):
    type: str
    name: str
    value: str
    purpose: str


class DomainOut(Out):
    id: str
    domain: str
    status: DomainStatus
    records: list[DnsRecordOut]
    message: str | None
    created_at: dt.datetime
    checked_at: dt.datetime | None


class DomainIn(In):
    domain: str = Field(max_length=253)


# ---------- Alert channels ----------


class AlertChannelOut(Out):
    id: str
    name: str
    kind: AlertChannelKind
    host: str
    events: list[str]
    enabled: bool
    signed: bool
    created_at: dt.datetime
    last_delivery_at: dt.datetime | None
    last_delivery_status: str | None


class AlertChannelIn(In):
    name: str = Field(max_length=200)
    kind: AlertChannelKind
    url: str | None = Field(default=None, max_length=2048)
    """Required when creating; omit when updating to keep the current address."""
    events: list[str] = Field(max_length=20)
    enabled: bool = True


class AlertChannelCreatedOut(AlertChannelOut):
    signing_secret: str | None
    """Generic webhooks only: shown once, used to verify the X-CloudBridge-Signature header."""


class AlertTestOut(Out):
    delivered: bool
    status: str


# ---------- API tokens ----------


class ApiTokenOut(Out):
    id: str
    name: str
    prefix: str
    created_at: dt.datetime
    last_used_at: dt.datetime | None
    expires_at: dt.datetime | None


class ApiTokenIn(In):
    name: str = Field(max_length=200)
    expires_in_days: int | None = None


class ApiTokenCreatedOut(ApiTokenOut):
    token: str
    """Shown once. Send it as `Authorization: Bearer <token>`."""
