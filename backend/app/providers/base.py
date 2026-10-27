"""The interface between CloudBridge and a cloud provider.

Every method is a short, non-blocking step: long operations (builds, rollouts, deletions) are
started by one call and observed by later `poll_*` calls, so the worker can persist progress
between steps and resume after a restart. Methods raise `ProviderError` with a user-safe message.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.domain import CheckStatus, ConnectionStatus, ValidationStage


class ProviderError(Exception):
    """A provider call failed. `message` is safe to show to users; it never contains raw provider output
    that was not passed through redaction."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        connection_status: ConnectionStatus | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.connection_status: ConnectionStatus = connection_status or _DEFAULT_CONNECTION_STATUS.get(code, "error")


_DEFAULT_CONNECTION_STATUS: dict[str, ConnectionStatus] = {
    "invalid_credentials": "invalid_credentials",
    "insufficient_permissions": "insufficient_permissions",
    "api_disabled": "insufficient_permissions",
    "provider_unavailable": "provider_unavailable",
    "timed_out": "timed_out",
}


@dataclass(frozen=True)
class ConnectionInfo:
    id: str
    provider: str
    region: str
    account_id: str | None = None
    role_arn: str | None = None
    external_id: str | None = None
    project_id: str | None = None
    workload_identity_provider: str | None = None
    service_account_email: str | None = None
    tenant_id: str | None = None
    subscription_id: str | None = None
    client_id: str | None = None
    resource_group: str | None = None
    environment_name: str | None = None
    registry_name: str | None = None
    ecs_cluster: str | None = None
    api_server: str | None = None
    ca_certificate: str | None = None
    namespace: str | None = None
    kubernetes_auth: str | None = None
    token_audience: str | None = None


@dataclass(frozen=True)
class AppTarget:
    """The provider-facing identity of an application."""

    id: str
    name: str
    region: str
    provider_service_id: str | None
    application_type: str = "web_service"
    min_instances: int = 1


@dataclass(frozen=True)
class SourceInfo:
    type: Literal["image", "repository"]
    image: str | None
    repository_url: str | None
    ref_type: str | None
    git_ref: str | None
    dockerfile_path: str | None
    build_context: str | None


@dataclass(frozen=True)
class SpecInfo:
    port: int
    cpu: str
    memory: str
    min_instances: int
    max_instances: int
    concurrency: int | None
    exposure: str
    health_check_path: str | None
    environment: list[tuple[str, str]]
    secret_refs: list[tuple[str, str]]

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> SpecInfo:
        return cls(
            port=int(data["port"]),
            cpu=str(data["cpu"]),
            memory=str(data["memory"]),
            min_instances=int(data["min_instances"]),
            max_instances=int(data["max_instances"]),
            concurrency=None if data.get("concurrency") is None else int(data["concurrency"]),
            exposure=str(data["exposure"]),
            health_check_path=data.get("health_check_path"),
            environment=[(str(e["name"]), str(e["value"])) for e in data.get("environment", [])],
            secret_refs=[(str(s["name"]), str(s["reference"])) for s in data.get("secret_refs", [])],
        )


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str | None = None


@dataclass(frozen=True)
class ConnectionValidationResult:
    status: ConnectionStatus
    error_code: str | None
    checks: list[CheckResult]


ProgressCallback = Callable[[ValidationStage, list[CheckResult]], None]


@dataclass(frozen=True)
class PrepareResult:
    ready: bool
    messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BuildStatus:
    state: Literal["running", "succeeded", "failed", "cancelled"]
    image_uri: str | None = None
    image_digest: str | None = None
    commit: str | None = None
    message: str | None = None
    log_lines: list[tuple[dt.datetime, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ResolvedImage:
    repository: str
    """Registry host and repository path, without tag or digest."""
    digest: str
    """sha256:<64 hex>"""

    @property
    def pinned(self) -> str:
        return f"{self.repository}@{self.digest}"


@dataclass(frozen=True)
class EffectiveConfig:
    port: int
    cpu: str
    memory: str
    min_instances: int
    max_instances: int
    concurrency: int | None
    exposure: str
    health_check_path: str | None


@dataclass(frozen=True)
class RolloutStatus:
    state: Literal["running", "succeeded", "failed"]
    service_id: str | None = None
    service_url: str | None = None
    effective: EffectiveConfig | None = None
    message: str | None = None


@dataclass(frozen=True)
class ServiceState:
    running: bool | None
    """True when the provider reports the service serving, False when failed or missing, None while changing."""
    message: str | None = None


@dataclass(frozen=True)
class DeleteStatus:
    state: Literal["running", "deleted", "failed"]
    message: str | None = None


@dataclass(frozen=True)
class RuntimeLogEntry:
    timestamp: dt.datetime
    stream: Literal["stdout", "stderr", "system"]
    message: str


@dataclass(frozen=True)
class MetricPoint:
    timestamp: dt.datetime
    value: float


MetricName = Literal["requests", "errors_4xx", "errors_5xx", "latency_ms", "instances", "cpu_percent", "memory_percent"]


@dataclass(frozen=True)
class MetricSeries:
    name: MetricName
    unit: str
    points: list[MetricPoint]


@dataclass(frozen=True)
class DnsRecord:
    type: str
    name: str
    value: str
    purpose: str


@dataclass(frozen=True)
class DomainState:
    status: Literal["pending_dns", "verifying", "active", "failed"]
    records: list[DnsRecord]
    message: str | None = None


class ExtrasNotSupported:
    """Defaults for the optional capabilities (metrics, custom domains, pausing). An adapter overrides the ones
    its provider supports; the rest answer with a clear `not_supported` error instead of failing obscurely."""

    label = "This provider"
    supports_repository_builds = True
    plain_http_allowed = False

    def _unsupported(self, what: str) -> ProviderError:
        return ProviderError("not_supported", f"{self.label} does not support {what} in CloudBridge yet.")

    def metrics(self, conn: ConnectionInfo, app: AppTarget, minutes: int, step_seconds: int) -> list[MetricSeries]:
        raise self._unsupported("metrics")

    def add_domain(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> DomainState:
        raise self._unsupported("custom domains")

    def domain_status(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> DomainState:
        raise self._unsupported("custom domains")

    def remove_domain(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> None:
        raise self._unsupported("custom domains")

    def set_paused(self, conn: ConnectionInfo, app: AppTarget, paused: bool) -> None:
        raise self._unsupported("pausing services")


class ProviderAdapter(Protocol):
    kind: str
    label: str
    supports_repository_builds: bool
    plain_http_allowed: bool

    def validate_connection(self, conn: ConnectionInfo, progress: ProgressCallback) -> ConnectionValidationResult: ...

    def check_access(self, conn: ConnectionInfo) -> None:
        """Raises ProviderError when CloudBridge cannot currently act on the connection."""

    def prepare(self, conn: ConnectionInfo, app: AppTarget, needs_build: bool) -> PrepareResult: ...

    def start_build(
        self, conn: ConnectionInfo, app: AppTarget, source: SourceInfo, tag: str, token: str
    ) -> dict[str, Any]: ...

    def poll_build(self, conn: ConnectionInfo, handle: dict[str, Any]) -> BuildStatus: ...

    def cancel_build(self, conn: ConnectionInfo, handle: dict[str, Any]) -> None: ...

    def resolve_image(self, conn: ConnectionInfo, image: str) -> ResolvedImage: ...

    def start_rollout(
        self, conn: ConnectionInfo, app: AppTarget, image: ResolvedImage, spec: SpecInfo
    ) -> dict[str, Any]: ...

    def poll_rollout(self, conn: ConnectionInfo, handle: dict[str, Any]) -> RolloutStatus: ...

    def check_service(self, conn: ConnectionInfo, app: AppTarget) -> ServiceState: ...

    def start_delete(self, conn: ConnectionInfo, app: AppTarget) -> dict[str, Any]: ...

    def poll_delete(self, conn: ConnectionInfo, handle: dict[str, Any]) -> DeleteStatus: ...

    def runtime_logs(self, conn: ConnectionInfo, app: AppTarget, limit: int) -> tuple[list[RuntimeLogEntry], bool]: ...

    def service_host_allowed(self, host: str) -> bool:
        """Whether CloudBridge may send health-check requests to this host (SSRF guard)."""

    def metrics(self, conn: ConnectionInfo, app: AppTarget, minutes: int, step_seconds: int) -> list[MetricSeries]: ...

    def add_domain(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> DomainState:
        """Starts serving the application on `domain`. Returns the DNS records the customer must create."""

    def domain_status(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> DomainState: ...

    def remove_domain(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> None: ...

    def set_paused(self, conn: ConnectionInfo, app: AppTarget, paused: bool) -> None:
        """Stops (or starts again) serving without deleting anything. The change may finish asynchronously."""


class ProviderRegistry:
    def __init__(self, adapters: dict[str, ProviderAdapter]) -> None:
        self._adapters = adapters

    def get(self, kind: str) -> ProviderAdapter:
        try:
            return self._adapters[kind]
        except KeyError:
            raise ProviderError("unsupported_provider", "This provider is not supported.") from None
