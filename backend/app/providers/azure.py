"""Microsoft Azure Container Apps.

CloudBridge authenticates with a federated credential: it obtains a short-lived signed token for its
own identity (from AWS outbound identity federation, or an OIDC token file) and exchanges it at
Microsoft Entra ID for an access token of the customer's app registration, which trusts CloudBridge
through a federated identity credential. No client secrets or certificates are accepted or stored.

Repository sources are built by ACR Tasks in the customer's Azure Container Registry. Apps are managed
through the Azure Resource Manager REST API with images pinned by digest. Private images and Key Vault
secrets are read by a user-assigned managed identity in the connection's resource group.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import posixpath
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from app import clock
from app.config import Settings
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
    ExtrasNotSupported,
    MetricName,
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
from app.providers.capabilities import (
    ACR_HOST,
    CONTAINER_APPS_DEFAULT_CONCURRENCY,
    DOCKER_HUB_HOSTS,
    MCR_HOST,
    memory_mib,
)
from app.providers.entra import FederatedIdentity, TokenSource
from app.providers.images import OciResolver, is_digest, parse_image
from app.redaction import log_line, safe_message

logger = logging.getLogger(__name__)

ARM = "https://management.azure.com"
LOGIN = "https://login.microsoftonline.com"
LOG_ANALYTICS = "https://api.loganalytics.io"
ARM_SCOPE = "https://management.azure.com/.default"
LOGS_SCOPE = "https://api.loganalytics.io/.default"
TOKEN_AUDIENCE = "api://AzureADTokenExchange"
RG_API = "2021-04-01"
APPS_API = "2024-03-01"
ACR_API = "2023-07-01"
ACR_RUNS_API = "2019-06-01-preview"
IDENTITY_API = "2023-01-31"
METRICS_API = "2023-10-01"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
CHECKS = (
    "Exchange federated token",
    "Resource group access",
    "Container Apps environment",
    "Container registry (repository sources)",
    "Runtime identity (private images and secrets)",
)
APP_TAG = "cloudbridge-application"
MANAGED_TAGS = {"managed-by": "cloudbridge"}
ZERO_GUID = "00000000-0000-0000-0000-000000000000"
_SECRET_NAME = re.compile(r"[^a-z0-9-]")
_AADSTS = re.compile(r"AADSTS(\d+)")


def _realm_allowed(host: str) -> bool:
    return host == "auth.docker.io" or bool(ACR_HOST.match(host))


def _memory_to_azure(value: str) -> str:
    """Container Apps takes memory as decimal gibibytes ("0.5Gi"), not mebibytes."""
    mib = memory_mib(value)
    return value if mib is None else f"{mib / 1024:g}Gi"


def _memory_from_azure(value: Any) -> str:
    """The provider's "0.5Gi" back in CloudBridge's spelling ("512Mi", "1Gi")."""
    text = str(value or "").replace(" ", "")
    try:
        gib = float(text[:-2]) if text.endswith("Gi") else None
    except ValueError:
        gib = None
    if gib is None:
        return text or "1Gi"
    mib = round(gib * 1024)
    return f"{mib // 1024}Gi" if mib % 1024 == 0 else f"{mib}Mi"


def _cpu_text(value: Any) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "0.5"


class AzureContainerAppsAdapter(ExtrasNotSupported):
    kind = "azure_container_apps"
    label = "Azure Container Apps"

    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[], httpx.Client] | None = None,
        token_source: TokenSource | None = None,
        oci: OciResolver | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=TIMEOUT, follow_redirects=False))
        self._identity = FederatedIdentity(settings, self._client_factory, token_source)
        self._oci = oci or OciResolver(realm_allowed=_realm_allowed, client_factory=client_factory)

    # ----- credentials and HTTP -----

    def _token(self, conn: ConnectionInfo, scope: str = ARM_SCOPE) -> str:
        if not conn.tenant_id or not conn.client_id or not conn.subscription_id or not conn.resource_group:
            raise ProviderError(
                "invalid_credentials", "The connection is missing its Azure tenant, client or subscription."
            )
        return self._identity.entra_token(conn.tenant_id, conn.client_id, scope, conn.id)

    def _request(
        self,
        conn: ConnectionInfo,
        method: str,
        url: str,
        *,
        json: Any = None,
        allow_404: bool = False,
        scope: str = ARM_SCOPE,
    ) -> Any:
        token = self._token(conn, scope)
        try:
            with self._client_factory() as client:
                response = client.request(method, url, json=json, headers={"Authorization": f"Bearer {token}"})
        except httpx.TimeoutException:
            raise ProviderError("timed_out", "Azure did not respond in time.", retryable=True) from None
        except httpx.HTTPError:
            raise ProviderError("provider_unavailable", "Azure could not be reached.", retryable=True) from None
        status = response.status_code
        if 200 <= status < 300:
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                return {}
        if status == 404 and allow_404:
            return None
        raise self._error(response)

    def _error(self, response: httpx.Response) -> ProviderError:
        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = {}
        error = (body.get("error") or {}) if isinstance(body, dict) else {}
        code = str(error.get("code", "")) if isinstance(error, dict) else ""
        raw = str(error.get("message", "")) if isinstance(error, dict) else ""
        if status == 401:
            return ProviderError("invalid_credentials", "Azure rejected CloudBridge's credentials.")
        if code in ("MissingSubscriptionRegistration", "SubscriptionNotRegistered"):
            return ProviderError(
                "api_disabled", safe_message(raw, "A required Azure resource provider is not registered.")
            )
        if code == "ExpressEnvironmentFeatureNotSupported":
            return ProviderError(
                "invalid_request",
                "This Container Apps environment is an express environment, which does not support this setting "
                "(for example Key Vault secret references). Remove it, or use a standard environment.",
            )
        if code == "RequestDisallowedByPolicy":
            return ProviderError(
                "invalid_request", safe_message(raw, "An Azure policy in the subscription does not allow this.")
            )
        if status == 403:
            return ProviderError(
                "insufficient_permissions",
                safe_message(raw, "The app registration's role assignments do not allow this action."),
            )
        if status == 404:
            return ProviderError("not_found", safe_message(raw, "The Azure resource was not found."))
        if status == 409:
            return ProviderError("conflict", safe_message(raw, "The Azure resource is busy."), retryable=True)
        if status == 429 or status >= 500:
            return ProviderError(
                "provider_unavailable", "Azure is busy or unavailable. CloudBridge will retry.", retryable=True
            )
        return ProviderError("invalid_request", safe_message(raw, "Azure rejected the request."))

    def _group(self, conn: ConnectionInfo) -> str:
        return f"/subscriptions/{conn.subscription_id}/resourceGroups/{conn.resource_group}"

    def _environment_id(self, conn: ConnectionInfo) -> str:
        return f"{self._group(conn)}/providers/Microsoft.App/managedEnvironments/{conn.environment_name}"

    def _registry_id(self, conn: ConnectionInfo) -> str:
        return f"{self._group(conn)}/providers/Microsoft.ContainerRegistry/registries/{conn.registry_name}"

    def _identity_id(self, conn: ConnectionInfo) -> str:
        name = self._settings.azure_runtime_identity_name
        return f"{self._group(conn)}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{name}"

    def _app_id(self, conn: ConnectionInfo, app: AppTarget) -> str:
        return f"{self._group(conn)}/providers/Microsoft.App/containerApps/{app.name}"

    def _environment(self, conn: ConnectionInfo) -> dict[str, Any]:
        environment = self._request(
            conn, "GET", f"{ARM}{self._environment_id(conn)}?api-version={APPS_API}", allow_404=True
        )
        if environment is None:
            raise ProviderError(
                "not_found",
                f"The Container Apps environment {conn.environment_name} was not found in resource group "
                f"{conn.resource_group}.",
            )
        location = _location(environment.get("location"))
        if location != conn.region:
            raise ProviderError(
                "invalid_request",
                f"The Container Apps environment is in {location or 'another region'}, but the connection uses "
                f"{conn.region}. Choose the environment's region.",
            )
        return dict(environment)

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
            self._token(conn)
        except ProviderError as error:
            return finish(error, 0)
        update(0, "passed")

        progress("validating_permissions", list(checks))
        try:
            group = self._request(conn, "GET", f"{ARM}{self._group(conn)}?api-version={RG_API}", allow_404=True)
            if group is None:
                raise ProviderError(
                    "insufficient_permissions",
                    f"Resource group {conn.resource_group} was not found, or the app registration has no role on it.",
                )
        except ProviderError as error:
            return finish(error, 1)
        update(1, "passed")
        try:
            self._environment(conn)
        except ProviderError as error:
            return finish(error, 2)
        update(2, "passed")

        progress("checking_readiness", list(checks))
        if not conn.registry_name:
            update(3, "skipped", "No registry configured: building from a repository is unavailable.")
        else:
            try:
                registry = self._request(
                    conn, "GET", f"{ARM}{self._registry_id(conn)}?api-version={ACR_API}", allow_404=True
                )
                if registry is None:
                    raise ProviderError(
                        "not_found", f"Container registry {conn.registry_name} was not found in the resource group."
                    )
                update(3, "passed")
            except ProviderError as error:
                if error.code not in ("insufficient_permissions", "not_found"):
                    return finish(error, 3)
                update(3, "skipped", f"Not available: building from a repository will fail. {error.message}")
        try:
            identity = self._request(
                conn, "GET", f"{ARM}{self._identity_id(conn)}?api-version={IDENTITY_API}", allow_404=True
            )
            if identity is None:
                update(
                    4,
                    "skipped",
                    f"No managed identity named {self._settings.azure_runtime_identity_name}: private registry "
                    "images and Key Vault secrets are unavailable.",
                )
            else:
                update(4, "passed")
        except ProviderError as error:
            if error.retryable:
                return finish(error, 4)
            update(
                4, "skipped", f"Not available: private registry images and Key Vault secrets will fail. {error.message}"
            )
        progress("checking_readiness", list(checks))
        return ConnectionValidationResult("connected", None, list(checks))

    def check_access(self, conn: ConnectionInfo) -> None:
        self._environment(conn)

    # ----- build -----

    def _repository(self, app: AppTarget) -> str:
        return f"{self._settings.azure_acr_repository_prefix}/{app.name}"

    def prepare(self, conn: ConnectionInfo, app: AppTarget, needs_build: bool) -> PrepareResult:
        self.check_access(conn)
        if app.region != conn.region:
            raise ProviderError(
                "invalid_request",
                f"The connection's Container Apps environment is in {conn.region}; deploy the application there.",
            )
        if not needs_build:
            return PrepareResult(ready=True, messages=["Provider access confirmed."])
        if not conn.registry_name:
            raise ProviderError(
                "registry_not_configured",
                "Building from a repository needs an Azure Container Registry on the connection.",
            )
        registry = self._request(conn, "GET", f"{ARM}{self._registry_id(conn)}?api-version={ACR_API}", allow_404=True)
        if registry is None:
            raise ProviderError("not_found", f"Container registry {conn.registry_name} was not found.")
        return PrepareResult(ready=True, messages=["Provider access confirmed."])

    def start_build(
        self, conn: ConnectionInfo, app: AppTarget, source: SourceInfo, tag: str, token: str
    ) -> dict[str, Any]:
        ref = source.git_ref or ""
        context = (source.build_context or ".").strip("/") or "."
        dockerfile = source.dockerfile_path or "Dockerfile"
        if any(c in value for value in (ref, context) for c in "#:"):
            raise ProviderError(
                "invalid_request", "ACR Tasks cannot build a branch or build context containing '#' or ':'."
            )
        location = f"{source.repository_url}#{ref}" + ("" if context == "." else f":{context}")
        image = f"{self._repository(app)}:{tag}"
        body = {
            "type": "DockerBuildRequest",
            "imageNames": [image],
            "isPushEnabled": True,
            "noCache": False,
            "sourceLocation": location,
            "dockerFilePath": posixpath.relpath(dockerfile, context),
            "platform": {"os": "Linux", "architecture": "amd64"},
            "timeout": max(300, min(28800, self._settings.build_timeout_minutes * 60)),
            "agentConfiguration": {"cpu": 2},
        }
        run = self._request(
            conn, "POST", f"{ARM}{self._registry_id(conn)}/scheduleRun?api-version={ACR_RUNS_API}", json=body
        )
        run_id = (run.get("properties") or {}).get("runId") if isinstance(run, dict) else None
        if not run_id:
            raise ProviderError("provider_unavailable", "ACR Tasks did not return a run ID.", retryable=True)
        return {
            "run_id": str(run_id),
            "image_uri": f"{conn.registry_name}.azurecr.io/{image}",
            "commit": ref if source.ref_type == "commit" else None,
        }

    def poll_build(self, conn: ConnectionInfo, handle: dict[str, Any]) -> BuildStatus:
        run_url = f"{ARM}{self._registry_id(conn)}/runs/{handle['run_id']}"
        run = self._request(conn, "GET", f"{run_url}?api-version={ACR_RUNS_API}")
        properties = run.get("properties") or {}
        status = properties.get("status")
        if status in ("Queued", "Started", "Running"):
            return BuildStatus(state="running")
        lines = self._build_logs(conn, run_url)
        if status == "Succeeded":
            repository = str(handle["image_uri"]).rsplit(":", 1)[0]
            path = repository.split("/", 1)[1]
            digest = next(
                (i.get("digest") for i in properties.get("outputImages") or [] if i.get("repository") == path),
                None,
            )
            if not is_digest(digest):
                return BuildStatus(
                    state="failed", message="The build finished but did not report an image digest.", log_lines=lines
                )
            return BuildStatus(
                state="succeeded",
                image_uri=repository,
                image_digest=str(digest),
                commit=handle.get("commit"),
                log_lines=lines,
            )
        if status == "Canceled":
            return BuildStatus(state="cancelled", message="The build was cancelled.", log_lines=lines)
        if status == "Timeout":
            return BuildStatus(state="failed", message="The image build exceeded its time limit.", log_lines=lines)
        detail = str(properties.get("runErrorMessage") or "")
        message = safe_message(
            f"The image build failed: {detail}" if detail else "", "The image build failed. See the build logs."
        )
        return BuildStatus(state="failed", message=message, log_lines=lines)

    def _build_logs(self, conn: ConnectionInfo, run_url: str) -> list[tuple[dt.datetime, str]]:
        try:
            link = self._request(conn, "POST", f"{run_url}/listLogSasUrl?api-version={ACR_RUNS_API}").get("logLink")
            host = urlsplit(str(link or "")).hostname or ""
            if not link or urlsplit(str(link)).scheme != "https" or not host.endswith(".blob.core.windows.net"):
                return []
            with self._client_factory() as client:
                response = client.get(str(link))
            if response.status_code != 200:
                return []
        except (ProviderError, httpx.HTTPError):
            logger.info("Could not read ACR Tasks logs", extra={"connection_id": conn.id})
            return []
        now = clock.utcnow()
        text = response.text.replace("\r\n", "\n").replace("\r", "\n")
        return [(now, log_line(line)) for line in text.split("\n")[-300:] if line.strip()]

    def cancel_build(self, conn: ConnectionInfo, handle: dict[str, Any]) -> None:
        self._request(
            conn, "POST", f"{ARM}{self._registry_id(conn)}/runs/{handle['run_id']}/cancel?api-version={ACR_RUNS_API}"
        )

    # ----- images -----

    def resolve_image(self, conn: ConnectionInfo, image: str) -> ResolvedImage:
        ref = parse_image(image)
        if ACR_HOST.match(ref.host):
            return self._oci.resolve(
                ref, ref.host, ref.path, basic=(ZERO_GUID, self._acr_refresh_token(conn, ref.host))
            )
        if ref.host == MCR_HOST:
            return self._oci.resolve(ref, MCR_HOST, ref.path)
        if ref.host in DOCKER_HUB_HOSTS:
            path = ref.path if "/" in ref.path else f"library/{ref.path}"
            return self._oci.resolve(ref, "registry-1.docker.io", path)
        raise ProviderError(
            "unsupported_registry",
            "Azure Container Apps can run images from Azure Container Registry, Microsoft Container Registry "
            "or Docker Hub.",
        )

    def _acr_refresh_token(self, conn: ConnectionInfo, host: str) -> str:
        form = {
            "grant_type": "access_token",
            "service": host,
            "tenant": conn.tenant_id,
            "access_token": self._token(conn),
        }
        try:
            with self._client_factory() as client:
                response = client.post(f"https://{host}/oauth2/exchange", data=form)
        except httpx.TimeoutException:
            raise ProviderError(
                "timed_out", "The container registry did not respond in time.", retryable=True
            ) from None
        except httpx.HTTPError:
            raise ProviderError(
                "provider_unavailable", "The container registry could not be reached.", retryable=True
            ) from None
        if response.status_code in (401, 403):
            raise ProviderError(
                "image_access_denied",
                "CloudBridge is not allowed to read this registry. Grant the app registration AcrPull.",
            )
        if response.status_code == 404:
            raise ProviderError("image_not_found", "The container registry was not found.")
        try:
            token = response.json().get("refresh_token")
        except ValueError:
            token = None
        if response.status_code != 200 or not isinstance(token, str) or not token:
            raise ProviderError("provider_unavailable", "The container registry did not issue a token.", retryable=True)
        return token

    # ----- rollout -----

    def _app_body(
        self,
        conn: ConnectionInfo,
        app: AppTarget,
        image: ResolvedImage,
        spec: SpecInfo,
        location: str,
        existing_domains: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        host = image.repository.split("/", 1)[0]
        needs_identity = bool(spec.secret_refs) or bool(ACR_HOST.match(host))
        identity_id = self._identity_id(conn)
        if needs_identity:
            identity = self._request(conn, "GET", f"{ARM}{identity_id}?api-version={IDENTITY_API}", allow_404=True)
            if identity is None:
                raise ProviderError(
                    "runtime_identity_missing",
                    f"Private registry images and Key Vault secrets need a managed identity named "
                    f"{self._settings.azure_runtime_identity_name} in resource group {conn.resource_group}.",
                )
        env: list[dict[str, Any]] = [{"name": name, "value": value} for name, value in spec.environment]
        secrets: list[dict[str, Any]] = []
        for index, (name, reference) in enumerate(spec.secret_refs):
            secret_name = f"kv{index}-{_SECRET_NAME.sub('-', name.lower())}"[:60].rstrip("-")
            secrets.append({"name": secret_name, "keyVaultUrl": reference, "identity": identity_id})
            env.append({"name": name, "secretRef": secret_name})
        container: dict[str, Any] = {
            "name": app.name,
            "image": image.pinned,
            "resources": {"cpu": float(spec.cpu), "memory": _memory_to_azure(spec.memory)},
            "env": env,
        }
        worker = app.application_type == "worker"
        if spec.health_check_path and not worker:
            container["probes"] = [
                {
                    "type": "Startup",
                    "httpGet": {"path": spec.health_check_path, "port": spec.port},
                    "periodSeconds": 5,
                    "timeoutSeconds": 3,
                    "failureThreshold": 24,
                }
            ]
        concurrency = spec.concurrency or CONTAINER_APPS_DEFAULT_CONCURRENCY
        configuration: dict[str, Any] = {"activeRevisionsMode": "Single", "secrets": secrets}
        if not worker:
            configuration["ingress"] = {
                "external": spec.exposure == "public",
                "targetPort": spec.port,
                "transport": "auto",
                "allowInsecure": False,
                "traffic": [{"latestRevision": True, "weight": 100}],
            }
            if existing_domains:
                configuration["ingress"]["customDomains"] = existing_domains
        rules = (
            []
            if worker
            else [{"name": "http-concurrency", "http": {"metadata": {"concurrentRequests": str(concurrency)}}}]
        )
        if ACR_HOST.match(host):
            configuration["registries"] = [{"server": host, "identity": identity_id}]
        body: dict[str, Any] = {
            "location": location,
            "tags": {**MANAGED_TAGS, APP_TAG: app.id},
            "properties": {
                "environmentId": self._environment_id(conn),
                "configuration": configuration,
                "template": {
                    "containers": [container],
                    "scale": {"minReplicas": spec.min_instances, "maxReplicas": spec.max_instances, "rules": rules},
                },
            },
        }
        if needs_identity:
            body["identity"] = {"type": "UserAssigned", "userAssignedIdentities": {identity_id: {}}}
        return body

    def start_rollout(
        self, conn: ConnectionInfo, app: AppTarget, image: ResolvedImage, spec: SpecInfo
    ) -> dict[str, Any]:
        environment = self._environment(conn)
        resource_id = app.provider_service_id or self._app_id(conn, app)
        existing = self._request(conn, "GET", f"{ARM}{resource_id}?api-version={APPS_API}", allow_404=True)
        if existing is not None and (existing.get("tags") or {}).get(APP_TAG) != app.id:
            raise ProviderError(
                "service_name_taken",
                f"A container app named {app.name} already exists in resource group {conn.resource_group} "
                "and is not managed by this application.",
            )
        # Custom domains are managed separately; a new version keeps the ones already bound.
        domains = list(
            ((((existing or {}).get("properties") or {}).get("configuration") or {}).get("ingress") or {}).get(
                "customDomains"
            )
            or []
        )
        body = self._app_body(conn, app, image, spec, str(environment.get("location")), domains)
        self._request(conn, "PUT", f"{ARM}{resource_id}?api-version={APPS_API}", json=body)
        return {"service_id": resource_id, "image": image.pinned, "port": spec.port}

    def poll_rollout(self, conn: ConnectionInfo, handle: dict[str, Any]) -> RolloutStatus:
        resource_id = str(handle["service_id"])
        container_app = self._request(conn, "GET", f"{ARM}{resource_id}?api-version={APPS_API}", allow_404=True)
        if container_app is None:
            return RolloutStatus(state="failed", service_id=resource_id, message="The container app was not found.")
        properties = container_app.get("properties") or {}
        state = properties.get("provisioningState")
        if state in ("InProgress", "Deleting", None):
            return RolloutStatus(state="running", service_id=resource_id)
        if state in ("Failed", "Canceled"):
            return RolloutStatus(
                state="failed",
                service_id=resource_id,
                message="Azure could not deploy the new version. Check the image, port and health check.",
            )
        revision_name = properties.get("latestRevisionName")
        if not revision_name:
            return RolloutStatus(state="running", service_id=resource_id)
        revision = self._request(
            conn, "GET", f"{ARM}{resource_id}/revisions/{revision_name}?api-version={APPS_API}", allow_404=True
        )
        if revision is None:
            return RolloutStatus(state="running", service_id=resource_id)
        rp = revision.get("properties") or {}
        containers = (rp.get("template") or {}).get("containers") or [{}]
        if containers[0].get("image") != handle.get("image"):
            return RolloutStatus(state="running", service_id=resource_id)
        revision_state = rp.get("provisioningState")
        running = rp.get("runningState")
        if revision_state == "Failed" or running in ("Failed", "Degraded") or rp.get("healthState") == "Unhealthy":
            detail = str(rp.get("provisioningError") or rp.get("runningStateDetails") or "")
            return RolloutStatus(
                state="failed",
                service_id=resource_id,
                message=safe_message(
                    f"The new version did not start: {detail}" if detail else "",
                    "The new version did not start. Check the image, port and health check.",
                ),
            )
        if revision_state != "Provisioned" or running in ("Processing", "Activating", "Deprovisioning"):
            return RolloutStatus(state="running", service_id=resource_id)
        # A background worker has no ingress: Azure returns "ingress": null.
        fqdn = ((properties.get("configuration") or {}).get("ingress") or {}).get("fqdn")
        return RolloutStatus(
            state="succeeded",
            service_id=resource_id,
            service_url=f"https://{fqdn}" if fqdn else None,
            effective=self._effective(container_app, int(handle.get("port") or 80)),
        )

    def _effective(self, container_app: dict[str, Any], requested_port: int = 80) -> EffectiveConfig:
        properties = container_app.get("properties") or {}
        template = properties.get("template") or {}
        container = (template.get("containers") or [{}])[0]
        resources = container.get("resources") or {}
        ingress = (properties.get("configuration") or {}).get("ingress") or {}
        scale = template.get("scale") or {}
        concurrency = CONTAINER_APPS_DEFAULT_CONCURRENCY
        for rule in scale.get("rules") or []:
            value = ((rule.get("http") or {}).get("metadata") or {}).get("concurrentRequests")
            if value is not None:
                with contextlib.suppress(TypeError, ValueError):
                    concurrency = int(value)
        probe: dict[str, Any] = next((p for p in container.get("probes") or [] if p.get("type") == "Startup"), {})
        return EffectiveConfig(
            # A background worker has no ingress, so its port is whatever was requested.
            port=int(ingress.get("targetPort") or requested_port),
            cpu=_cpu_text(resources.get("cpu")),
            memory=_memory_from_azure(resources.get("memory")),
            min_instances=int(scale.get("minReplicas") or 0),
            max_instances=int(scale.get("maxReplicas") or 10),
            concurrency=concurrency,
            exposure="public" if ingress.get("external") else "private",
            health_check_path=(probe.get("httpGet") or {}).get("path"),
        )

    def check_service(self, conn: ConnectionInfo, app: AppTarget) -> ServiceState:
        resource_id = app.provider_service_id or self._app_id(conn, app)
        container_app = self._request(conn, "GET", f"{ARM}{resource_id}?api-version={APPS_API}", allow_404=True)
        if container_app is None:
            return ServiceState(running=False, message="The container app no longer exists.")
        properties = container_app.get("properties") or {}
        state = properties.get("provisioningState")
        if state in ("InProgress", "Deleting"):
            return ServiceState(running=None)
        if state != "Succeeded":
            return ServiceState(running=False, message="Azure reports the container app as not ready.")
        if properties.get("runningStatus") == "Stopped":
            return ServiceState(running=False, message="The container app is stopped.")
        return ServiceState(running=True)

    # ----- deletion -----

    def start_delete(self, conn: ConnectionInfo, app: AppTarget) -> dict[str, Any]:
        if not app.provider_service_id:
            return {"done": True}
        result = self._request(conn, "DELETE", f"{ARM}{app.provider_service_id}?api-version={APPS_API}", allow_404=True)
        if result is None:
            return {"done": True}
        return {"service_id": app.provider_service_id}

    def poll_delete(self, conn: ConnectionInfo, handle: dict[str, Any]) -> DeleteStatus:
        if handle.get("done") or not handle.get("service_id"):
            return DeleteStatus(state="deleted")
        container_app = self._request(
            conn, "GET", f"{ARM}{handle['service_id']}?api-version={APPS_API}", allow_404=True
        )
        if container_app is None:
            return DeleteStatus(state="deleted")
        state = (container_app.get("properties") or {}).get("provisioningState")
        if state == "Failed":
            return DeleteStatus(state="failed", message="Azure could not delete the container app.")
        return DeleteStatus(state="running")

    # ----- logs -----

    def runtime_logs(self, conn: ConnectionInfo, app: AppTarget, limit: int) -> tuple[list[RuntimeLogEntry], bool]:
        if not app.provider_service_id:
            return [], False
        environment = self._environment(conn)
        logs = (environment.get("properties") or {}).get("appLogsConfiguration") or {}
        workspace = (logs.get("logAnalyticsConfiguration") or {}).get("customerId")
        if logs.get("destination") != "log-analytics" or not workspace:
            raise ProviderError(
                "logs_unavailable", "The Container Apps environment does not send logs to a Log Analytics workspace."
            )
        query = (
            "ContainerAppConsoleLogs_CL"
            f" | where ContainerAppName_s == '{app.name}'"
            " | where TimeGenerated > ago(1h)"
            " | project TimeGenerated, Stream_s, Log_s"
            " | order by TimeGenerated desc"
            f" | take {limit + 1}"
        )
        data = self._request(
            conn,
            "POST",
            f"{LOG_ANALYTICS}/v1/workspaces/{workspace}/query",
            json={"query": query},
            scope=LOGS_SCOPE,
        )
        rows: list[Any] = []
        for table in data.get("tables") or []:
            rows.extend(table.get("rows") or [])
        entries: list[RuntimeLogEntry] = []
        for row in reversed(rows):
            if not isinstance(row, list) or len(row) < 3 or row[2] is None:
                continue
            stream = "stderr" if row[1] == "stderr" else "stdout"
            entries.append(
                RuntimeLogEntry(
                    timestamp=_parse_time(row[0]),
                    stream=stream,  # type: ignore[arg-type]
                    message=log_line(str(row[2])),
                )
            )
        truncated = len(entries) > limit
        return entries[-limit:], truncated

    def service_host_allowed(self, host: str) -> bool:
        return host.endswith(".azurecontainerapps.io")

    # ----- metrics -----

    def metrics(self, conn: ConnectionInfo, app: AppTarget, minutes: int, step_seconds: int) -> list[MetricSeries]:
        if not app.provider_service_id:
            return []
        end = clock.utcnow()
        start = end - dt.timedelta(minutes=minutes)
        span = f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        interval = _interval(step_seconds)
        base = f"{ARM}{app.provider_service_id}/providers/Microsoft.Insights/metrics?api-version={METRICS_API}"
        common = f"&timespan={span}&interval={interval}"
        series: dict[MetricName, MetricSeries] = {}
        data = self._request(
            conn,
            "GET",
            f"{base}{common}&metricnames={','.join(m for m, _, _, _ in AZURE_METRICS)}"
            "&aggregation=Total,Average,Maximum",
        )
        by_name = {str((item.get("name") or {}).get("value")): item for item in data.get("value") or []}
        for metric, aggregation, name, unit in AZURE_METRICS:
            item = by_name.get(metric) or {}
            points = [
                MetricPoint(timestamp=_parse_time(p.get("timeStamp")), value=float(p[aggregation]))
                for ts in item.get("timeseries") or []
                for p in ts.get("data") or []
                if p.get(aggregation) is not None
            ]
            series[name] = MetricSeries(name=name, unit=unit, points=points)
        # Requests split by status class gives the 4xx and 5xx series.
        split = self._request(
            conn,
            "GET",
            f"{base}{common}&metricnames=Requests&aggregation=Total&$filter=statusCodeCategory eq '*'",
        )
        for item in split.get("value") or []:
            for ts in item.get("timeseries") or []:
                category = next(
                    (
                        str(m.get("value"))
                        for m in ts.get("metadatavalues") or []
                        if (m.get("name") or {}).get("value", "").lower() == "statuscodecategory"
                    ),
                    "",
                )
                key: MetricName | None = {"4xx": "errors_4xx", "5xx": "errors_5xx"}.get(category)  # type: ignore[assignment]
                if key is None:
                    continue
                points = [
                    MetricPoint(timestamp=_parse_time(p.get("timeStamp")), value=float(p["total"]))
                    for p in ts.get("data") or []
                    if p.get("total") is not None
                ]
                series[key] = MetricSeries(name=key, unit="count", points=points)
        for key in ("errors_4xx", "errors_5xx"):
            series.setdefault(key, MetricSeries(name=key, unit="count", points=[]))  # type: ignore[arg-type]
        order: tuple[MetricName, ...] = (
            "requests",
            "errors_4xx",
            "errors_5xx",
            "latency_ms",
            "instances",
            "cpu_percent",
            "memory_percent",
        )
        return [series[name] for name in order if name in series]

    # ----- custom domains -----

    def _domain_records(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> list[DnsRecord]:
        environment = self._environment(conn)
        props = environment.get("properties") or {}
        verification = (props.get("customDomainConfiguration") or {}).get("customDomainVerificationId")
        container_app = self._request(conn, "GET", f"{ARM}{app.provider_service_id}?api-version={APPS_API}")
        fqdn = ((((container_app.get("properties") or {}).get("configuration") or {}).get("ingress")) or {}).get("fqdn")
        records: list[DnsRecord] = []
        if domain.count(".") == 1:
            if props.get("staticIp"):
                records.append(
                    DnsRecord(type="A", name=domain, value=str(props["staticIp"]), purpose="Routes traffic to the app")
                )
        elif fqdn:
            records.append(DnsRecord(type="CNAME", name=domain, value=str(fqdn), purpose="Routes traffic to the app"))
        if verification:
            records.append(
                DnsRecord(
                    type="TXT",
                    name=f"asuid.{domain}",
                    value=str(verification),
                    purpose="Proves you own the domain",
                )
            )
        return records

    def add_domain(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> DomainState:
        if not app.provider_service_id:
            raise ProviderError("not_deployed", "Deploy the application before adding a domain.")
        if app.application_type == "worker":
            raise ProviderError("not_supported", "Background workers have no web address to put a domain on.")
        return self.domain_status(conn, app, domain)

    def _certificate_id(self, conn: ConnectionInfo, domain: str) -> str:
        name = "cb-" + _SECRET_NAME.sub("-", domain.lower())[:55].strip("-")
        return f"{self._environment_id(conn)}/managedCertificates/{name}"

    def domain_status(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> DomainState:
        if not app.provider_service_id:
            return DomainState(status="failed", records=[], message="The application is not deployed.")
        records = self._domain_records(conn, app, domain)
        container_app = self._request(conn, "GET", f"{ARM}{app.provider_service_id}?api-version={APPS_API}")
        ingress = dict(((container_app.get("properties") or {}).get("configuration") or {}).get("ingress") or {})
        bound = [d for d in ingress.get("customDomains") or [] if str(d.get("name", "")).lower() == domain]
        if bound and bound[0].get("bindingType") == "SniEnabled":
            return DomainState(status="active", records=records)
        try:
            analysis = self._request(
                conn,
                "POST",
                f"{ARM}{app.provider_service_id}/listCustomHostNameAnalysis?customHostname={domain}&api-version={APPS_API}",
            )
        except ProviderError as error:
            if error.code in ("invalid_credentials", "insufficient_permissions"):
                raise
            # Azure answers 500 for a host name that does not resolve yet; the records are still what to create.
            return DomainState(
                status="pending_dns", records=records, message="Create the DNS records below; Azure then verifies them."
            )
        props = analysis.get("properties", analysis)
        if str(props.get("customDomainVerificationTest", "")).lower() != "passed":
            failure = (props.get("customDomainVerificationFailureInfo") or {}).get("message")
            return DomainState(
                status="pending_dns",
                records=records,
                message=safe_message(
                    f"Azure has not found the DNS records yet: {failure}" if failure else "",
                    "Create the DNS records below; Azure then verifies them.",
                ),
            )
        certificate_id = self._certificate_id(conn, domain)
        certificate = self._request(conn, "GET", f"{ARM}{certificate_id}?api-version={APPS_API}", allow_404=True)
        if certificate is None:
            location = self._environment(conn).get("location")
            self._request(
                conn,
                "PUT",
                f"{ARM}{certificate_id}?api-version={APPS_API}",
                json={"location": location, "properties": {"subjectName": domain, "domainControlValidation": "CNAME"}},
            )
            return DomainState(status="verifying", records=records, message="Azure is issuing the certificate.")
        state = str((certificate.get("properties") or {}).get("provisioningState", ""))
        if state.lower() == "failed":
            return DomainState(status="failed", records=records, message="Azure could not issue the certificate.")
        if state.lower() != "succeeded":
            return DomainState(status="verifying", records=records, message="Azure is issuing the certificate.")
        domains = [d for d in ingress.get("customDomains") or [] if str(d.get("name", "")).lower() != domain]
        domains.append({"name": domain, "bindingType": "SniEnabled", "certificateId": certificate_id})
        ingress["customDomains"] = domains
        self._request(
            conn,
            "PATCH",
            f"{ARM}{app.provider_service_id}?api-version={APPS_API}",
            json={"properties": {"configuration": {"ingress": ingress}}},
        )
        return DomainState(status="verifying", records=records, message="Binding the certificate to the app.")

    def remove_domain(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> None:
        if app.provider_service_id:
            container_app = self._request(
                conn, "GET", f"{ARM}{app.provider_service_id}?api-version={APPS_API}", allow_404=True
            )
            if container_app is not None:
                ingress = dict(
                    ((container_app.get("properties") or {}).get("configuration") or {}).get("ingress") or {}
                )
                kept = [d for d in ingress.get("customDomains") or [] if str(d.get("name", "")).lower() != domain]
                if len(kept) != len(ingress.get("customDomains") or []):
                    ingress["customDomains"] = kept
                    self._request(
                        conn,
                        "PATCH",
                        f"{ARM}{app.provider_service_id}?api-version={APPS_API}",
                        json={"properties": {"configuration": {"ingress": ingress}}},
                    )
        try:
            self._request(
                conn, "DELETE", f"{ARM}{self._certificate_id(conn, domain)}?api-version={APPS_API}", allow_404=True
            )
        except ProviderError as error:
            if error.code != "conflict":
                raise

    # ----- pause and resume -----

    def set_paused(self, conn: ConnectionInfo, app: AppTarget, paused: bool) -> None:
        if not app.provider_service_id:
            raise ProviderError("not_deployed", "The application is not deployed.")
        action = "stop" if paused else "start"
        self._request(conn, "POST", f"{ARM}{app.provider_service_id}/{action}?api-version={APPS_API}")


# (Azure Monitor metric, aggregation, CloudBridge series, unit)
AZURE_METRICS: tuple[tuple[str, str, MetricName, str], ...] = (
    ("Requests", "total", "requests", "count"),
    ("ResponseTime", "average", "latency_ms", "ms"),
    ("Replicas", "maximum", "instances", "count"),
    ("CpuPercentage", "average", "cpu_percent", "percent"),
    ("MemoryPercentage", "average", "memory_percent", "percent"),
)


def _interval(step_seconds: int) -> str:
    for seconds, value in ((60, "PT1M"), (300, "PT5M"), (900, "PT15M"), (1800, "PT30M")):
        if step_seconds <= seconds:
            return value
    return "PT1H"


def _location(value: Any) -> str:
    return str(value or "").replace(" ", "").lower()


def _parse_time(value: Any) -> dt.datetime:
    if isinstance(value, str):
        try:
            return clock.as_utc(dt.datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            pass
    return clock.utcnow()
