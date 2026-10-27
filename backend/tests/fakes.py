"""A scriptable provider adapter for tests. It never contacts a cloud provider."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from app.providers.base import (
    AppTarget,
    BuildStatus,
    CheckResult,
    ConnectionInfo,
    ConnectionValidationResult,
    DeleteStatus,
    DnsRecord,
    DomainState,
    EffectiveConfig,
    MetricPoint,
    MetricSeries,
    PrepareResult,
    ProgressCallback,
    ProviderError,
    ResolvedImage,
    RolloutStatus,
    RuntimeLogEntry,
    ServiceState,
    SourceInfo,
    SpecInfo,
)

DIGEST = "sha256:" + "a" * 64
BUILT_DIGEST = "sha256:" + "b" * 64


@dataclass
class FakeAdapter:
    kind: str
    label: str = "Fake provider"
    supports_repository_builds: bool = True
    plain_http_allowed: bool = False
    paused: bool = False
    domains: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)
    validation_status: str = "connected"
    build_polls_until_done: int = 1
    build_result: str = "succeeded"
    rollout_polls_until_done: int = 1
    rollout_result: str = "succeeded"
    service_running: bool | None = True
    fail_next: list[ProviderError] = field(default_factory=list)
    image_error: ProviderError | None = None
    min_floor: int = 1
    _build_polls: int = 0
    _rollout_polls: int = 0
    _rollouts: int = 0
    deployed_images: list[str] = field(default_factory=list)
    deleted: bool = False

    def _maybe_fail(self, name: str, payload: Any = None) -> None:
        self.calls.append((name, payload))
        if self.fail_next:
            raise self.fail_next.pop(0)

    def validate_connection(self, conn: ConnectionInfo, progress: ProgressCallback) -> ConnectionValidationResult:
        self.calls.append(("validate_connection", conn.id))
        checks = [CheckResult("Credentials", "pending"), CheckResult("Permissions", "pending")]
        progress("connecting", checks)
        if self.validation_status == "connected":
            done = [CheckResult("Credentials", "passed"), CheckResult("Permissions", "passed")]
            progress("validating_permissions", done)
            return ConnectionValidationResult("connected", None, done)
        failed = [
            CheckResult("Credentials", "passed"),
            CheckResult("Permissions", "failed", "The role does not allow apprunner:ListServices."),
        ]
        return ConnectionValidationResult(self.validation_status, self.validation_status, failed)  # type: ignore[arg-type]

    def check_access(self, conn: ConnectionInfo) -> None:
        self._maybe_fail("check_access")

    def prepare(self, conn: ConnectionInfo, app: AppTarget, needs_build: bool) -> PrepareResult:
        self._maybe_fail("prepare", needs_build)
        return PrepareResult(ready=True, messages=["Provider access confirmed."])

    def start_build(
        self, conn: ConnectionInfo, app: AppTarget, source: SourceInfo, tag: str, token: str
    ) -> dict[str, Any]:
        self._maybe_fail("start_build", {"tag": tag, "ref": source.git_ref})
        return {"build_id": f"build-{tag}", "image_uri": f"registry.example/{app.name}:{tag}"}

    def poll_build(self, conn: ConnectionInfo, handle: dict[str, Any]) -> BuildStatus:
        self._maybe_fail("poll_build", handle)
        self._build_polls += 1
        if self._build_polls < self.build_polls_until_done:
            return BuildStatus(state="running")
        now = dt.datetime.now(dt.UTC)
        if self.build_result == "succeeded":
            return BuildStatus(
                state="succeeded",
                image_uri=str(handle["image_uri"]).rsplit(":", 1)[0],
                image_digest=BUILT_DIGEST,
                commit="0123456789abcdef0123456789abcdef01234567",
                log_lines=[(now, "Step 1/3 : FROM node:22"), (now, "Successfully built")],
            )
        if self.build_result == "cancelled":
            return BuildStatus(state="cancelled", message="The build was stopped.")
        return BuildStatus(
            state="failed", message="Build phase BUILD failed: exit status 1", log_lines=[(now, "error")]
        )

    def cancel_build(self, conn: ConnectionInfo, handle: dict[str, Any]) -> None:
        self._maybe_fail("cancel_build", handle)

    def resolve_image(self, conn: ConnectionInfo, image: str) -> ResolvedImage:
        self._maybe_fail("resolve_image", image)
        if self.image_error is not None:
            raise self.image_error
        repository = image.split("@", 1)[0]
        last = repository.rsplit("/", 1)[-1]
        if ":" in last:
            repository = repository.rsplit(":", 1)[0]
        digest = image.split("@", 1)[1] if "@" in image else DIGEST
        return ResolvedImage(repository=repository, digest=digest)

    def start_rollout(
        self, conn: ConnectionInfo, app: AppTarget, image: ResolvedImage, spec: SpecInfo
    ) -> dict[str, Any]:
        self._maybe_fail("start_rollout", {"image": image.pinned, "spec": spec})
        self._rollouts += 1
        self._rollout_polls = 0
        self.deployed_images.append(image.pinned)
        return {
            "service_id": app.provider_service_id or f"svc/{app.name}",
            "operation": f"op-{self._rollouts}",
            "spec": spec.__dict__,
        }

    def poll_rollout(self, conn: ConnectionInfo, handle: dict[str, Any]) -> RolloutStatus:
        self._maybe_fail("poll_rollout", handle)
        self._rollout_polls += 1
        if self._rollout_polls < self.rollout_polls_until_done:
            return RolloutStatus(state="running", service_id=handle["service_id"])
        if self.rollout_result == "failed":
            return RolloutStatus(
                state="failed", service_id=handle["service_id"], message="The provider kept the previous version."
            )
        spec = handle["spec"]
        return RolloutStatus(
            state="succeeded",
            service_id=handle["service_id"],
            service_url="https://orders.example.awsapprunner.com",
            effective=EffectiveConfig(
                port=spec["port"],
                cpu=spec["cpu"],
                memory=spec["memory"],
                min_instances=max(spec["min_instances"], self.min_floor),
                max_instances=spec["max_instances"],
                concurrency=spec["concurrency"] or 100,
                exposure=spec["exposure"],
                health_check_path=spec["health_check_path"],
            ),
        )

    def check_service(self, conn: ConnectionInfo, app: AppTarget) -> ServiceState:
        self._maybe_fail("check_service")
        return ServiceState(running=self.service_running, message=None if self.service_running else "Not running.")

    def start_delete(self, conn: ConnectionInfo, app: AppTarget) -> dict[str, Any]:
        self._maybe_fail("start_delete", app.provider_service_id)
        return {"service_id": app.provider_service_id}

    def poll_delete(self, conn: ConnectionInfo, handle: dict[str, Any]) -> DeleteStatus:
        self._maybe_fail("poll_delete", handle)
        self.deleted = True
        return DeleteStatus(state="deleted")

    def runtime_logs(self, conn: ConnectionInfo, app: AppTarget, limit: int) -> tuple[list[RuntimeLogEntry], bool]:
        self._maybe_fail("runtime_logs")
        return [RuntimeLogEntry(dt.datetime.now(dt.UTC), "stdout", "GET /healthz 200")], False

    def service_host_allowed(self, host: str) -> bool:
        return host.endswith(".awsapprunner.com") or host.endswith(".run.app")

    # ----- optional capabilities -----

    def metrics(self, conn: ConnectionInfo, app: AppTarget, minutes: int, step_seconds: int) -> list[MetricSeries]:
        self._maybe_fail("metrics", (minutes, step_seconds))
        now = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.UTC)
        points = [MetricPoint(now - dt.timedelta(seconds=step_seconds * i), float(10 * i)) for i in range(3)]
        return [MetricSeries("requests", "count", sorted(points, key=lambda p: p.timestamp))]

    def add_domain(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> DomainState:
        self._maybe_fail("add_domain", domain)
        self.domains[domain] = "pending_dns"
        return self.domain_status(conn, app, domain)

    def domain_status(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> DomainState:
        self.calls.append(("domain_status", domain))
        status = self.domains.get(domain, "failed")
        record = DnsRecord("CNAME", domain, "target.example.net", "Routes traffic")
        return DomainState(status=status, records=[record], message=None)  # type: ignore[arg-type]

    def remove_domain(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> None:
        self._maybe_fail("remove_domain", domain)
        self.domains.pop(domain, None)

    def set_paused(self, conn: ConnectionInfo, app: AppTarget, paused: bool) -> None:
        self._maybe_fail("set_paused", paused)
        self.paused = paused
