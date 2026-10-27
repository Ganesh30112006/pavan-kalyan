"""What each provider supports, and how a requested spec maps onto it.

`spec_findings` mirrors frontend/src/utils/capabilities.ts so the wizard's warnings and the
backend's 422 responses agree. Provider-specific rules the capability model cannot express
(valid CPU/memory pairs, image registries) are added by `provider_rules`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.config import Settings
from app.domain import PROVIDER_LABELS
from app.validation import secret_reference_provider

Severity = Literal["error", "adjusted", "ignored"]

APPLICATION_TYPE_LABELS = {"web_service": "Web service", "api": "API", "worker": "Background worker"}

# App Runner: vCPU -> allowed memory (https://docs.aws.amazon.com/apprunner/latest/api/API_InstanceConfiguration.html)
APP_RUNNER_CPU_MEMORY: dict[str, tuple[str, ...]] = {
    "0.25": ("512Mi", "1Gi"),
    "0.5": ("1Gi",),
    "1": ("2Gi", "3Gi", "4Gi"),
    "2": ("4Gi", "6Gi"),
    "4": ("8Gi", "10Gi", "12Gi"),
}
APP_RUNNER_MEMORY = ("512Mi", "1Gi", "2Gi", "3Gi", "4Gi", "6Gi", "8Gi", "10Gi", "12Gi")
APP_RUNNER_DEFAULT_CONCURRENCY = 100

CLOUD_RUN_CPU = ("1", "2", "4", "8")
CLOUD_RUN_MEMORY = ("512Mi", "1Gi", "2Gi", "4Gi", "8Gi", "16Gi", "32Gi")
CLOUD_RUN_DEFAULT_CONCURRENCY = 80

# Container Apps (consumption): CPU and memory come in fixed pairs of 1 vCPU : 2 GiB.
CONTAINER_APPS_CPU_MEMORY: dict[str, str] = {
    "0.25": "512Mi",
    "0.5": "1Gi",
    "0.75": "1536Mi",
    "1": "2Gi",
    "1.25": "2560Mi",
    "1.5": "3Gi",
    "1.75": "3584Mi",
    "2": "4Gi",
}
CONTAINER_APPS_DEFAULT_CONCURRENCY = 10
ACR_HOST = re.compile(r"(?a)^([a-z0-9]{5,50})\.azurecr\.io$")
MCR_HOST = "mcr.microsoft.com"

ECR_PRIVATE_HOST = re.compile(r"(?a)^(\d{12})\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com$")
ECR_PUBLIC_HOST = "public.ecr.aws"
GCP_REGISTRY_HOST = re.compile(r"(?a)^(?:[a-z0-9-]+-docker\.pkg\.dev|(?:[a-z]+\.)?gcr\.io)$")
DOCKER_HUB_HOSTS = frozenset({"docker.io", "registry-1.docker.io", "index.docker.io"})


# ECS on Fargate: vCPU -> allowed memory
FARGATE_CPU_MEMORY: dict[str, tuple[str, ...]] = {
    "0.25": ("512Mi", "1Gi", "2Gi"),
    "0.5": ("1Gi", "2Gi", "3Gi", "4Gi"),
    "1": ("2Gi", "3Gi", "4Gi", "5Gi", "6Gi", "7Gi", "8Gi"),
    "2": ("4Gi", "5Gi", "6Gi", "7Gi", "8Gi", "10Gi", "12Gi", "16Gi"),
    "4": ("8Gi", "10Gi", "12Gi", "16Gi", "20Gi", "24Gi", "30Gi"),
}
FARGATE_MEMORY = ("512Mi", "1Gi", "2Gi", "3Gi", "4Gi", "5Gi", "6Gi", "7Gi", "8Gi", "10Gi", "12Gi", "16Gi", "20Gi",
                  "24Gi", "30Gi")  # fmt: skip
KUBERNETES_CPU = ("0.25", "0.5", "1", "2", "4")
KUBERNETES_MEMORY = ("256Mi", "512Mi", "1Gi", "2Gi", "4Gi", "8Gi")
KUBERNETES_REGION = "in-cluster"
GHCR_HOST = "ghcr.io"
QUAY_HOST = "quay.io"

# Which secret store each provider can read. AWS references work for both AWS providers.
SECRET_STORES: dict[str, str] = {
    "aws_app_runner": "aws_app_runner",
    "aws_ecs_express": "aws_app_runner",
    "gcp_cloud_run": "gcp_cloud_run",
    "azure_container_apps": "azure_container_apps",
    "kubernetes": "kubernetes",
}
STORE_LABELS = {
    "aws_app_runner": "AWS",
    "gcp_cloud_run": "Google Secret Manager",
    "azure_container_apps": "Azure Key Vault",
    "kubernetes": "a Kubernetes secret",
}


@dataclass(frozen=True)
class Features:
    repository_builds: bool
    metrics: bool
    custom_domains: bool
    pause: bool


@dataclass(frozen=True)
class Capabilities:
    provider: str
    regions: list[str]
    cpu_options: list[str]
    memory_options: list[str]
    min_instances_floor: int
    max_instances_limit: int
    supports_concurrency: bool
    max_concurrency: int | None
    supports_private_exposure: bool
    supported_application_types: list[str]
    supports_secret_references: bool
    notes: list[str]
    features: Features = Features(repository_builds=True, metrics=False, custom_domains=False, pause=False)


def capabilities_for(provider: str, settings: Settings) -> Capabilities:
    if provider == "aws_app_runner":
        return Capabilities(
            provider=provider,
            regions=list(settings.aws_region_list),
            cpu_options=list(APP_RUNNER_CPU_MEMORY),
            memory_options=list(APP_RUNNER_MEMORY),
            min_instances_floor=1,
            max_instances_limit=25,
            supports_concurrency=True,
            max_concurrency=200,
            supports_private_exposure=True,
            supported_application_types=["web_service", "api"],
            supports_secret_references=True,
            notes=[
                "Services always keep at least one provisioned instance.",
                "Each vCPU size allows specific memory sizes: 0.25 vCPU with 512Mi or 1Gi, 0.5 vCPU with 1Gi, "
                "1 vCPU with 2–4Gi, 2 vCPU with 4Gi or 6Gi, 4 vCPU with 8–12Gi.",
                "Images must be in Amazon ECR or ECR Public.",
                "Private services are reachable only through a VPC interface endpoint you create for App Runner.",
                "Background workers are not supported: App Runner runs HTTP services.",
            ],
            features=Features(repository_builds=True, metrics=True, custom_domains=True, pause=True),
        )
    if provider == "aws_ecs_express":
        return Capabilities(
            provider=provider,
            regions=list(settings.aws_region_list),
            cpu_options=list(FARGATE_CPU_MEMORY),
            memory_options=list(FARGATE_MEMORY),
            min_instances_floor=1,
            max_instances_limit=100,
            supports_concurrency=False,
            max_concurrency=None,
            supports_private_exposure=False,
            supported_application_types=["web_service", "api"],
            supports_secret_references=True,
            notes=[
                "Runs on AWS Fargate behind an Application Load Balancer that ECS Express Mode creates and manages.",
                "Each vCPU size allows a range of memory: 0.25 vCPU with 512Mi–2Gi, 0.5 with 1–4Gi, 1 with 2–8Gi, "
                "2 with 4–16Gi, 4 with 8–30Gi.",
                "Tasks scale on average CPU; a request concurrency setting does not apply.",
                "Images can come from Amazon ECR, ECR Public or Docker Hub.",
                "Services are public; private services are not offered through CloudBridge yet.",
            ],
            features=Features(repository_builds=True, metrics=True, custom_domains=False, pause=True),
        )
    if provider == "kubernetes":
        return Capabilities(
            provider=provider,
            regions=[KUBERNETES_REGION],
            cpu_options=list(KUBERNETES_CPU),
            memory_options=list(KUBERNETES_MEMORY),
            min_instances_floor=1,
            max_instances_limit=100,
            supports_concurrency=False,
            max_concurrency=None,
            supports_private_exposure=True,
            supported_application_types=["web_service", "api", "worker"],
            supports_secret_references=True,
            notes=[
                "CloudBridge creates a Deployment, a Service and a HorizontalPodAutoscaler in the connection's "
                "namespace.",
                "Public services get a LoadBalancer address over plain HTTP; put an ingress controller with TLS in "
                "front for production traffic. Private services are reachable only inside the cluster.",
                "Pods scale on CPU (70% of the request); there is no scale to zero.",
                "Deploy container images; building from a repository is not available for Kubernetes yet.",
                "Secrets reference existing Kubernetes secrets in the namespace (k8s:SECRET/KEY).",
            ],
            features=Features(repository_builds=False, metrics=True, custom_domains=False, pause=True),
        )
    if provider == "gcp_cloud_run":
        return Capabilities(
            provider=provider,
            regions=list(settings.gcp_region_list),
            cpu_options=list(CLOUD_RUN_CPU),
            memory_options=list(CLOUD_RUN_MEMORY),
            min_instances_floor=0,
            max_instances_limit=1000,
            supports_concurrency=True,
            max_concurrency=1000,
            supports_private_exposure=True,
            supported_application_types=["web_service", "api"],
            supports_secret_references=True,
            notes=[
                "Services can scale to zero when idle.",
                "Memory above 4Gi needs at least 2 vCPU, above 8Gi at least 4 vCPU, above 16Gi 8 vCPU; "
                "4 vCPU needs at least 2Gi and 8 vCPU at least 4Gi.",
                "Images must be in Artifact Registry, Container Registry or Docker Hub.",
                "Private services use internal ingress and are reachable only from your VPC.",
                "More than 100 instances may need a quota increase.",
                "Background workers are not supported yet: Cloud Run services must listen on the container port.",
            ],
        )
    if provider == "azure_container_apps":
        return Capabilities(
            provider=provider,
            regions=list(settings.azure_region_list),
            cpu_options=list(CONTAINER_APPS_CPU_MEMORY),
            memory_options=list(CONTAINER_APPS_CPU_MEMORY.values()),
            min_instances_floor=0,
            max_instances_limit=300,
            supports_concurrency=True,
            max_concurrency=1000,
            supports_private_exposure=True,
            supported_application_types=["web_service", "api", "worker"],
            supports_secret_references=True,
            notes=[
                "Apps can scale to zero when idle.",
                "CPU and memory come in fixed pairs: 0.25 vCPU with 512Mi, 0.5 with 1Gi, and so on up to 2 vCPU "
                "with 4Gi.",
                "Images must be in Azure Container Registry, Microsoft Container Registry or Docker Hub.",
                "Private apps are reachable only from inside their Container Apps environment.",
                "Secrets are Azure Key Vault references, read through the runtime managed identity.",
                "Background workers run without a web address and need at least one instance.",
            ],
            features=Features(repository_builds=True, metrics=True, custom_domains=True, pause=True),
        )
    raise ValueError(f"Unknown provider {provider}")


@dataclass(frozen=True)
class Finding:
    field: str
    severity: Severity
    message: str


@dataclass(frozen=True)
class SpecToCheck:
    application_type: str
    region: str
    cpu: str
    memory: str
    min_instances: int
    max_instances: int
    concurrency: int | None
    exposure: str
    secret_ref_count: int


def spec_findings(spec: SpecToCheck, caps: Capabilities) -> list[Finding]:
    """Same checks and messages as the frontend's capabilityFindings."""
    name = PROVIDER_LABELS[caps.provider]
    findings: list[Finding] = []

    def add(field: str, severity: Severity, message: str) -> None:
        findings.append(Finding(field, severity, message))

    if spec.application_type not in caps.supported_application_types:
        label = APPLICATION_TYPE_LABELS.get(spec.application_type, spec.application_type).lower()
        add("application_type", "error", f"{name} does not run {label} applications.")
    if spec.region and spec.region not in caps.regions:
        add("region", "error", f"{name} is not available in {spec.region}.")
    if spec.cpu not in caps.cpu_options:
        add("cpu", "error", f"{name} does not offer {spec.cpu} vCPU. Supported: {', '.join(caps.cpu_options)}.")
    if spec.memory not in caps.memory_options:
        add(
            "memory",
            "error",
            f"{name} does not offer {spec.memory} of memory. Supported: {', '.join(caps.memory_options)}.",
        )
    if spec.min_instances < caps.min_instances_floor:
        floor = caps.min_instances_floor
        add(
            "min_instances",
            "adjusted",
            f"{name} keeps at least {floor} instance{'' if floor == 1 else 's'} running; "
            f"minimum instances will be {floor}, not {spec.min_instances}.",
        )
    if spec.max_instances > caps.max_instances_limit:
        add("max_instances", "error", f"{name} allows at most {caps.max_instances_limit} instances.")
    if spec.concurrency is not None:
        if not caps.supports_concurrency:
            add("concurrency", "ignored", f"{name} does not support a request concurrency setting; it will be ignored.")
        elif caps.max_concurrency is not None and spec.concurrency > caps.max_concurrency:
            add(
                "concurrency",
                "error",
                f"{name} allows at most {caps.max_concurrency} concurrent requests per instance.",
            )
    if spec.exposure == "private" and not caps.supports_private_exposure:
        add("exposure", "error", f"{name} cannot make services private through CloudBridge.")
    if spec.secret_ref_count > 0 and not caps.supports_secret_references:
        add("secret_refs", "error", f"{name} does not support secret references through CloudBridge.")
    return findings


def memory_mib(value: str) -> int | None:
    match = re.fullmatch(r"(?a)(\d+)(Mi|Gi)", value)
    if not match:
        return None
    amount = int(match.group(1))
    return amount * 1024 if match.group(2) == "Gi" else amount


def provider_rules(provider: str, cpu: str, memory: str, image: str | None) -> list[Finding]:
    """Rules beyond the capability model: valid CPU/memory pairs and supported registries."""
    findings: list[Finding] = []
    name = PROVIDER_LABELS[provider]
    if provider == "aws_app_runner":
        allowed = APP_RUNNER_CPU_MEMORY.get(cpu)
        if allowed is not None and memory in APP_RUNNER_MEMORY and memory not in allowed:
            findings.append(
                Finding("memory", "error", f"{name} supports {', '.join(allowed)} of memory with {cpu} vCPU.")
            )
        if image is not None:
            host = image.split("/", 1)[0]
            if not ECR_PRIVATE_HOST.match(host) and host != ECR_PUBLIC_HOST:
                findings.append(
                    Finding(
                        "source.image",
                        "error",
                        f"{name} can only run images from Amazon ECR (ACCOUNT.dkr.ecr.REGION.amazonaws.com/…) "
                        "or ECR Public (public.ecr.aws/…).",
                    )
                )
    elif provider == "aws_ecs_express":
        allowed = FARGATE_CPU_MEMORY.get(cpu)
        if allowed is not None and memory in FARGATE_MEMORY and memory not in allowed:
            findings.append(
                Finding("memory", "error", f"{name} supports {', '.join(allowed)} of memory with {cpu} vCPU.")
            )
        if image is not None:
            host = image.split("/", 1)[0]
            if not ECR_PRIVATE_HOST.match(host) and host != ECR_PUBLIC_HOST and host not in DOCKER_HUB_HOSTS:
                findings.append(
                    Finding(
                        "source.image",
                        "error",
                        f"{name} can run images from Amazon ECR, ECR Public (public.ecr.aws/…) or Docker Hub "
                        "(docker.io/…).",
                    )
                )
    elif provider == "kubernetes":
        if image is not None:
            host = image.split("/", 1)[0]
            known = (ECR_PUBLIC_HOST, MCR_HOST, GHCR_HOST, QUAY_HOST)
            if host not in DOCKER_HUB_HOSTS and host not in known and not ACR_HOST.match(host):
                findings.append(
                    Finding(
                        "source.image",
                        "error",
                        f"{name} can run images from Docker Hub, GitHub (ghcr.io), Quay, Microsoft "
                        "(mcr.microsoft.com), ECR Public or Azure Container Registry.",
                    )
                )
    elif provider == "azure_container_apps":
        paired = CONTAINER_APPS_CPU_MEMORY.get(cpu)
        if paired is not None and memory in CONTAINER_APPS_CPU_MEMORY.values() and memory != paired:
            findings.append(Finding("memory", "error", f"{name} pairs {cpu} vCPU with {paired} of memory."))
        if image is not None:
            host = image.split("/", 1)[0]
            if not ACR_HOST.match(host) and host != MCR_HOST and host not in DOCKER_HUB_HOSTS:
                findings.append(
                    Finding(
                        "source.image",
                        "error",
                        f"{name} can run images from Azure Container Registry (NAME.azurecr.io/…), "
                        "Microsoft Container Registry (mcr.microsoft.com/…) or Docker Hub (docker.io/…).",
                    )
                )
    elif provider == "gcp_cloud_run":
        mib = memory_mib(memory)
        if cpu in CLOUD_RUN_CPU and mib is not None and memory in CLOUD_RUN_MEMORY:
            vcpu = int(cpu)
            minimum_cpu = 8 if mib > 16 * 1024 else 4 if mib > 8 * 1024 else 2 if mib > 4 * 1024 else 1
            minimum_memory = {4: 2048, 8: 4096}.get(vcpu, 0)
            if vcpu < minimum_cpu:
                findings.append(
                    Finding("cpu", "error", f"{name} needs at least {minimum_cpu} vCPU for {memory} of memory.")
                )
            elif mib < minimum_memory:
                findings.append(
                    Finding(
                        "memory",
                        "error",
                        f"{name} needs at least {minimum_memory // 1024}Gi of memory with {cpu} vCPU.",
                    )
                )
        if image is not None:
            host = image.split("/", 1)[0]
            if not GCP_REGISTRY_HOST.match(host) and host not in DOCKER_HUB_HOSTS:
                findings.append(
                    Finding(
                        "source.image",
                        "error",
                        f"{name} can run images from Artifact Registry (REGION-docker.pkg.dev/…), "
                        "Container Registry (gcr.io/…) or Docker Hub (docker.io/…).",
                    )
                )
    return findings


def secret_reference_findings(provider: str, references: list[str]) -> list[Finding]:
    """Each secret reference must point to the target provider's secret manager."""
    findings: list[Finding] = []
    for index, reference in enumerate(references):
        target = secret_reference_provider(reference)
        if target is not None and target != SECRET_STORES.get(provider):
            where = STORE_LABELS.get(target, target)
            findings.append(
                Finding(
                    f"secret_refs.{index}.reference",
                    "error",
                    f"Secret {index + 1}: this reference points to {where}, but the application deploys to "
                    f"{PROVIDER_LABELS[provider]}.",
                )
            )
    return findings
