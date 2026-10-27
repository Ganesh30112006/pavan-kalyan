"""Google Cloud Run.

CloudBridge authenticates with workload identity federation: it proves its own identity (its AWS
role, or an OIDC token file) to Google STS through the customer's workload identity provider, then
impersonates the customer's service account for short-lived access tokens. No service account
keys are accepted or stored.

Repository sources are built by Cloud Build in the customer's project and pushed to Artifact
Registry. Services are managed through the Cloud Run Admin API v2 with images pinned by digest.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import requests
from google.auth import aws as google_aws
from google.auth import exceptions as google_exceptions
from google.auth import identity_pool
from google.auth.transport.requests import AuthorizedSession, Request

from app import clock
from app.config import Settings
from app.providers.base import (
    AppTarget,
    BuildStatus,
    CheckResult,
    ConnectionInfo,
    ConnectionValidationResult,
    DeleteStatus,
    EffectiveConfig,
    ExtrasNotSupported,
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
from app.providers.capabilities import CLOUD_RUN_DEFAULT_CONCURRENCY, DOCKER_HUB_HOSTS, GCP_REGISTRY_HOST
from app.providers.images import OciResolver, is_digest, parse_image
from app.redaction import log_line, safe_message

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
RUN = "https://run.googleapis.com/v2"
BUILD = "https://cloudbuild.googleapis.com/v1"
REGISTRY = "https://artifactregistry.googleapis.com/v1"
LOGGING = "https://logging.googleapis.com/v2"
TIMEOUT = (10, 30)
CHECKS = (
    "Exchange workload identity token",
    "Cloud Run Admin",
    "Artifact Registry access",
    "Cloud Build access (repository sources)",
)
APP_LABEL = "cloudbridge-application"


def _realm_allowed(host: str) -> bool:
    return host in ("auth.docker.io", "gcr.io") or host.endswith((".pkg.dev", ".gcr.io"))


class _BotoAwsSupplier(google_aws.AwsSecurityCredentialsSupplier):
    """Supplies CloudBridge's own AWS credentials (from the standard chain) to Google STS."""

    def __init__(self, region: str) -> None:
        self._region = region

    def get_aws_security_credentials(self, context: Any, request: Any) -> Any:
        import boto3

        credentials = boto3.Session().get_credentials()
        if credentials is None:
            raise google_exceptions.RefreshError("CloudBridge has no AWS identity to present to Google.")
        frozen = credentials.get_frozen_credentials()
        return google_aws.AwsSecurityCredentials(str(frozen.access_key), str(frozen.secret_key), frozen.token)

    def get_aws_region(self, context: Any, request: Any) -> str:
        return self._region


class GcpCloudRunAdapter(ExtrasNotSupported):
    kind = "gcp_cloud_run"
    label = "Google Cloud Run"

    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[ConnectionInfo], Any] | None = None,
        oci: OciResolver | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._oci = oci or OciResolver(realm_allowed=_realm_allowed)
        self._lock = threading.Lock()
        self._sessions: dict[str, Any] = {}

    # ----- credentials and HTTP -----

    def _credentials(self, conn: ConnectionInfo) -> Any:
        source = self._settings.gcp_subject_token_source
        if source == "none":
            raise ProviderError(
                "platform_not_configured",
                "CloudBridge has no workload identity configured for Google Cloud. "
                "Ask your CloudBridge administrator to configure it.",
                connection_status="error",
            )
        if not conn.workload_identity_provider or not conn.service_account_email:
            raise ProviderError(
                "invalid_credentials", "The connection has no workload identity provider or service account."
            )
        audience = f"//iam.googleapis.com/{conn.workload_identity_provider}"
        impersonation = (
            "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
            f"{conn.service_account_email}:generateAccessToken"
        )
        if source == "aws":
            return google_aws.Credentials(
                audience=audience,
                subject_token_type="urn:ietf:params:aws:token-type:aws4_request",
                aws_security_credentials_supplier=_BotoAwsSupplier(self._settings.aws_sts_region),
                service_account_impersonation_url=impersonation,
                scopes=SCOPES,
            )
        return identity_pool.Credentials(
            audience=audience,
            subject_token_type="urn:ietf:params:oauth:token-type:jwt",
            credential_source={"file": self._settings.gcp_subject_token_file},
            service_account_impersonation_url=impersonation,
            scopes=SCOPES,
        )

    def _session(self, conn: ConnectionInfo) -> Any:
        if self._session_factory is not None:
            return self._session_factory(conn)
        key = f"{conn.id}|{conn.workload_identity_provider}|{conn.service_account_email}"
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                session = AuthorizedSession(self._credentials(conn))
                self._sessions[key] = session
            return session

    def _access_token(self, conn: ConnectionInfo) -> str:
        session = self._session(conn)
        credentials = session.credentials
        try:
            if not credentials.valid:
                credentials.refresh(Request())
        except google_exceptions.RefreshError:
            raise ProviderError(
                "invalid_credentials",
                "CloudBridge could not get a token through the workload identity provider. Check the provider, "
                "its attribute conditions and the service account's workload identity user binding.",
            ) from None
        except google_exceptions.TransportError:
            raise ProviderError("provider_unavailable", "Google Cloud could not be reached.", retryable=True) from None
        return str(credentials.token)

    def _call(
        self,
        conn: ConnectionInfo,
        method: str,
        url: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any:
        session = self._session(conn)
        try:
            response = session.request(method, url, json=json, params=params, timeout=TIMEOUT)
        except google_exceptions.RefreshError:
            raise ProviderError(
                "invalid_credentials",
                "CloudBridge could not get a token through the workload identity provider. Check the provider, "
                "its attribute conditions and the service account's workload identity user binding.",
            ) from None
        except (google_exceptions.TransportError, requests.ConnectionError):
            raise ProviderError("provider_unavailable", "Google Cloud could not be reached.", retryable=True) from None
        except requests.Timeout:
            raise ProviderError("timed_out", "Google Cloud did not respond in time.", retryable=True) from None
        status = int(response.status_code)
        if 200 <= status < 300:
            return response.json() if response.content else {}
        if status == 404 and allow_404:
            return None
        raise self._error(response)

    def _error(self, response: Any) -> ProviderError:
        status = int(response.status_code)
        try:
            body = response.json().get("error") or {}
        except ValueError:
            body = {}
        raw = str(body.get("message", "")) if isinstance(body, dict) else ""
        reasons = {
            str(d.get("reason", ""))
            for d in ((body.get("details") or []) if isinstance(body, dict) else [])
            if isinstance(d, dict)
        }
        if status == 401:
            return ProviderError("invalid_credentials", "Google Cloud rejected CloudBridge's credentials.")
        if status == 403:
            if "SERVICE_DISABLED" in reasons or "has not been used" in raw or "is disabled" in raw:
                return ProviderError("api_disabled", safe_message(raw, "A required Google Cloud API is not enabled."))
            return ProviderError(
                "insufficient_permissions", safe_message(raw, "The service account does not allow this action.")
            )
        if status == 404:
            return ProviderError("not_found", safe_message(raw, "The Google Cloud resource was not found."))
        if status == 409:
            return ProviderError(
                "conflict", safe_message(raw, "The Google Cloud resource is busy or already exists."), retryable=True
            )
        if status == 429 or status >= 500:
            return ProviderError(
                "provider_unavailable", "Google Cloud is busy or unavailable. CloudBridge will retry.", retryable=True
            )
        return ProviderError("invalid_request", safe_message(raw, "Google Cloud rejected the request."))

    def _parent(self, conn: ConnectionInfo, region: str | None = None) -> str:
        return f"projects/{conn.project_id}/locations/{region or conn.region}"

    # ----- connection validation -----

    def validate_connection(self, conn: ConnectionInfo, progress: ProgressCallback) -> ConnectionValidationResult:
        checks = [CheckResult(name, "pending") for name in CHECKS]

        def update(index: int, status: Any, detail: str | None = None) -> None:
            checks[index] = CheckResult(CHECKS[index], status, detail)

        def finish(error: ProviderError, index: int) -> ConnectionValidationResult:
            update(index, "failed", error.message)
            for i, check in enumerate(checks):
                if check.status == "pending":
                    update(i, "skipped")
            return ConnectionValidationResult(error.connection_status, error.code, list(checks))

        progress("connecting", list(checks))
        try:
            self._access_token(conn)
        except ProviderError as error:
            return finish(error, 0)
        update(0, "passed")

        progress("validating_permissions", list(checks))
        parent = self._parent(conn)
        try:
            self._call(conn, "GET", f"{RUN}/{parent}/services", params={"pageSize": 1})
        except ProviderError as error:
            return finish(error, 1)
        update(1, "passed")
        try:
            self._call(conn, "GET", f"{REGISTRY}/{parent}/repositories", params={"pageSize": 1})
        except ProviderError as error:
            return finish(error, 2)
        update(2, "passed")

        progress("checking_readiness", list(checks))
        try:
            self._call(conn, "GET", f"{BUILD}/{parent}/builds", params={"pageSize": 1})
            update(3, "passed")
        except ProviderError as error:
            if error.code not in ("insufficient_permissions", "api_disabled"):
                return finish(error, 3)
            update(3, "skipped", "Not available: building from a repository will fail. Image sources are unaffected.")
        progress("checking_readiness", list(checks))
        return ConnectionValidationResult("connected", None, list(checks))

    def check_access(self, conn: ConnectionInfo) -> None:
        self._call(conn, "GET", f"{RUN}/{self._parent(conn)}/services", params={"pageSize": 1})

    # ----- build -----

    def _repository(self, conn: ConnectionInfo) -> str:
        return f"{self._parent(conn)}/repositories/{self._settings.gcp_artifact_repository}"

    def prepare(self, conn: ConnectionInfo, app: AppTarget, needs_build: bool) -> PrepareResult:
        self.check_access(conn)
        if not needs_build:
            return PrepareResult(ready=True, messages=["Provider access confirmed."])
        repository = self._call(conn, "GET", f"{REGISTRY}/{self._repository(conn)}", allow_404=True)
        if repository is not None:
            return PrepareResult(ready=True, messages=["Provider access confirmed."])
        self._call(
            conn,
            "POST",
            f"{REGISTRY}/{self._parent(conn)}/repositories",
            params={"repositoryId": self._settings.gcp_artifact_repository},
            json={
                "format": "DOCKER",
                "description": "Images built by CloudBridge",
                "labels": {"managed-by": "cloudbridge"},
            },
        )
        return PrepareResult(
            ready=False, messages=[f"Creating Artifact Registry repository {self._settings.gcp_artifact_repository}."]
        )

    def start_build(
        self, conn: ConnectionInfo, app: AppTarget, source: SourceInfo, tag: str, token: str
    ) -> dict[str, Any]:
        image = (
            f"{conn.region}-docker.pkg.dev/{conn.project_id}/{self._settings.gcp_artifact_repository}/{app.name}:{tag}"
        )
        body = {
            "source": {"gitSource": {"url": source.repository_url, "revision": source.git_ref}},
            "steps": [
                {
                    "name": "gcr.io/cloud-builders/docker",
                    "args": [
                        "build",
                        "--file",
                        source.dockerfile_path or "Dockerfile",
                        "--tag",
                        image,
                        source.build_context or ".",
                    ],
                }
            ],
            "images": [image],
            "timeout": f"{self._settings.build_timeout_minutes * 60}s",
            "options": {"logging": "CLOUD_LOGGING_ONLY"},
            "tags": ["cloudbridge", f"deployment-{token[:36]}"],
        }
        operation = self._call(conn, "POST", f"{BUILD}/{self._parent(conn)}/builds", json=body)
        build_id = ((operation.get("metadata") or {}).get("build") or {}).get("id")
        if not build_id:
            raise ProviderError("provider_unavailable", "Cloud Build did not return a build ID.", retryable=True)
        return {"build_id": str(build_id), "image_uri": image}

    def poll_build(self, conn: ConnectionInfo, handle: dict[str, Any]) -> BuildStatus:
        build = self._call(conn, "GET", f"{BUILD}/{self._parent(conn)}/builds/{handle['build_id']}")
        status = build.get("status")
        if status in ("STATUS_UNKNOWN", "PENDING", "QUEUED", "WORKING"):
            return BuildStatus(state="running")
        lines = self._build_logs(conn, str(handle["build_id"]))
        if status == "SUCCESS":
            image_uri = str(handle["image_uri"])
            digest = next(
                (
                    i.get("digest")
                    for i in ((build.get("results") or {}).get("images") or [])
                    if i.get("name") == image_uri
                ),
                None,
            )
            if not is_digest(digest):
                return BuildStatus(
                    state="failed", message="The build finished but did not report an image digest.", log_lines=lines
                )
            commit = ((build.get("sourceProvenance") or {}).get("resolvedGitSource") or {}).get("revision")
            return BuildStatus(
                state="succeeded",
                image_uri=image_uri.rsplit(":", 1)[0],
                image_digest=str(digest),
                commit=str(commit) if commit else None,
                log_lines=lines,
            )
        if status == "CANCELLED":
            return BuildStatus(state="cancelled", message="The build was cancelled.", log_lines=lines)
        if status in ("TIMEOUT", "EXPIRED"):
            return BuildStatus(state="failed", message="The image build exceeded its time limit.", log_lines=lines)
        detail = str((build.get("failureInfo") or {}).get("detail") or build.get("statusDetail") or "")
        message = safe_message(
            f"The image build failed: {detail}" if detail else "", "The image build failed. See the build logs."
        )
        return BuildStatus(state="failed", message=message, log_lines=lines)

    def _build_logs(self, conn: ConnectionInfo, build_id: str) -> list[tuple[dt.datetime, str]]:
        try:
            entries = self._log_entries(conn, f'resource.type="build" AND resource.labels.build_id="{build_id}"', 300)
        except ProviderError:
            logger.info("Could not read Cloud Build logs", extra={"connection_id": conn.id})
            return []
        return [(e.timestamp, e.message) for e in entries]

    def cancel_build(self, conn: ConnectionInfo, handle: dict[str, Any]) -> None:
        self._call(conn, "POST", f"{BUILD}/{self._parent(conn)}/builds/{handle['build_id']}:cancel", json={})

    # ----- images -----

    def resolve_image(self, conn: ConnectionInfo, image: str) -> ResolvedImage:
        ref = parse_image(image)
        if GCP_REGISTRY_HOST.match(ref.host):
            token = self._access_token(conn)
            return self._oci.resolve(ref, ref.host, ref.path, basic=("oauth2accesstoken", token))
        if ref.host in DOCKER_HUB_HOSTS:
            path = ref.path if "/" in ref.path else f"library/{ref.path}"
            return self._oci.resolve(ref, "registry-1.docker.io", path)
        raise ProviderError(
            "unsupported_registry",
            "Google Cloud Run can run images from Artifact Registry, Container Registry or Docker Hub.",
        )

    # ----- rollout -----

    def _service_name(self, conn: ConnectionInfo, app: AppTarget) -> str:
        return f"{self._parent(conn, app.region)}/services/{app.name}"

    def _service_body(self, app: AppTarget, image: ResolvedImage, spec: SpecInfo) -> dict[str, Any]:
        env: list[dict[str, Any]] = [{"name": name, "value": value} for name, value in spec.environment]
        for name, reference in spec.secret_refs:
            secret, _, version = reference.partition("/versions/")
            env.append(
                {"name": name, "valueSource": {"secretKeyRef": {"secret": secret, "version": version or "latest"}}}
            )
        container: dict[str, Any] = {
            "image": image.pinned,
            "ports": [{"containerPort": spec.port}],
            "resources": {"limits": {"cpu": spec.cpu, "memory": spec.memory}},
            "env": env,
        }
        if spec.health_check_path:
            container["startupProbe"] = {
                "httpGet": {"path": spec.health_check_path, "port": spec.port},
                "periodSeconds": 5,
                "timeoutSeconds": 3,
                "failureThreshold": 24,
            }
        template: dict[str, Any] = {
            "scaling": {"minInstanceCount": spec.min_instances, "maxInstanceCount": spec.max_instances},
            "containers": [container],
            "labels": {"managed-by": "cloudbridge", APP_LABEL: app.id},
        }
        if spec.concurrency is not None:
            template["maxInstanceRequestConcurrency"] = spec.concurrency
        return {
            "labels": {"managed-by": "cloudbridge", APP_LABEL: app.id},
            "ingress": "INGRESS_TRAFFIC_ALL" if spec.exposure == "public" else "INGRESS_TRAFFIC_INTERNAL_ONLY",
            "template": template,
        }

    def start_rollout(
        self, conn: ConnectionInfo, app: AppTarget, image: ResolvedImage, spec: SpecInfo
    ) -> dict[str, Any]:
        name = app.provider_service_id or self._service_name(conn, app)
        body = self._service_body(app, image, spec)
        existing = self._call(conn, "GET", f"{RUN}/{name}", allow_404=True)
        if existing is None:
            operation = self._call(
                conn,
                "POST",
                f"{RUN}/{self._parent(conn, app.region)}/services",
                params={"serviceId": app.name},
                json=body,
            )
        else:
            if (existing.get("labels") or {}).get(APP_LABEL) != app.id:
                raise ProviderError(
                    "service_name_taken",
                    f"A Cloud Run service named {app.name} already exists in this project and region "
                    "and is not managed by this application.",
                )
            operation = self._call(conn, "PATCH", f"{RUN}/{name}", json=body)
        return {
            "service_id": name,
            "operation": operation.get("name"),
            "public": spec.exposure == "public",
            "iam_done": False,
        }

    def poll_rollout(self, conn: ConnectionInfo, handle: dict[str, Any]) -> RolloutStatus:
        name = str(handle["service_id"])
        operation_name = handle.get("operation")
        if operation_name and not handle.get("operation_done"):
            operation = self._call(conn, "GET", f"{RUN}/{operation_name}")
            if not operation.get("done"):
                return RolloutStatus(state="running", service_id=name)
            if operation.get("error"):
                raw = str(operation["error"].get("message", ""))
                return RolloutStatus(
                    state="failed",
                    service_id=name,
                    message=safe_message(
                        f"Cloud Run could not deploy the new version: {raw}",
                        "Cloud Run could not deploy the new version. Check the image, port and health check.",
                    ),
                )
            handle["operation_done"] = True
        if not handle.get("iam_done"):
            try:
                self._set_public_access(conn, name, bool(handle.get("public")))
            except ProviderError as error:
                if error.retryable:
                    raise
                return RolloutStatus(
                    state="failed",
                    service_id=name,
                    message=safe_message(
                        f"The new version is deployed but its access could not be set: {error.message}",
                        "The new version is deployed but its public access could not be set.",
                    ),
                )
            handle["iam_done"] = True
        service = self._call(conn, "GET", f"{RUN}/{name}")
        return RolloutStatus(
            state="succeeded", service_id=name, service_url=service.get("uri"), effective=self._effective(service)
        )

    def _set_public_access(self, conn: ConnectionInfo, name: str, public: bool) -> None:
        policy = self._call(conn, "GET", f"{RUN}/{name}:getIamPolicy")
        bindings = [dict(b) for b in (policy.get("bindings") or [])]
        invoker = next((b for b in bindings if b.get("role") == "roles/run.invoker"), None)
        members = set(invoker.get("members") or []) if invoker else set()
        if public == ("allUsers" in members):
            return
        if public:
            members.add("allUsers")
        else:
            members.discard("allUsers")
        bindings = [b for b in bindings if b.get("role") != "roles/run.invoker"]
        if members:
            bindings.append({"role": "roles/run.invoker", "members": sorted(members)})
        new_policy: dict[str, Any] = {"bindings": bindings}
        if policy.get("etag"):
            new_policy["etag"] = policy["etag"]
        self._call(conn, "POST", f"{RUN}/{name}:setIamPolicy", json={"policy": new_policy})

    def _effective(self, service: dict[str, Any]) -> EffectiveConfig:
        template = service.get("template") or {}
        container = (template.get("containers") or [{}])[0]
        limits = (container.get("resources") or {}).get("limits") or {}
        ports = container.get("ports") or [{}]
        scaling = template.get("scaling") or {}
        probe = (container.get("startupProbe") or {}).get("httpGet") or {}
        return EffectiveConfig(
            port=int(ports[0].get("containerPort", 8080)),
            cpu=str(limits.get("cpu", "1")),
            memory=str(limits.get("memory", "512Mi")),
            min_instances=int(scaling.get("minInstanceCount", 0)),
            max_instances=int(scaling.get("maxInstanceCount", 100)),
            concurrency=int(template.get("maxInstanceRequestConcurrency", CLOUD_RUN_DEFAULT_CONCURRENCY)),
            exposure="public" if service.get("ingress", "INGRESS_TRAFFIC_ALL") == "INGRESS_TRAFFIC_ALL" else "private",
            health_check_path=probe.get("path"),
        )

    def check_service(self, conn: ConnectionInfo, app: AppTarget) -> ServiceState:
        name = app.provider_service_id or self._service_name(conn, app)
        service = self._call(conn, "GET", f"{RUN}/{name}", allow_404=True)
        if service is None:
            return ServiceState(running=False, message="The Cloud Run service no longer exists.")
        if service.get("reconciling"):
            return ServiceState(running=None)
        state = (service.get("terminalCondition") or {}).get("state")
        if state == "CONDITION_SUCCEEDED":
            return ServiceState(running=True)
        if state in ("CONDITION_PENDING", "CONDITION_RECONCILING"):
            return ServiceState(running=None)
        return ServiceState(running=False, message="Cloud Run reports the service as not ready.")

    # ----- deletion -----

    def start_delete(self, conn: ConnectionInfo, app: AppTarget) -> dict[str, Any]:
        if not app.provider_service_id:
            return {"done": True}
        operation = self._call(conn, "DELETE", f"{RUN}/{app.provider_service_id}", allow_404=True)
        if operation is None:
            return {"done": True}
        return {"operation": operation.get("name")}

    def poll_delete(self, conn: ConnectionInfo, handle: dict[str, Any]) -> DeleteStatus:
        if handle.get("done") or not handle.get("operation"):
            return DeleteStatus(state="deleted")
        operation = self._call(conn, "GET", f"{RUN}/{handle['operation']}", allow_404=True)
        if operation is None:
            return DeleteStatus(state="deleted")
        if not operation.get("done"):
            return DeleteStatus(state="running")
        if operation.get("error"):
            return DeleteStatus(state="failed", message="Cloud Run could not delete the service.")
        return DeleteStatus(state="deleted")

    # ----- logs -----

    def _log_entries(self, conn: ConnectionInfo, log_filter: str, limit: int) -> list[RuntimeLogEntry]:
        body = {
            "resourceNames": [f"projects/{conn.project_id}"],
            "filter": log_filter,
            "orderBy": "timestamp desc",
            "pageSize": limit,
        }
        data = self._call(conn, "POST", f"{LOGGING}/entries:list", json=body)
        entries: list[RuntimeLogEntry] = []
        for entry in reversed(data.get("entries") or []):
            message = entry.get("textPayload")
            if message is None and isinstance(entry.get("jsonPayload"), dict):
                payload = entry["jsonPayload"]
                message = payload.get("message") or payload.get("msg")
            if message is None and isinstance(entry.get("httpRequest"), dict):
                request = entry["httpRequest"]
                message = (
                    f"{request.get('requestMethod', '')} {request.get('requestUrl', '')} {request.get('status', '')}"
                )
            if message is None:
                continue
            log_name = str(entry.get("logName", ""))
            stream = "stderr" if "stderr" in log_name else "stdout" if "stdout" in log_name else "system"
            entries.append(
                RuntimeLogEntry(
                    timestamp=_parse_time(entry.get("timestamp")),
                    stream=stream,  # type: ignore[arg-type]
                    message=log_line(str(message)),
                )
            )
        return entries

    def runtime_logs(self, conn: ConnectionInfo, app: AppTarget, limit: int) -> tuple[list[RuntimeLogEntry], bool]:
        if not app.provider_service_id:
            return [], False
        since = (clock.utcnow() - dt.timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        log_filter = (
            f'resource.type="cloud_run_revision" AND resource.labels.service_name="{quote(app.name)}" '
            f'AND resource.labels.location="{app.region}" AND timestamp>="{since}"'
        )
        entries = self._log_entries(conn, log_filter, limit + 1)
        truncated = len(entries) > limit
        return entries[-limit:], truncated

    def service_host_allowed(self, host: str) -> bool:
        return host.endswith(".run.app")


def _parse_time(value: Any) -> dt.datetime:
    if isinstance(value, str):
        try:
            return clock.as_utc(dt.datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            pass
    return clock.utcnow()
