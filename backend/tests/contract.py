"""Assertions that responses match the frontend contract (frontend/src/services/api/decoders.ts)
exactly: every field present, with the type the decoder expects."""

from __future__ import annotations

import datetime as dt
from typing import Any

ISO = "iso"


def _is_iso(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def check(value: Any, shape: Any, path: str = "") -> None:
    if isinstance(shape, dict):
        assert isinstance(value, dict), f"{path}: expected object, got {value!r}"
        assert set(value) == set(shape), f"{path}: keys {sorted(value)} != {sorted(shape)}"
        for key, sub in shape.items():
            check(value[key], sub, f"{path}.{key}")
    elif isinstance(shape, list):
        assert isinstance(value, list), f"{path}: expected array"
        for i, item in enumerate(value):
            check(item, shape[0], f"{path}[{i}]")
    elif isinstance(shape, tuple):  # alternatives
        errors = []
        for option in shape:
            try:
                check(value, option, path)
                return
            except AssertionError as error:
                errors.append(str(error))
        raise AssertionError(f"{path}: {value!r} matches none of {shape}: {errors}")
    elif shape is None:
        assert value is None, f"{path}: expected null"
    elif shape == ISO:
        assert _is_iso(value), f"{path}: expected ISO UTC timestamp, got {value!r}"
    elif isinstance(shape, set):
        assert value in shape, f"{path}: {value!r} not in {sorted(shape)}"
    elif shape is int:
        assert isinstance(value, int) and not isinstance(value, bool), f"{path}: expected integer, got {value!r}"
    else:
        assert isinstance(value, shape), f"{path}: expected {shape}, got {value!r}"


def opt(shape: Any) -> tuple[Any, None]:
    return (shape, None)


PROVIDERS = {"aws_app_runner", "gcp_cloud_run", "azure_container_apps", "aws_ecs_express", "kubernetes"}
DEPLOYMENT_STATUSES = {
    "awaiting_approval", "queued", "validating", "building", "publishing", "deploying", "verifying", "succeeded",
    "failed",
    "rolling_back", "rolled_back", "failed_rollback", "cancelled",
}  # fmt: skip
STAGES = {"queued", "validation", "build", "publishing", "provisioning", "verification"}

SESSION = {
    "user": {
        "id": str,
        "email": str,
        "display_name": str,
        "role": {"owner", "admin", "developer", "viewer"},
        "preferences": {"timezone": opt(str), "time_format": {"12h", "24h"}},
    },
    "organization": {"id": str, "name": str},
    "permissions": [str],
    "features": {"organization_settings": bool, "notification_preferences": bool},
}

APP_SUMMARY = {
    "id": str,
    "name": str,
    "description": opt(str),
    "application_type": {"web_service", "api", "worker"},
    "runtime": {"node", "python", "java", "go", "dotnet", "ruby", "php", "other"},
    "provider": PROVIDERS,
    "connection_id": str,
    "region": str,
    "status": {"draft", "deploying", "running", "failed", "stopped", "deleting"},
    "health": {"healthy", "degraded", "unhealthy", "unknown"},
    "current_version": opt(str),
    "latest_deployment": opt({"id": str, "status": DEPLOYMENT_STATUSES, "created_at": ISO}),
    "updated_at": ISO,
}
APPLICATION = {
    **APP_SUMMARY,
    "source": {
        "type": {"image", "repository"},
        "image": opt(str),
        "repository_url": opt(str),
        "ref_type": opt({"branch", "commit"}),
        "git_ref": opt(str),
        "dockerfile_path": opt(str),
        "build_context": opt(str),
    },
    "image_digest": opt(str),
    "service_url": opt(str),
    "created_at": ISO,
    "created_by": opt(str),
    "environment": {"production", "staging", "preview"},
    "promote_to_id": opt(str),
    "preview_of_id": opt(str),
    "preview_number": opt(int),
    "paused": bool,
    "requires_approval": bool,
    "auto_deploy": bool,
}
ADJUSTMENT = {"field": str, "requested": str, "effective": str, "reason": str}
CONFIGURATION = {
    "application_id": str,
    "version": int,
    "port": int,
    "cpu": str,
    "memory": str,
    "min_instances": int,
    "max_instances": int,
    "concurrency": opt(int),
    "exposure": {"public", "private"},
    "health_check_path": opt(str),
    "environment": [{"name": str, "value": str}],
    "secret_refs": [{"name": str, "reference": str}],
    "updated_at": ISO,
    "effective": opt(
        {
            "port": int,
            "cpu": str,
            "memory": str,
            "min_instances": int,
            "max_instances": int,
            "concurrency": opt(int),
            "exposure": {"public", "private"},
            "health_check_path": opt(str),
            "observed_at": ISO,
        }
    ),
    "adjustments": [ADJUSTMENT],
}
VALIDATION_RESULT = {
    "valid": bool,
    "issues": [{"field": opt(str), "severity": {"error", "warning"}, "message": str}],
    "adjustments": [ADJUSTMENT],
}
CAPABILITIES = {
    "provider": PROVIDERS,
    "regions": [str],
    "cpu_options": [str],
    "memory_options": [str],
    "min_instances_floor": int,
    "max_instances_limit": int,
    "supports_concurrency": bool,
    "max_concurrency": opt(int),
    "supports_private_exposure": bool,
    "supported_application_types": [{"web_service", "api", "worker"}],
    "supports_secret_references": bool,
    "notes": [str],
    "features": {"repository_builds": bool, "metrics": bool, "custom_domains": bool, "pause": bool},
}
LOG_PAGE = {
    "entries": [{"timestamp": ISO, "stream": {"stdout", "stderr", "system"}, "message": str}],
    "truncated": bool,
}
DEPLOYMENT_SUMMARY = {
    "id": str,
    "application_id": str,
    "application_name": str,
    "provider": PROVIDERS,
    "region": opt(str),
    "status": DEPLOYMENT_STATUSES,
    "kind": {"deploy", "rollback"},
    "version": opt(str),
    "image_digest": opt(str),
    "source_ref": opt(str),
    "initiated_by": opt(str),
    "created_at": ISO,
    "finished_at": opt(ISO),
    "trigger": {"manual", "push", "pull_request", "promotion", "api"},
}
DEPLOYMENT = {
    **DEPLOYMENT_SUMMARY,
    "rollback_of": opt(str),
    "started_at": opt(ISO),
    "stages": [
        {
            "name": STAGES,
            "status": {"pending", "active", "succeeded", "failed", "cancelled", "skipped"},
            "started_at": opt(ISO),
            "finished_at": opt(ISO),
        }
    ],
    "failure": opt({"code": str, "message": str}),
    "service_url": opt(str),
    "progress_percent": None,
    "allowed_actions": {"rollback": bool, "cancel": bool, "approve": bool},
    "approved_by": opt(str),
    "approved_at": opt(ISO),
}
EVENT = {"id": str, "timestamp": ISO, "level": {"info", "warning", "error"}, "stage": opt(STAGES), "message": str}
SETUP = {
    "external_id": opt(str),
    "trusted_principal": opt(str),
    "federation_issuer": opt(str),
    "federation_audience": opt(str),
    "aws": opt(
        {
            "ecr_repository_prefix": str,
            "apprunner_access_role": str,
            "apprunner_instance_role": str,
            "codebuild_role": str,
            "ecs_execution_role": str,
            "ecs_infrastructure_role": str,
        }
    ),
}
CONNECTION = {
    "id": str,
    "name": str,
    "provider": PROVIDERS,
    "status": {
        "pending_validation",
        "validating",
        "connected",
        "invalid_credentials",
        "insufficient_permissions",
        "provider_unavailable",
        "timed_out",
        "disconnected",
        "error",
    },  # fmt: skip
    "default_region": str,
    "config": {
        "account_id": opt(str),
        "role_arn": opt(str),
        "project_id": opt(str),
        "workload_identity_provider": opt(str),
        "service_account_email": opt(str),
        "tenant_id": opt(str),
        "subscription_id": opt(str),
        "client_id": opt(str),
        "resource_group": opt(str),
        "environment_name": opt(str),
        "registry_name": opt(str),
        "ecs_cluster": opt(str),
        "api_server": opt(str),
        "namespace": opt(str),
        "kubernetes_auth": opt({"aks", "oidc"}),
        "token_audience": opt(str),
    },
    "setup": opt(SETUP),
    "validation": opt(
        {
            "stage": opt({"connecting", "validating_permissions", "checking_readiness"}),
            "started_at": opt(ISO),
            "finished_at": opt(ISO),
            "checks": [{"name": str, "status": {"pending", "passed", "failed", "skipped"}, "detail": opt(str)}],
            "error_code": opt(str),
        }
    ),
    "created_at": ISO,
    "last_validated_at": opt(ISO),
}
AUDIT_EVENT = {
    "id": str,
    "occurred_at": ISO,
    "actor": opt({"id": str, "display_name": str, "email": opt(str)}),
    "action": str,
    "resource_type": str,
    "resource_id": opt(str),
    "resource_name": opt(str),
    "outcome": {"success", "failure", "denied"},
    "request_id": opt(str),
}
ERROR = {
    "error": {
        "code": str,
        "message": str,
        "request_id": opt(str),
        "fields": [{"field": str, "message": str}],
    }
}


def page(item: Any) -> dict[str, Any]:
    return {"items": [item], "total": int, "page": int, "page_size": int}


def field_errors(body: dict[str, Any]) -> dict[str, str]:
    check(body, ERROR)
    return {f["field"]: f["message"] for f in body["error"]["fields"]}


AUTOMATION = {
    "environment": {"production", "staging", "preview"},
    "promote_to_id": opt(str),
    "auto_deploy": bool,
    "auto_deploy_branch": opt(str),
    "previews_enabled": bool,
    "config_from_repo": bool,
    "requires_approval": bool,
    "webhook": {"url": str, "secret_configured": bool},
}
SCHEDULE = {
    "schedule": opt({"timezone": str, "days": [int], "sleep_at": str, "wake_at": str}),
    "paused": bool,
    "paused_by_schedule": bool,
    "asleep_now": bool,
}
METRICS = {
    "range": {"1h", "6h", "24h", "7d"},
    "step_seconds": int,
    "series": [
        {
            "name": {
                "requests",
                "errors_4xx",
                "errors_5xx",
                "latency_ms",
                "instances",
                "cpu_percent",
                "memory_percent",
            },
            "unit": str,
            "points": [{"timestamp": ISO, "value": float}],
        }
    ],
    "unavailable_reason": opt(str),
}
COST = {
    "provider": PROVIDERS,
    "currency": {"USD"},
    "monthly_low": opt(float),
    "monthly_high": opt(float),
    "lines": [{"item": str, "low": float, "high": float}],
    "assumptions": [str],
}
DOMAIN = {
    "id": str,
    "domain": str,
    "status": {"pending_dns", "verifying", "active", "failed", "removing"},
    "records": [{"type": str, "name": str, "value": str, "purpose": str}],
    "message": opt(str),
    "created_at": ISO,
    "checked_at": opt(ISO),
}
ALERT_CHANNEL = {
    "id": str,
    "name": str,
    "kind": {"slack", "teams", "webhook"},
    "host": str,
    "events": [str],
    "enabled": bool,
    "signed": bool,
    "created_at": ISO,
    "last_delivery_at": opt(ISO),
    "last_delivery_status": opt(str),
}
API_TOKEN = {
    "id": str,
    "name": str,
    "prefix": str,
    "created_at": ISO,
    "last_used_at": opt(ISO),
    "expires_at": opt(ISO),
}
