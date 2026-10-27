"""Validation of requested application settings and comparison with what the provider runs.

Field paths match the frontend's keys (`source.image`, `environment.0.name`, `secret_refs.1.reference`)
so 422 errors land on the right inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app import validation
from app.config import Settings
from app.domain import PROVIDER_LABELS
from app.errors import FieldError
from app.providers.base import EffectiveConfig
from app.providers.capabilities import (
    Capabilities,
    Finding,
    SpecToCheck,
    capabilities_for,
    provider_rules,
    secret_reference_findings,
    spec_findings,
)
from app.schemas import RequestedConfigurationIn, SourceIO

GCP_RESERVED_ENV = frozenset(
    {
        "PORT",
        "K_SERVICE",
        "K_REVISION",
        "K_CONFIGURATION",
        "CLOUD_RUN_JOB",
        "CLOUD_RUN_EXECUTION",
        "CLOUD_RUN_TASK_INDEX",
        "CLOUD_RUN_TASK_ATTEMPT",
        "CLOUD_RUN_TASK_COUNT",
    }
)


@dataclass
class Requested:
    port: int
    cpu: str
    memory: str
    min_instances: int
    max_instances: int
    concurrency: int | None
    exposure: str
    health_check_path: str | None
    environment: list[dict[str, str]]
    secret_refs: list[dict[str, str]]

    def as_check(self, application_type: str, region: str) -> SpecToCheck:
        return SpecToCheck(
            application_type=application_type,
            region=region,
            cpu=self.cpu,
            memory=self.memory,
            min_instances=self.min_instances,
            max_instances=self.max_instances,
            concurrency=self.concurrency,
            exposure=self.exposure,
            secret_ref_count=len(self.secret_refs),
        )

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> Requested:
        return cls(
            port=int(data["port"]),
            cpu=str(data["cpu"]),
            memory=str(data["memory"]),
            min_instances=int(data["min_instances"]),
            max_instances=int(data["max_instances"]),
            concurrency=data.get("concurrency"),
            exposure=str(data["exposure"]),
            health_check_path=data.get("health_check_path"),
            environment=[dict(e) for e in data.get("environment", [])],
            secret_refs=[dict(s) for s in data.get("secret_refs", [])],
        )


def normalize_requested(body: RequestedConfigurationIn) -> tuple[Requested, list[FieldError]]:
    errors: list[FieldError] = []
    health = (body.health_check_path or "").strip() or None
    if health is not None and (problem := validation.health_check_path(health)):
        errors.append(FieldError("health_check_path", problem))
    if body.min_instances > body.max_instances:
        errors.append(FieldError("max_instances", "Maximum instances must be at least the minimum."))

    seen: set[str] = set()
    environment: list[dict[str, str]] = []
    for i, row in enumerate(body.environment):
        name = row.name.strip()
        if problem := validation.env_var_name(name):
            errors.append(FieldError(f"environment.{i}.name", f"Variable {i + 1}: {problem}"))
        elif name in seen:
            errors.append(FieldError(f"environment.{i}.name", f"Variable {i + 1}: {name} is defined more than once."))
        elif problem := validation.plain_credential(name, row.value):
            errors.append(FieldError(f"environment.{i}.value", f"Variable {i + 1}: {problem}"))
        seen.add(name)
        environment.append({"name": name, "value": row.value})

    secret_refs: list[dict[str, str]] = []
    for i, secret in enumerate(body.secret_refs):
        name = secret.name.strip()
        reference = secret.reference.strip()
        if problem := validation.env_var_name(name):
            errors.append(FieldError(f"secret_refs.{i}.name", f"Secret {i + 1}: {problem}"))
        elif name in seen:
            errors.append(
                FieldError(
                    f"secret_refs.{i}.name", f"Secret {i + 1}: {name} is already used by another variable or secret."
                )
            )
        seen.add(name)
        if problem := validation.secret_reference(reference):
            errors.append(FieldError(f"secret_refs.{i}.reference", f"Secret {i + 1}: {problem}"))
        secret_refs.append({"name": name, "reference": reference})

    requested = Requested(
        port=body.port,
        cpu=body.cpu.strip(),
        memory=body.memory.strip(),
        min_instances=body.min_instances,
        max_instances=body.max_instances,
        concurrency=body.concurrency,
        exposure=body.exposure,
        health_check_path=health,
        environment=environment,
        secret_refs=secret_refs,
    )
    return requested, errors


def normalize_source(source: SourceIO) -> tuple[dict[str, Any], list[FieldError]]:
    errors: list[FieldError] = []

    def check(field: str, value: str, problem: str | None) -> str:
        if problem:
            errors.append(FieldError(f"source.{field}", problem))
        return value

    if source.type == "image":
        image = (source.image or "").strip()
        check("image", image, validation.container_image(image))
        return {
            "type": "image",
            "image": image,
            "repository_url": None,
            "ref_type": None,
            "git_ref": None,
            "dockerfile_path": None,
            "build_context": None,
        }, errors

    url = (source.repository_url or "").strip()
    check("repository_url", url, "Repository URL is required." if not url else validation.repository_url(url))
    ref_type = source.ref_type or "branch"
    git_ref = (source.git_ref or "").strip()
    check(
        "git_ref", git_ref, validation.git_commit(git_ref) if ref_type == "commit" else validation.git_branch(git_ref)
    )
    dockerfile = (source.dockerfile_path or "").strip()
    check("dockerfile_path", dockerfile, validation.dockerfile_path(dockerfile))
    context = (source.build_context or "").strip()
    check("build_context", context, validation.build_context(context))
    return {
        "type": "repository",
        "image": None,
        "repository_url": url,
        "ref_type": ref_type,
        "git_ref": git_ref.lower() if ref_type == "commit" else git_ref,
        "dockerfile_path": dockerfile,
        "build_context": context,
    }, errors


def env_name_findings(provider: str, requested: Requested) -> list[Finding]:
    findings: list[Finding] = []
    name = PROVIDER_LABELS[provider]
    rows = [("environment", i, e["name"]) for i, e in enumerate(requested.environment)] + [
        ("secret_refs", i, s["name"]) for i, s in enumerate(requested.secret_refs)
    ]
    for group, i, var in rows:
        label = "Variable" if group == "environment" else "Secret"
        reserved = (
            (provider == "gcp_cloud_run" and var.upper() in GCP_RESERVED_ENV)
            or (provider == "aws_app_runner" and var.upper().startswith("AWSAPPRUNNER"))
            or (provider == "azure_container_apps" and var.upper().startswith("CONTAINER_APP_"))
            or (provider == "aws_ecs_express" and var.upper().startswith("ECS_"))
        )
        if reserved:
            findings.append(Finding(f"{group}.{i}.name", "error", f"{label} {i + 1}: {var} is reserved by {name}."))
    return findings


def findings_for(
    settings: Settings,
    provider: str,
    application_type: str,
    region: str,
    requested: Requested,
    image: str | None,
) -> list[Finding]:
    caps = capabilities_for(provider, settings)
    return (
        spec_findings(requested.as_check(application_type, region), caps)
        + provider_rules(provider, requested.cpu, requested.memory, image)
        + secret_reference_findings(provider, [s["reference"] for s in requested.secret_refs])
        + env_name_findings(provider, requested)
        + worker_findings(application_type, requested)
    )


def worker_findings(application_type: str, requested: Requested) -> list[Finding]:
    """Background workers have no web address, and nothing would start one that scaled to zero."""
    if application_type != "worker":
        return []
    findings: list[Finding] = []
    if requested.exposure != "private":
        findings.append(
            Finding("exposure", "error", "Background workers have no web address; set exposure to private.")
        )
    if requested.health_check_path:
        findings.append(
            Finding(
                "health_check_path",
                "ignored",
                "Background workers have no web address, so the health check path is not used.",
            )
        )
    if requested.min_instances < 1:
        findings.append(Finding("min_instances", "error", "Background workers need at least 1 instance."))
    return findings


def blocking(findings: list[Finding]) -> list[FieldError]:
    return [FieldError(f.field, f.message) for f in findings if f.severity == "error"]


def predicted_adjustments(caps: Capabilities, requested: Requested) -> list[dict[str, str]]:
    adjustments: list[dict[str, str]] = []
    if requested.min_instances < caps.min_instances_floor:
        adjustments.append(
            {
                "field": "min_instances",
                "requested": str(requested.min_instances),
                "effective": str(caps.min_instances_floor),
                "reason": (
                    f"{PROVIDER_LABELS[caps.provider]} keeps at least {caps.min_instances_floor} instance running."
                ),
            }
        )
    return adjustments


def effective_dict(effective: EffectiveConfig, observed_at: str) -> dict[str, Any]:
    return {
        "port": effective.port,
        "cpu": effective.cpu,
        "memory": effective.memory,
        "min_instances": effective.min_instances,
        "max_instances": effective.max_instances,
        "concurrency": effective.concurrency,
        "exposure": effective.exposure,
        "health_check_path": effective.health_check_path,
        "observed_at": observed_at,
    }


def observed_adjustments(provider: str, requested: Requested, effective: EffectiveConfig) -> list[dict[str, str]]:
    """Requested settings the provider applied differently, as observed after a deployment."""
    name = PROVIDER_LABELS[provider]
    adjustments: list[dict[str, str]] = []
    pairs: list[tuple[str, Any, Any]] = [
        ("port", requested.port, effective.port),
        ("cpu", requested.cpu, effective.cpu),
        ("memory", requested.memory, effective.memory),
        ("min_instances", requested.min_instances, effective.min_instances),
        ("max_instances", requested.max_instances, effective.max_instances),
        ("exposure", requested.exposure, effective.exposure),
        ("health_check_path", requested.health_check_path, effective.health_check_path),
    ]
    if requested.concurrency is not None:
        pairs.append(("concurrency", requested.concurrency, effective.concurrency))
    for field, want, got in pairs:
        if want == got:
            continue
        reason = (
            f"{name} keeps at least {got} instance running."
            if field == "min_instances" and isinstance(got, int) and isinstance(want, int) and got > want
            else f"{name} applied a different value than requested."
        )
        adjustments.append(
            {
                "field": field,
                "requested": "none" if want is None else str(want),
                "effective": "none" if got is None else str(got),
                "reason": reason,
            }
        )
    return adjustments
