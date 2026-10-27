"""The deployment state machine.

Each call to `DeploymentFlow.step` performs one short step (start or poll one provider operation)
and returns how long to wait before the next step. Progress, provider handles and timers live in
`Deployment.state`, so a restarted worker resumes where the previous one stopped.

Status is only ever set from what the provider reported: success is recorded after the provider
confirmed the rollout and the service passed verification.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import clock, validation
from app.config import Settings
from app.domain import CANCELLABLE_DEPLOYMENT_STATUSES, PROVIDER_LABELS, STAGE_NAMES, AuditOutcome
from app.models import Application, ApplicationSpec, Deployment, DeploymentLog, ProviderConnection, User
from app.providers.base import (
    AppTarget,
    ConnectionInfo,
    EffectiveConfig,
    ProviderAdapter,
    ProviderError,
    ProviderRegistry,
    ResolvedImage,
    SourceInfo,
    SpecInfo,
)
from app.schemas import RequestedConfigurationIn
from app.services import audit, config_file, specs
from app.services.deployments import add_event, spec_snapshot
from app.services.targets import app_target, connection_info
from app.worker import http_check
from app.worker.notify import Notifier

logger = logging.getLogger(__name__)

STAGE_STATUS = {
    "queued": "queued",
    "validation": "validating",
    "build": "building",
    "publishing": "publishing",
    "provisioning": "deploying",
    "verification": "verifying",
}
STAGE_LABEL = {
    "queued": "Queue",
    "validation": "Validation",
    "build": "Image build",
    "publishing": "Image publishing",
    "provisioning": "Provisioning",
    "verification": "Verification",
}
BUILD_SERVICE = {
    "aws_app_runner": "AWS CodeBuild",
    "aws_ecs_express": "AWS CodeBuild",
    "gcp_cloud_run": "Cloud Build",
    "azure_container_apps": "ACR Tasks",
}
MAX_PROVIDER_RETRIES = 8
MAX_PREPARE_POLLS = 60
BUILD_POLL = 10.0
ROLLOUT_POLL = 10.0
VERIFY_POLL = 10.0

Resolver = Callable[[str], list[str]]


class StepFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _elapsed(state: dict[str, Any], key: str) -> dt.timedelta:
    started = state.get(key)
    if not started:
        return dt.timedelta(0)
    return clock.utcnow() - dt.datetime.fromisoformat(str(started).replace("Z", "+00:00"))


class DeploymentFlow:
    def __init__(
        self,
        settings: Settings,
        registry: ProviderRegistry,
        notifier: Notifier,
        resolver: Resolver | None = None,
        http_client_factory: Any = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._notifier = notifier
        self._resolver = resolver
        self._http_client_factory = http_client_factory

    # ----- entry point -----

    def step(self, db: Session, dep: Deployment) -> float | None:
        """Advances the deployment by one step. Returns seconds until the next step, or None when finished."""
        app = db.get(Application, dep.application_id)
        state = dict(dep.state or {})
        try:
            delay = self._advance(db, dep, app, state)
            state.pop("provider_errors", None)
            state.pop("internal_errors", None)
        except StepFailed as failure:
            delay = self._fail(db, dep, app, failure.code, failure.message, state)
        except ProviderError as error:
            retries = int(state.get("provider_errors", 0))
            if error.retryable and retries < MAX_PROVIDER_RETRIES:
                state["provider_errors"] = retries + 1
                if retries == 0:
                    add_event(db, dep, f"{error.message}", level="warning", stage=self._current(dep))
                delay = min(120.0, 5.0 * 2**retries)
            else:
                state.pop("provider_errors", None)
                delay = self._fail(db, dep, app, error.code, error.message, state)
        dep.state = state
        return delay

    # ----- stage bookkeeping -----

    @staticmethod
    def _current(dep: Deployment) -> str | None:
        for stage in dep.stages:
            if stage["status"] == "active":
                return str(stage["name"])
        return None

    def _set_stage(self, dep: Deployment, name: str, status: str) -> None:
        now = clock.iso(clock.utcnow())
        stages = []
        for stage in dep.stages:
            stage = dict(stage)
            if stage["name"] == name:
                stage["status"] = status
                if status == "active":
                    stage["started_at"] = now
                    stage["finished_at"] = None
                elif status in ("succeeded", "failed", "cancelled"):
                    stage["started_at"] = stage.get("started_at") or now
                    stage["finished_at"] = now
            stages.append(stage)
        dep.stages = stages

    def _begin(self, db: Session, dep: Deployment, name: str) -> None:
        self._set_stage(dep, name, "active")
        if dep.kind == "deploy" and dep.status != "rolling_back":
            dep.status = STAGE_STATUS[name]
        add_event(db, dep, f"{STAGE_LABEL[name]} started.", stage=name)

    def _complete(self, db: Session, dep: Deployment, name: str, message: str | None = None) -> None:
        self._set_stage(dep, name, "succeeded")
        add_event(db, dep, message or f"{STAGE_LABEL[name]} finished.", stage=name)

    def _skip(self, dep: Deployment, name: str) -> None:
        self._set_stage(dep, name, "skipped")

    # ----- the state machine -----

    def _advance(self, db: Session, dep: Deployment, app: Application | None, state: dict[str, Any]) -> float | None:
        if app is None or app.deleted_at is not None:
            raise StepFailed("application_deleted", "The application was deleted.")
        if dep.cancel_requested_at is not None:
            # A cancellation that raced with the move to provisioning still applies while nothing has
            # been sent to the provider; once the rollout has started it runs to completion.
            if dep.status in CANCELLABLE_DEPLOYMENT_STATUSES or (
                self._current(dep) == "provisioning" and "rollout" not in state and not state.get("restore")
            ):
                return self._cancel(db, dep, app, state)
            if not state.get("cancel_ignored"):
                state["cancel_ignored"] = True
                add_event(
                    db,
                    dep,
                    "Cancellation arrived after provisioning started, so the rollout continues.",
                    level="warning",
                    stage=self._current(dep),
                )
        if state.get("restore"):
            return self._restore(db, dep, app, state)

        stage = self._current(dep)
        if stage is None:
            pending = next((s["name"] for s in dep.stages if s["status"] == "pending"), None)
            if pending is None:
                raise StepFailed("internal_error", "The deployment has no remaining stages.")
            self._begin(db, dep, str(pending))
            return 0.0

        if stage == "queued":
            self._complete(db, dep, "queued", "Picked up by the deployment worker.")
            dep.started_at = clock.utcnow()
            self._begin(db, dep, "validation")
            return 0.0

        conn = db.get(ProviderConnection, app.connection_id)
        if conn is None or conn.status != "connected":
            raise StepFailed(
                "connection_unavailable",
                "The application's cloud account is not connected. Validate the connection and deploy again.",
            )
        info = connection_info(conn, app.region)
        adapter = self._registry.get(app.provider)
        target = app_target(app)

        if stage == "validation":
            return self._validation(db, dep, app, adapter, info, target, state)
        if stage == "build":
            return self._build(db, dep, adapter, info, target, state)
        if stage == "publishing":
            return self._publishing(db, dep, adapter, info)
        if stage == "provisioning":
            return self._provisioning(db, dep, app, adapter, info, target, state)
        if stage == "verification":
            return self._verification(db, dep, app, adapter, info, target, state)
        raise StepFailed("internal_error", f"Unknown stage {stage}.")

    def _source(self, dep: Deployment) -> SourceInfo:
        s = dep.source_snapshot
        return SourceInfo(
            type=s.get("type", "image"),
            image=s.get("image"),
            repository_url=s.get("repository_url"),
            ref_type=s.get("ref_type"),
            git_ref=s.get("git_ref"),
            dockerfile_path=s.get("dockerfile_path"),
            build_context=s.get("build_context"),
        )

    def _validation(
        self,
        db: Session,
        dep: Deployment,
        app: Application,
        adapter: ProviderAdapter,
        info: ConnectionInfo,
        target: AppTarget,
        state: dict[str, Any],
    ) -> float:
        source = self._source(dep)
        needs_build = dep.kind == "deploy" and source.type == "repository"
        if needs_build and not adapter.supports_repository_builds:
            raise StepFailed(
                "builds_not_supported", f"{adapter.label} cannot build from a repository yet. Deploy an image."
            )
        if dep.kind == "deploy" and dep.source_snapshot.get("config_file") and not state.get("config_checked"):
            self._apply_config_file(db, dep, app, source)
            state["config_checked"] = True
        if not state.get("checked_spec"):
            requested = specs.Requested.from_snapshot(dep.spec_snapshot)
            image = source.image if dep.kind == "deploy" and source.type == "image" else None
            errors = specs.blocking(
                specs.findings_for(self._settings, app.provider, app.application_type, app.region, requested, image)
            )
            if errors:
                extra = f" ({len(errors) - 1} more issue{'s' if len(errors) > 2 else ''})" if len(errors) > 1 else ""
                raise StepFailed("invalid_configuration", f"{errors[0].message}{extra}")
            if needs_build and source.repository_url:
                problem = validation.repository_host_is_public(source.repository_url, self._resolver)
                if problem:
                    raise StepFailed("repository_unreachable", problem)
            state["checked_spec"] = True

        result = adapter.prepare(info, target, needs_build)
        for message in result.messages:
            add_event(db, dep, message, stage="validation")
        if not result.ready:
            polls = int(state.get("prepare_polls", 0)) + 1
            state["prepare_polls"] = polls
            if polls > MAX_PREPARE_POLLS:
                raise StepFailed(
                    "provider_not_ready", "The provider resources CloudBridge needs did not become ready in time."
                )
            return 5.0

        self._complete(db, dep, "validation", "Configuration and provider access validated.")
        if dep.kind == "rollback":
            self._skip(dep, "build")
            self._skip(dep, "publishing")
            add_event(db, dep, f"Restoring image {dep.image_digest} of version {dep.version}.", stage="provisioning")
            self._begin(db, dep, "provisioning")
        elif needs_build:
            self._begin(db, dep, "build")
        else:
            self._skip(dep, "build")
            self._begin(db, dep, "publishing")
        return 0.0

    def _apply_config_file(self, db: Session, dep: Deployment, app: Application, source: SourceInfo) -> None:
        """Reads cloudbridge.yaml at the deployed ref and makes it the application's requested settings."""
        ref = source.git_ref or ""
        try:
            text = config_file.fetch_github(source.repository_url or "", ref, self._http_client_factory)
        except config_file.ConfigFetchError as error:
            raise StepFailed("config_file_unavailable", str(error)) from None
        if text is None:
            add_event(
                db, dep, f"No {config_file.FILE_NAME} at {ref[:12]}; using the saved settings.", stage="validation"
            )
            return
        try:
            merged = config_file.parse(text, dep.spec_snapshot)
            body = RequestedConfigurationIn.model_validate(merged)
        except config_file.ConfigFileError as error:
            raise StepFailed("invalid_config_file", f"{config_file.FILE_NAME}: {error}") from None
        except ValidationError as error:
            first = error.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            raise StepFailed("invalid_config_file", f"{config_file.FILE_NAME}: {where}: {first.get('msg')}") from None
        requested, errors = specs.normalize_requested(body)
        if errors:
            raise StepFailed("invalid_config_file", f"{config_file.FILE_NAME}: {errors[0].message}")
        changed = [
            key
            for key in (
                "port",
                "cpu",
                "memory",
                "min_instances",
                "max_instances",
                "concurrency",
                "exposure",
                "health_check_path",
                "environment",
                "secret_refs",
            )
            if getattr(requested, key) != dep.spec_snapshot.get(key)
        ]
        if not changed:
            add_event(db, dep, f"{config_file.FILE_NAME} matches the saved settings.", stage="validation")
            return
        spec = db.get(ApplicationSpec, app.id)
        if spec is not None:
            for key in changed:
                setattr(spec, key, getattr(requested, key))
            spec.version += 1
            spec.updated_at = clock.utcnow()
            dep.spec_snapshot = spec_snapshot(spec)
        add_event(
            db, dep, f"Applied {config_file.FILE_NAME}: {', '.join(changed).replace('_', ' ')}.", stage="validation"
        )

    def _build(
        self,
        db: Session,
        dep: Deployment,
        adapter: ProviderAdapter,
        info: ConnectionInfo,
        target: AppTarget,
        state: dict[str, Any],
    ) -> float:
        source = self._source(dep)
        if "build" not in state:
            tag = f"{dep.version}-{dep.id[:8]}"
            state["build"] = adapter.start_build(info, target, source, tag, dep.id)
            state["build_started_at"] = clock.iso(clock.utcnow())
            add_event(
                db,
                dep,
                f"Image build started in {BUILD_SERVICE.get(dep.provider, 'the build service')}.",
                stage="build",
            )
            return BUILD_POLL

        status = adapter.poll_build(info, state["build"])
        if status.state == "running":
            limit = dt.timedelta(minutes=self._settings.build_timeout_minutes + 5)
            if _elapsed(state, "build_started_at") > limit:
                self._try_cancel_build(db, dep, adapter, info, state)
                raise StepFailed("build_timed_out", "The image build did not finish in time.")
            return BUILD_POLL

        for timestamp, line in status.log_lines:
            db.add(DeploymentLog(deployment_id=dep.id, timestamp=timestamp, stream="stdout", message=line))
        state["build_finished"] = True
        if status.state == "cancelled":
            if dep.cancel_requested_at is not None:
                self._finish_cancelled(db, dep, state)
                return 0.0
            raise StepFailed("build_cancelled", "The image build was stopped outside CloudBridge.")
        if status.state == "failed":
            raise StepFailed("build_failed", status.message or "The image build failed. See the build logs.")

        dep.image_uri = status.image_uri
        dep.image_digest = status.image_digest
        # A commit source already has its label (e.g. "main@<sha>" from a push, set when it was queued).
        if status.commit and source.ref_type != "commit":
            dep.source_ref = f"{source.git_ref}@{status.commit[:12]}"
        self._complete(db, dep, "build", f"Image built: {status.image_digest}.")
        self._begin(db, dep, "publishing")
        return 0.0

    def _publishing(self, db: Session, dep: Deployment, adapter: ProviderAdapter, info: ConnectionInfo) -> float:
        source = self._source(dep)
        if dep.image_uri and dep.image_digest:
            reference = f"{dep.image_uri}@{dep.image_digest}"
        elif source.image:
            reference = source.image
        else:
            raise StepFailed("image_unavailable", "There is no image to deploy.")
        try:
            resolved = adapter.resolve_image(info, reference)
        except ProviderError as error:
            if error.retryable:
                raise
            raise StepFailed("image_unavailable", error.message) from None
        dep.image_uri = resolved.repository
        dep.image_digest = resolved.digest
        self._complete(db, dep, "publishing", f"Image pinned to {resolved.digest}.")
        self._begin(db, dep, "provisioning")
        return 0.0

    def _start_rollout(
        self,
        app: Application,
        adapter: ProviderAdapter,
        info: ConnectionInfo,
        target: AppTarget,
        image_uri: str,
        digest: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        handle = adapter.start_rollout(
            info, target, ResolvedImage(repository=image_uri, digest=digest), SpecInfo.from_snapshot(snapshot)
        )
        if handle.get("service_id") and app.provider_service_id != handle["service_id"]:
            app.provider_service_id = str(handle["service_id"])
        return handle

    def _provisioning(
        self,
        db: Session,
        dep: Deployment,
        app: Application,
        adapter: ProviderAdapter,
        info: ConnectionInfo,
        target: AppTarget,
        state: dict[str, Any],
    ) -> float:
        name = PROVIDER_LABELS[dep.provider]
        if "rollout" not in state:
            if not dep.image_uri or not dep.image_digest:
                raise StepFailed("image_unavailable", "There is no pinned image to deploy.")
            state["rollout"] = self._start_rollout(
                app, adapter, info, target, dep.image_uri, dep.image_digest, dep.spec_snapshot
            )
            state["rollout_started_at"] = clock.iso(clock.utcnow())
            add_event(db, dep, f"{name} is rolling out version {dep.version}.", stage="provisioning")
            return ROLLOUT_POLL

        handle = dict(state["rollout"])
        status = adapter.poll_rollout(info, handle)
        state["rollout"] = handle
        if status.state == "running":
            if _elapsed(state, "rollout_started_at") > dt.timedelta(minutes=self._settings.rollout_timeout_minutes):
                raise StepFailed("rollout_timed_out", f"{name} did not finish the rollout in time.")
            return ROLLOUT_POLL
        if status.state == "failed":
            raise StepFailed("provisioning_failed", status.message or f"{name} could not deploy the new version.")
        state["effective"] = _effective_state(status.effective)
        dep.service_url = status.service_url
        if status.service_id:
            app.provider_service_id = status.service_id
        self._complete(db, dep, "provisioning", f"{name} finished the rollout.")
        self._begin(db, dep, "verification")
        return 0.0

    def _check_live(
        self,
        adapter: ProviderAdapter,
        info: ConnectionInfo,
        app: Application,
        url: str | None,
        snapshot: dict[str, Any],
    ) -> tuple[str, str]:
        """('passed'|'failed'|'wait', message)."""
        target = app_target(app)
        service = adapter.check_service(info, target)
        if service.running is False:
            return "failed", service.message or "The provider reports the service is not running."
        if service.running is None:
            return "wait", "The provider is still applying the change."
        if app.application_type == "worker":
            return "passed", "The worker has no web address; the provider reports it running."
        if snapshot.get("exposure") == "private":
            return "passed", "The service is private; the provider reports it running."
        if not url:
            return "failed", "The provider did not report a service address."
        result = http_check.check(
            url,
            snapshot.get("health_check_path"),
            adapter.service_host_allowed,
            self._http_client_factory,
            allow_http=adapter.plain_http_allowed,
        )
        if result.outcome == "passed":
            return "passed", result.message
        return "wait", result.message

    def _verification(
        self,
        db: Session,
        dep: Deployment,
        app: Application,
        adapter: ProviderAdapter,
        info: ConnectionInfo,
        target: AppTarget,
        state: dict[str, Any],
    ) -> float:
        state.setdefault("verify_started_at", clock.iso(clock.utcnow()))
        outcome, message = self._check_live(adapter, info, app, dep.service_url, dep.spec_snapshot)
        if outcome == "passed":
            self._complete(db, dep, "verification", f"Verification passed: {message}")
            self._succeed(db, dep, app, state)
            return 0.0
        if outcome == "wait" and _elapsed(state, "verify_started_at") <= dt.timedelta(
            seconds=self._settings.verification_timeout_seconds
        ):
            state["verify_last"] = message
            return VERIFY_POLL
        return self._verification_failed(db, dep, app, state, f"Verification failed: {message}") or 0.0

    def _verification_failed(
        self, db: Session, dep: Deployment, app: Application, state: dict[str, Any], reason: str
    ) -> float | None:
        """The new version is serving but did not pass verification: restore the previous version if there is
        one. Returns 0.0 while restoring, None when the deployment has finished."""
        self._set_stage(dep, "verification", "failed")
        add_event(db, dep, reason, level="error", stage="verification")
        if dep.kind == "rollback":
            self._finish_failure(db, dep, app, "verification_failed", reason, serving_new=True)
            return None
        previous = db.get(Deployment, app.current_deployment_id) if app.current_deployment_id else None
        if previous is None or not previous.image_uri or not previous.image_digest or not previous.spec_snapshot:
            self._finish_failure(db, dep, app, "verification_failed", reason, serving_new=True)
            return None
        state["restore"] = {"deployment_id": previous.id, "reason": reason}
        dep.status = "rolling_back"
        add_event(db, dep, f"Restoring the previous version {previous.version}.", level="warning", stage="verification")
        return 0.0

    def _restore(self, db: Session, dep: Deployment, app: Application, state: dict[str, Any]) -> float:
        restore = dict(state["restore"])
        previous = db.get(Deployment, restore["deployment_id"])
        conn = db.get(ProviderConnection, app.connection_id)
        if previous is None or conn is None or not previous.image_uri or not previous.image_digest:
            self._finish_restore_failed(db, dep, app, state, "The previous version could not be restored.")
            return 0.0
        info = connection_info(conn, app.region)
        adapter = self._registry.get(app.provider)
        target = app_target(app)
        if "rollout" not in restore:
            restore["rollout"] = self._start_rollout(
                app, adapter, info, target, previous.image_uri, previous.image_digest, previous.spec_snapshot
            )
            restore["started_at"] = clock.iso(clock.utcnow())
            state["restore"] = restore
            return ROLLOUT_POLL
        handle = dict(restore["rollout"])
        status = adapter.poll_rollout(info, handle)
        restore["rollout"] = handle
        state["restore"] = restore
        if status.state == "running":
            if _elapsed(restore, "started_at") > dt.timedelta(minutes=self._settings.rollout_timeout_minutes):
                self._finish_restore_failed(
                    db, dep, app, state, "Restoring the previous version did not finish in time."
                )
                return 0.0
            return ROLLOUT_POLL
        if status.state == "failed":
            self._finish_restore_failed(
                db, dep, app, state, status.message or "The previous version could not be restored."
            )
            return 0.0

        now = clock.utcnow()
        dep.status = "rolled_back"
        dep.finished_at = now
        dep.failure_code = "verification_failed"
        dep.failure_message = f"{restore['reason']} The previous version {previous.version} was restored."
        add_event(db, dep, f"Previous version {previous.version} restored.", stage="verification")
        app.status = "running"
        app.health = "unknown"
        app.service_url = status.service_url or app.service_url
        self._record_effective(db, app, previous.spec_snapshot, _effective_state(status.effective))
        self._finalize(db, dep, app, action="deployment.rolled_back", outcome="failure")
        self._notifier.notify(
            db,
            app.organization_id,
            "rollback_completed",
            f"{app.name}: version {dep.version} failed verification and was rolled back",
            f"Version {dep.version} of {app.name} failed verification. "
            f"CloudBridge restored version {previous.version}.",
            application=app,
        )
        return 0.0

    def _finish_restore_failed(
        self, db: Session, dep: Deployment, app: Application, state: dict[str, Any], message: str
    ) -> None:
        reason = dict(state.get("restore") or {}).get("reason", "Verification failed.")
        dep.status = "failed_rollback"
        dep.finished_at = clock.utcnow()
        dep.failure_code = "rollback_failed"
        dep.failure_message = f"{reason} {message}"
        add_event(db, dep, message, level="error", stage="verification")
        app.status = "failed"
        app.health = "unhealthy"
        self._finalize(db, dep, app, action="deployment.failed", outcome="failure")
        self._notify_failure(db, dep, app)

    # ----- terminal transitions -----

    def _succeed(self, db: Session, dep: Deployment, app: Application, state: dict[str, Any]) -> None:
        now = clock.utcnow()
        dep.status = "rolled_back" if dep.kind == "rollback" else "succeeded"
        dep.finished_at = now
        app.status = "running"
        app.health = "healthy"
        app.health_checked_at = now
        app.current_version = dep.version
        app.current_deployment_id = dep.id
        app.image_uri = dep.image_uri
        app.image_digest = dep.image_digest
        app.service_url = dep.service_url
        self._record_effective(db, app, dep.spec_snapshot, state.get("effective"))
        self._finalize(
            db,
            dep,
            app,
            action="deployment.rolled_back" if dep.kind == "rollback" else "deployment.succeeded",
            outcome="success",
        )
        if dep.kind == "rollback":
            self._notifier.notify(
                db,
                app.organization_id,
                "rollback_completed",
                f"{app.name}: rolled back to version {dep.version}",
                f"{app.name} now runs version {dep.version} again.",
                application=app,
            )
        else:
            self._notifier.notify(
                db,
                app.organization_id,
                "deployment_succeeded",
                f"{app.name}: version {dep.version} deployed",
                f"Version {dep.version} of {app.name} is live at {dep.service_url or 'its private address'}.",
                application=app,
            )

    def _record_effective(
        self, db: Session, app: Application, snapshot: dict[str, Any], effective: dict[str, Any] | None
    ) -> None:
        spec = db.get(ApplicationSpec, app.id)
        if spec is None or effective is None:
            return
        observed = EffectiveConfig(**effective)
        spec.effective = specs.effective_dict(observed, clock.iso(clock.utcnow()))
        spec.adjustments = specs.observed_adjustments(app.provider, specs.Requested.from_snapshot(snapshot), observed)

    def _fail(
        self,
        db: Session,
        dep: Deployment,
        app: Application | None,
        code: str,
        message: str,
        state: dict[str, Any] | None = None,
    ) -> float | None:
        """Handles an error the deployment cannot recover from. Returns 0.0 when the deployment continues
        (restoring the previous version), None when it has finished."""
        if state is None:
            own = dict(dep.state or {})
            delay = self._fail(db, dep, app, code, message, own)
            dep.state = own
            return delay
        if app is not None and app.deleted_at is None:
            if dep.kind == "deploy" and state.get("restore"):
                # Restoring the previous version failed: the new, unverified version is still serving.
                self._finish_restore_failed(db, dep, app, state, message)
                return None
            if self._current(dep) == "verification":
                # The provider already serves the new version, so this is a failed verification.
                return self._verification_failed(db, dep, app, state, f"Verification failed: {message}")
        current = self._current(dep)
        if current:
            self._set_stage(dep, current, "failed")
        add_event(db, dep, message, level="error", stage=current)
        if app is None:
            dep.status = "failed_rollback" if dep.kind == "rollback" else "failed"
            dep.failure_code, dep.failure_message, dep.finished_at = code, message, clock.utcnow()
            self._skip_pending(dep)
            return None
        self._finish_failure(db, dep, app, code, message, serving_new=False)
        return None

    def _skip_pending(self, dep: Deployment) -> None:
        dep.stages = [dict(s, status="skipped") if s["status"] == "pending" else dict(s) for s in dep.stages]

    def _finish_failure(
        self, db: Session, dep: Deployment, app: Application, code: str, message: str, *, serving_new: bool
    ) -> None:
        dep.status = "failed_rollback" if dep.kind == "rollback" else "failed"
        dep.finished_at = clock.utcnow()
        dep.failure_code = code
        dep.failure_message = message
        self._skip_pending(dep)
        if serving_new and dep.kind == "deploy":
            # The provider accepted the version but it failed verification and nothing could be restored.
            app.current_version = dep.version
            app.current_deployment_id = dep.id
            app.image_uri = dep.image_uri
            app.image_digest = dep.image_digest
            app.service_url = dep.service_url or app.service_url
            app.status = "failed"
            app.health = "unhealthy"
        elif serving_new:
            app.status = "failed"
            app.health = "unhealthy"
        else:
            app.status = "running" if app.current_version else "failed"
        self._finalize(db, dep, app, action="deployment.failed", outcome="failure")
        self._notify_failure(db, dep, app)

    def _notify_failure(self, db: Session, dep: Deployment, app: Application) -> None:
        kind = "Rollback" if dep.kind == "rollback" else "Deployment"
        self._notifier.notify(
            db,
            app.organization_id,
            "deployment_failed",
            f"{app.name}: {kind.lower()} of version {dep.version} failed",
            f"{kind} of version {dep.version} of {app.name} failed: {dep.failure_message}",
            application=app,
        )

    def _try_cancel_build(
        self, db: Session, dep: Deployment, adapter: ProviderAdapter, info: ConnectionInfo, state: dict[str, Any]
    ) -> None:
        if "build" in state and not state.get("build_finished"):
            try:
                adapter.cancel_build(info, state["build"])
            except ProviderError as error:
                add_event(db, dep, f"Could not stop the build: {error.message}", level="warning", stage="build")

    def _cancel(self, db: Session, dep: Deployment, app: Application, state: dict[str, Any]) -> float:
        conn = db.get(ProviderConnection, app.connection_id)
        if "build" in state and not state.get("build_finished") and conn is not None:
            adapter = self._registry.get(app.provider)
            self._try_cancel_build(db, dep, adapter, connection_info(conn, app.region), state)
        self._finish_cancelled(db, dep, state)
        return 0.0

    def _finish_cancelled(self, db: Session, dep: Deployment, state: dict[str, Any]) -> None:
        app = db.get(Application, dep.application_id)
        dep.stages = [
            dict(s, status="cancelled", finished_at=clock.iso(clock.utcnow()))
            if s["status"] == "active"
            else dict(s, status="cancelled")
            if s["status"] == "pending"
            else dict(s)
            for s in dep.stages
        ]
        dep.status = "cancelled"
        dep.finished_at = clock.utcnow()
        add_event(db, dep, "Deployment cancelled. No changes were made to the running service.", level="warning")
        if app is not None:
            app.status = "running" if app.current_version else "draft"
            self._finalize(db, dep, app, action="deployment.cancelled", outcome="success")

    def _finalize(self, db: Session, dep: Deployment, app: Application, *, action: str, outcome: AuditOutcome) -> None:
        app.active_deployment_id = None
        app.updated_at = clock.utcnow()
        dep.next_attempt_at = None
        actor = db.get(User, dep.initiated_by_id) if dep.initiated_by_id else None
        audit.record(
            db,
            organization_id=dep.organization_id,
            actor=actor,
            action=action,
            resource_type="deployment",
            resource_id=dep.id,
            resource_name=dep.application_name,
            outcome=outcome,
        )


def _effective_state(effective: EffectiveConfig | None) -> dict[str, Any] | None:
    return None if effective is None else dict(effective.__dict__)


__all__ = ["STAGE_NAMES", "DeploymentFlow", "StepFailed"]
