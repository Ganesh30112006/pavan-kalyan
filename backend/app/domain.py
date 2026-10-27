"""Vocabulary shared by the API, the worker and the frontend contract (docs/api-contract.md)."""

from __future__ import annotations

from typing import Final, Literal, get_args

ProviderKind = Literal["aws_app_runner", "gcp_cloud_run", "azure_container_apps", "aws_ecs_express", "kubernetes"]
PROVIDER_KINDS: Final[tuple[str, ...]] = get_args(ProviderKind)
PROVIDER_LABELS: Final[dict[str, str]] = {
    "aws_app_runner": "AWS App Runner",
    "gcp_cloud_run": "Google Cloud Run",
    "azure_container_apps": "Azure Container Apps",
    "aws_ecs_express": "AWS ECS Express Mode",
    "kubernetes": "Kubernetes",
}

UserRole = Literal["owner", "admin", "developer", "viewer"]
USER_ROLES: Final[tuple[str, ...]] = get_args(UserRole)

Permission = Literal[
    "applications:create",
    "applications:update",
    "applications:delete",
    "deployments:create",
    "deployments:cancel",
    "deployments:rollback",
    "deployments:approve",
    "providers:manage",
    "audit:read",
    "organization:manage",
]
ALL_PERMISSIONS: Final[tuple[Permission, ...]] = get_args(Permission)

ROLE_PERMISSIONS: Final[dict[str, tuple[Permission, ...]]] = {
    "owner": ALL_PERMISSIONS,
    "admin": tuple(p for p in ALL_PERMISSIONS if p != "organization:manage"),
    "developer": (
        "applications:create",
        "applications:update",
        "deployments:create",
        "deployments:cancel",
        "deployments:rollback",
        "audit:read",
    ),
    "viewer": (),
}

ApplicationType = Literal["web_service", "api", "worker"]
RuntimeType = Literal["node", "python", "java", "go", "dotnet", "ruby", "php", "other"]
ApplicationStatus = Literal["draft", "deploying", "running", "failed", "stopped", "deleting"]
AppEnvironment = Literal["production", "staging", "preview"]
APP_ENVIRONMENTS: Final[tuple[str, ...]] = get_args(AppEnvironment)
DeploymentTrigger = Literal["manual", "push", "pull_request", "promotion", "api"]
APPLICATION_STATUSES: Final[tuple[str, ...]] = get_args(ApplicationStatus)
HealthStatus = Literal["healthy", "degraded", "unhealthy", "unknown"]
HEALTH_STATUSES: Final[tuple[str, ...]] = get_args(HealthStatus)
Exposure = Literal["public", "private"]

DeploymentStatus = Literal[
    "awaiting_approval",
    "queued",
    "validating",
    "building",
    "publishing",
    "deploying",
    "verifying",
    "succeeded",
    "failed",
    "rolling_back",
    "rolled_back",
    "failed_rollback",
    "cancelled",
]
DEPLOYMENT_STATUSES: Final[tuple[str, ...]] = get_args(DeploymentStatus)
ACTIVE_DEPLOYMENT_STATUSES: Final[frozenset[str]] = frozenset(
    {"queued", "validating", "building", "publishing", "deploying", "verifying", "rolling_back"}
)
CANCELLABLE_DEPLOYMENT_STATUSES: Final[frozenset[str]] = frozenset(
    {"awaiting_approval", "queued", "validating", "building", "publishing"}
)

StageName = Literal["queued", "validation", "build", "publishing", "provisioning", "verification"]
STAGE_NAMES: Final[tuple[StageName, ...]] = get_args(StageName)
StageStatus = Literal["pending", "active", "succeeded", "failed", "cancelled", "skipped"]

ConnectionStatus = Literal[
    "pending_validation",
    "validating",
    "connected",
    "invalid_credentials",
    "insufficient_permissions",
    "provider_unavailable",
    "timed_out",
    "disconnected",
    "error",
]
ValidationStage = Literal["connecting", "validating_permissions", "checking_readiness"]
CheckStatus = Literal["pending", "passed", "failed", "skipped"]

AuditOutcome = Literal["success", "failure", "denied"]
AUDIT_OUTCOMES: Final[tuple[str, ...]] = get_args(AuditOutcome)

NotificationEvent = Literal[
    "deployment_succeeded",
    "deployment_failed",
    "rollback_completed",
    "provider_connection_failed",
    "application_deleted",
    "application_unhealthy",
    "approval_requested",
]
NOTIFICATION_EVENTS: Final[tuple[str, ...]] = get_args(NotificationEvent)
DEFAULT_NOTIFICATION_EVENTS: Final[dict[str, bool]] = {
    "deployment_succeeded": False,
    "deployment_failed": True,
    "rollback_completed": True,
    "provider_connection_failed": True,
    "application_deleted": False,
    "application_unhealthy": True,
    "approval_requested": True,
}

AlertChannelKind = Literal["slack", "teams", "webhook"]
DomainStatus = Literal["pending_dns", "verifying", "active", "failed", "removing"]


def permissions_for(role: str) -> list[Permission]:
    return list(ROLE_PERMISSIONS.get(role, ()))
