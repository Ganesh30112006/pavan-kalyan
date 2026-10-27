"""Monthly cost estimates from public list prices.

These are estimates, not quotes: list prices for us-east-1 / East US, before taxes, discounts and free
tiers other than the ones named. Traffic decides where between the low and high figure a bill lands, so
both ends are shown with the assumptions that produce them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.providers.capabilities import memory_mib

HOURS_PER_MONTH = 730
SECONDS_PER_MONTH = HOURS_PER_MONTH * 3600

# AWS App Runner: active instances pay vCPU and memory; provisioned (idle) instances pay memory only.
APP_RUNNER_VCPU_HOUR = 0.064
APP_RUNNER_GB_HOUR = 0.007
# AWS Fargate (Linux, x86) and the Application Load Balancer that ECS Express Mode creates.
FARGATE_VCPU_HOUR = 0.04048
FARGATE_GB_HOUR = 0.004445
ALB_HOUR = 0.0225
# Azure Container Apps (consumption plan).
ACA_ACTIVE_VCPU_SECOND = 0.000024
ACA_ACTIVE_GIB_SECOND = 0.000003
ACA_IDLE_VCPU_SECOND = 0.000003
ACA_IDLE_GIB_SECOND = 0.000003
ACA_FREE_VCPU_SECONDS = 180_000
ACA_FREE_GIB_SECONDS = 360_000
# Google Cloud Run (instance-based billing).
CLOUD_RUN_VCPU_SECOND = 0.000018
CLOUD_RUN_GIB_SECOND = 0.000002


@dataclass(frozen=True)
class CostLine:
    item: str
    low: float
    high: float


@dataclass(frozen=True)
class CostEstimate:
    provider: str
    currency: str
    monthly_low: float | None
    monthly_high: float | None
    lines: list[CostLine] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


def _gib(memory: str) -> float:
    mib = memory_mib(memory)
    return (mib or 1024) / 1024


def _round(value: float) -> float:
    return round(max(value, 0.0), 2)


def estimate(provider: str, cpu: str, memory: str, min_instances: int, max_instances: int) -> CostEstimate:
    vcpu = float(cpu)
    gib = _gib(memory)
    low_n = max(min_instances, 0)
    high_n = max(max_instances, low_n, 1)

    if provider == "aws_app_runner":
        low_n = max(low_n, 1)
        idle = gib * APP_RUNNER_GB_HOUR * HOURS_PER_MONTH
        active = (vcpu * APP_RUNNER_VCPU_HOUR + gib * APP_RUNNER_GB_HOUR) * HOURS_PER_MONTH
        lines = [CostLine("Instances", low_n * idle, high_n * active)]
        assumptions = [
            f"Low: {low_n} instance{'s' if low_n != 1 else ''} provisioned but idle all month (memory only).",
            f"High: {high_n} instance{'s' if high_n != 1 else ''} handling requests around the clock.",
            "Build minutes, data transfer and custom domains are not included.",
        ]
    elif provider == "aws_ecs_express":
        low_n = max(low_n, 1)
        task = (vcpu * FARGATE_VCPU_HOUR + gib * FARGATE_GB_HOUR) * HOURS_PER_MONTH
        alb = ALB_HOUR * HOURS_PER_MONTH
        lines = [CostLine("Fargate tasks", low_n * task, high_n * task), CostLine("Load balancer", alb, alb)]
        assumptions = [
            f"Low: {low_n} task{'s' if low_n != 1 else ''} running all month; high: {high_n}.",
            "The load balancer is billed hourly; its capacity units (LCUs) and data transfer are not included.",
            "Express Mode may share one load balancer between services, which lowers this figure.",
        ]
    elif provider == "azure_container_apps":
        free = ACA_FREE_VCPU_SECONDS * ACA_ACTIVE_VCPU_SECOND + ACA_FREE_GIB_SECONDS * ACA_ACTIVE_GIB_SECOND
        idle = (vcpu * ACA_IDLE_VCPU_SECOND + gib * ACA_IDLE_GIB_SECOND) * SECONDS_PER_MONTH
        active = (vcpu * ACA_ACTIVE_VCPU_SECOND + gib * ACA_ACTIVE_GIB_SECOND) * SECONDS_PER_MONTH
        lines = [
            CostLine("Replicas", low_n * idle, high_n * active),
            CostLine("Monthly free grant", -min(free, low_n * idle), -min(free, high_n * active)),
        ]
        assumptions = [
            "Low: the minimum replicas idle all month" + (" (scaled to zero costs nothing)." if low_n == 0 else "."),
            f"High: {high_n} replica{'s' if high_n != 1 else ''} active around the clock.",
            "The consumption plan's monthly free grant (180,000 vCPU-seconds, 360,000 GiB-seconds) is applied "
            "once per subscription.",
        ]
    elif provider == "gcp_cloud_run":
        instance = (vcpu * CLOUD_RUN_VCPU_SECOND + gib * CLOUD_RUN_GIB_SECOND) * SECONDS_PER_MONTH
        lines = [CostLine("Instances", low_n * instance, high_n * instance)]
        assumptions = [
            f"Instance-based billing: low keeps {low_n} instance{'s' if low_n != 1 else ''} warm all month, "
            f"high runs {high_n}.",
            "Request-based billing and the free tier can make low-traffic services much cheaper.",
        ]
    elif provider == "kubernetes":
        return CostEstimate(
            provider=provider,
            currency="USD",
            monthly_low=None,
            monthly_high=None,
            assumptions=[
                "Kubernetes runs on your cluster's nodes, which are billed by your cloud, not per application.",
                f"This application requests {low_n * vcpu:g}–{high_n * vcpu:g} vCPU and "
                f"{low_n * gib:g}–{high_n * gib:g} GiB of the cluster's capacity.",
            ],
        )
    else:
        raise ValueError(f"Unknown provider {provider}")

    rounded = [CostLine(line.item, _round(line.low) if line.low >= 0 else round(line.low, 2),
                        _round(line.high) if line.high >= 0 else round(line.high, 2)) for line in lines]  # fmt: skip
    return CostEstimate(
        provider=provider,
        currency="USD",
        monthly_low=_round(sum(line.low for line in lines)),
        monthly_high=_round(sum(line.high for line in lines)),
        lines=rounded,
        assumptions=[*assumptions, "List prices for us-east-1 / East US; your region and agreements may differ."],
    )
