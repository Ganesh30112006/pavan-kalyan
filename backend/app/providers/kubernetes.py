"""Kubernetes: deploy to an existing cluster through its API server.

No kubeconfig credentials are stored. AKS clusters are reached with a Microsoft Entra token for the
connection's app registration (Azure RBAC for Kubernetes grants it rights in one namespace); other
clusters can trust CloudBridge's OIDC token directly (structured authentication). The connection holds
only the API server address, its public CA certificate and the namespace.

Each application becomes a Deployment, a Service (LoadBalancer when public, ClusterIP when private) and,
when it can scale, a HorizontalPodAutoscaler, all written with server-side apply under the
`cloudbridge` field manager.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import logging
import re
import ssl
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

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
    EffectiveConfig,
    ExtrasNotSupported,
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
from app.providers.capabilities import ACR_HOST, DOCKER_HUB_HOSTS, ECR_PUBLIC_HOST, GHCR_HOST, MCR_HOST, QUAY_HOST
from app.providers.entra import FederatedIdentity, TokenSource
from app.providers.images import OciResolver, parse_image
from app.redaction import log_line, safe_message

logger = logging.getLogger(__name__)

MANAGER = "cloudbridge"
APP_ANNOTATION = "cloudbridge.io/application-id"
NAME_LABEL = "app.kubernetes.io/name"
MANAGED_LABEL = "app.kubernetes.io/managed-by"
CHECKS = (
    "Get a cluster token",
    "Reach the API server",
    "Namespace access",
    "Permission to manage workloads",
    "Metrics API (optional)",
)
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
CPU_TARGET_PERCENT = 70
PROGRESS_DEADLINE_SECONDS = 300
FAILING_REASONS = {
    "ErrImagePull",
    "ImagePullBackOff",
    "InvalidImageName",
    "CrashLoopBackOff",
    "CreateContainerConfigError",
    "CreateContainerError",
    "RunContainerError",
}
ARM_SCOPE = "https://management.azure.com/.default"
_LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T[0-9:.]+Z) (.*)$")


def _realm_allowed(host: str) -> bool:
    return host in ("auth.docker.io", GHCR_HOST, QUAY_HOST, ECR_PUBLIC_HOST) or bool(ACR_HOST.match(host))


def _quantity_cpu(cpu: str) -> str:
    return f"{round(float(cpu) * 1000)}m"


def _parse_cpu(value: Any) -> float:
    """Kubernetes CPU quantity to cores ("250m" -> 0.25, "1" -> 1, "1500000n" -> 0.0015)."""
    text = str(value or "0")
    units = {"n": 1e-9, "u": 1e-6, "m": 1e-3}
    try:
        return float(text[:-1]) * units[text[-1]] if text and text[-1] in units else float(text)
    except ValueError:
        return 0.0


def _parse_bytes(value: Any) -> float:
    text = str(value or "0")
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "K": 1000, "M": 1000**2, "G": 1000**3}
    for suffix, factor in units.items():
        if text.endswith(suffix):
            try:
                return float(text[: -len(suffix)]) * factor
            except ValueError:
                return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _cpu_text(value: Any) -> str:
    return f"{_parse_cpu(value):g}"


class KubernetesAdapter(ExtrasNotSupported):
    kind = "kubernetes"
    label = "Kubernetes"
    supports_repository_builds = False
    plain_http_allowed = True

    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[ConnectionInfo | None], httpx.Client] | None = None,
        token_source: TokenSource | None = None,
        oci: OciResolver | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or self._default_client
        self._identity = FederatedIdentity(settings, lambda: self._client_factory(None), token_source)
        self._oci = oci or OciResolver(
            realm_allowed=_realm_allowed,
            client_factory=(lambda: self._client_factory(None)) if client_factory else None,
        )

    @staticmethod
    def _default_client(conn: ConnectionInfo | None) -> httpx.Client:
        if conn is None or not conn.ca_certificate:
            return httpx.Client(timeout=TIMEOUT, follow_redirects=False)
        context = ssl.create_default_context(cadata=conn.ca_certificate)
        return httpx.Client(timeout=TIMEOUT, follow_redirects=False, verify=context)

    # ----- credentials and HTTP -----

    def _token(self, conn: ConnectionInfo) -> str:
        if conn.kubernetes_auth == "aks":
            if not conn.tenant_id or not conn.client_id:
                raise ProviderError("invalid_credentials", "The connection has no tenant or client ID.")
            return self._identity.entra_token(
                conn.tenant_id, conn.client_id, self._settings.kubernetes_aks_scope, conn.id
            )
        if conn.kubernetes_auth == "oidc" and conn.token_audience:
            return self._identity.assertion(conn.token_audience)
        raise ProviderError("invalid_credentials", "The connection does not say how to authenticate to the cluster.")

    def _url(self, conn: ConnectionInfo, path: str) -> str:
        if not conn.api_server:
            raise ProviderError("invalid_credentials", "The connection has no API server address.")
        return f"{conn.api_server.rstrip('/')}{path}"

    def _request(
        self,
        conn: ConnectionInfo,
        method: str,
        path: str,
        *,
        body: Any = None,
        apply: bool = False,
        allow_404: bool = False,
        params: dict[str, str] | None = None,
        text: bool = False,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self._token(conn)}", "Accept": "application/json"}
        content: bytes | None = None
        if body is not None:
            headers["Content-Type"] = (
                "application/apply-patch+yaml"
                if apply
                else ("application/merge-patch+json" if method == "PATCH" else "application/json")
            )
            content = json.dumps(body).encode("utf-8")
        query = dict(params or {})
        if apply:
            query.update({"fieldManager": MANAGER, "force": "true"})
        try:
            with self._client_factory(conn) as client:
                response = client.request(method, self._url(conn, path), headers=headers, content=content, params=query)
        except httpx.TimeoutException:
            raise ProviderError(
                "timed_out", "The cluster's API server did not respond in time.", retryable=True
            ) from None
        except httpx.HTTPError:
            raise ProviderError(
                "provider_unavailable", "The cluster's API server could not be reached.", retryable=True
            ) from None
        status = response.status_code
        if 200 <= status < 300:
            if text:
                return response.text
            return response.json() if response.content else {}
        if status == 404 and allow_404:
            return None
        raise self._error(response)

    def _error(self, response: httpx.Response) -> ProviderError:
        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = {}
        raw = str(body.get("message", "")) if isinstance(body, dict) else ""
        if status == 401:
            return ProviderError("invalid_credentials", "The cluster rejected CloudBridge's token.")
        if status == 403:
            return ProviderError(
                "insufficient_permissions", safe_message(raw, "The cluster's RBAC does not allow this action.")
            )
        if status == 404:
            return ProviderError("not_found", safe_message(raw, "The Kubernetes resource was not found."))
        if status == 409:
            return ProviderError("conflict", safe_message(raw, "The Kubernetes resource changed."), retryable=True)
        if status == 429 or status >= 500:
            return ProviderError("provider_unavailable", "The cluster is busy. CloudBridge will retry.", retryable=True)
        return ProviderError("invalid_request", safe_message(raw, "The cluster rejected the request."))

    def _ns(self, conn: ConnectionInfo) -> str:
        if not conn.namespace:
            raise ProviderError("invalid_credentials", "The connection has no namespace.")
        return quote(conn.namespace, safe="")

    def _deployment_path(self, conn: ConnectionInfo, name: str) -> str:
        return f"/apis/apps/v1/namespaces/{self._ns(conn)}/deployments/{quote(name, safe='')}"

    def _service_path(self, conn: ConnectionInfo, name: str) -> str:
        return f"/api/v1/namespaces/{self._ns(conn)}/services/{quote(name, safe='')}"

    def _hpa_path(self, conn: ConnectionInfo, name: str) -> str:
        return f"/apis/autoscaling/v2/namespaces/{self._ns(conn)}/horizontalpodautoscalers/{quote(name, safe='')}"

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
        try:
            version = self._request(conn, "GET", "/version")
            update(1, "passed", f"Kubernetes {version.get('gitVersion', '')}".strip())
        except ProviderError as error:
            return finish(error, 1)

        progress("validating_permissions", list(checks))
        try:
            self._request(conn, "GET", f"/apis/apps/v1/namespaces/{self._ns(conn)}/deployments", params={"limit": "1"})
        except ProviderError as error:
            return finish(error, 2)
        update(2, "passed")
        missing = []
        for group, resource in (("apps", "deployments"), ("", "services"), ("autoscaling", "horizontalpodautoscalers")):
            for verb in ("create", "patch", "delete"):
                review = {
                    "apiVersion": "authorization.k8s.io/v1",
                    "kind": "SelfSubjectAccessReview",
                    "spec": {
                        "resourceAttributes": {
                            "namespace": conn.namespace,
                            "group": group,
                            "resource": resource,
                            "verb": verb,
                        }
                    },
                }
                try:
                    result = self._request(
                        conn, "POST", "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews", body=review
                    )
                except ProviderError as error:
                    return finish(error, 3)
                if not (result.get("status") or {}).get("allowed"):
                    missing.append(f"{verb} {resource}")
        if missing:
            denied = ProviderError(
                "insufficient_permissions", f"CloudBridge may not {', '.join(missing)} in namespace {conn.namespace}."
            )
            return finish(denied, 3)
        update(3, "passed")

        progress("checking_readiness", list(checks))
        try:
            self._request(
                conn, "GET", f"/apis/metrics.k8s.io/v1beta1/namespaces/{self._ns(conn)}/pods", params={"limit": "1"}
            )
            update(4, "passed")
        except ProviderError as error:
            if error.retryable:
                return finish(error, 4)
            update(4, "skipped", "Not available: CPU and memory charts need the cluster's metrics server.")
        progress("checking_readiness", list(checks))
        return ConnectionValidationResult("connected", None, list(checks))

    def check_access(self, conn: ConnectionInfo) -> None:
        self._request(conn, "GET", f"/apis/apps/v1/namespaces/{self._ns(conn)}/deployments", params={"limit": "1"})

    # ----- builds are not offered for Kubernetes -----

    def prepare(self, conn: ConnectionInfo, app: AppTarget, needs_build: bool) -> PrepareResult:
        if needs_build:
            raise ProviderError(
                "builds_not_supported", "Kubernetes deploys images; building from a repository is not available."
            )
        self.check_access(conn)
        return PrepareResult(ready=True, messages=["Cluster access confirmed."])

    def start_build(
        self, conn: ConnectionInfo, app: AppTarget, source: SourceInfo, tag: str, token: str
    ) -> dict[str, Any]:
        raise ProviderError(
            "builds_not_supported", "Kubernetes deploys images; building from a repository is not available."
        )

    def poll_build(self, conn: ConnectionInfo, handle: dict[str, Any]) -> BuildStatus:
        return BuildStatus(state="failed", message="Kubernetes does not build images.")

    def cancel_build(self, conn: ConnectionInfo, handle: dict[str, Any]) -> None:
        return None

    # ----- images -----

    def resolve_image(self, conn: ConnectionInfo, image: str) -> ResolvedImage:
        ref = parse_image(image)
        if ref.host in DOCKER_HUB_HOSTS:
            path = ref.path if "/" in ref.path else f"library/{ref.path}"
            return self._oci.resolve(ref, "registry-1.docker.io", path)
        if ref.host in (MCR_HOST, GHCR_HOST, QUAY_HOST, ECR_PUBLIC_HOST):
            return self._oci.resolve(ref, ref.host, ref.path)
        if ACR_HOST.match(ref.host):
            basic = ("00000000-0000-0000-0000-000000000000", self._acr_refresh_token(conn, ref.host))
            return self._oci.resolve(ref, ref.host, ref.path, basic=basic)
        raise ProviderError(
            "unsupported_registry",
            "Kubernetes can run images from Docker Hub, GitHub, Quay, Microsoft, ECR Public or "
            "Azure Container Registry.",
        )

    def _acr_refresh_token(self, conn: ConnectionInfo, host: str) -> str:
        if conn.kubernetes_auth != "aks" or not conn.tenant_id or not conn.client_id:
            raise ProviderError(
                "image_access_denied", "CloudBridge reads Azure Container Registry images only for AKS connections."
            )
        token = self._identity.entra_token(conn.tenant_id, conn.client_id, ARM_SCOPE, conn.id)
        form = {"grant_type": "access_token", "service": host, "tenant": conn.tenant_id, "access_token": token}
        try:
            with self._client_factory(None) as client:
                response = client.post(f"https://{host}/oauth2/exchange", data=form)
        except httpx.HTTPError:
            raise ProviderError(
                "provider_unavailable", "The container registry could not be reached.", retryable=True
            ) from None
        if response.status_code in (401, 403):
            raise ProviderError("image_access_denied", "CloudBridge is not allowed to read this registry (AcrPull).")
        try:
            refresh = response.json().get("refresh_token")
        except ValueError:
            refresh = None
        if response.status_code != 200 or not isinstance(refresh, str):
            raise ProviderError("provider_unavailable", "The container registry did not issue a token.", retryable=True)
        return refresh

    # ----- rollout -----

    def _labels(self, app: AppTarget) -> dict[str, str]:
        return {NAME_LABEL: app.name, MANAGED_LABEL: MANAGER}

    def _env(self, spec: SpecInfo) -> list[dict[str, Any]]:
        env: list[dict[str, Any]] = [{"name": name, "value": value} for name, value in spec.environment]
        for name, reference in spec.secret_refs:
            secret, _, key = reference.removeprefix("k8s:").partition("/")
            env.append({"name": name, "valueFrom": {"secretKeyRef": {"name": secret, "key": key}}})
        return env

    def _deployment(self, conn: ConnectionInfo, app: AppTarget, image: ResolvedImage, spec: SpecInfo) -> dict[str, Any]:
        worker = app.application_type == "worker"
        container: dict[str, Any] = {
            "name": "app",
            "image": image.pinned,
            "env": self._env(spec),
            "resources": {
                "requests": {"cpu": _quantity_cpu(spec.cpu), "memory": spec.memory},
                "limits": {"memory": spec.memory},
            },
        }
        if not worker:
            container["ports"] = [{"name": "http", "containerPort": spec.port}]
            if spec.health_check_path:
                container["readinessProbe"] = {
                    "httpGet": {"path": spec.health_check_path, "port": spec.port},
                    "periodSeconds": 5,
                    "timeoutSeconds": 3,
                    "failureThreshold": 3,
                }
        deployment_spec: dict[str, Any] = {
            "progressDeadlineSeconds": PROGRESS_DEADLINE_SECONDS,
            "revisionHistoryLimit": 5,
            "selector": {"matchLabels": {NAME_LABEL: app.name}},
            "template": {
                "metadata": {"labels": self._labels(app), "annotations": {APP_ANNOTATION: app.id}},
                "spec": {"containers": [container], "automountServiceAccountToken": False},
            },
        }
        if spec.max_instances <= max(spec.min_instances, 1):
            # Fixed size; when the application can scale, the HorizontalPodAutoscaler owns the replica count.
            deployment_spec["replicas"] = max(spec.min_instances, 1)
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": app.name,
                "namespace": conn.namespace,
                "labels": self._labels(app),
                "annotations": {APP_ANNOTATION: app.id},
            },
            "spec": deployment_spec,
        }

    def _service(self, conn: ConnectionInfo, app: AppTarget, spec: SpecInfo) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": app.name,
                "namespace": conn.namespace,
                "labels": self._labels(app),
                "annotations": {APP_ANNOTATION: app.id},
            },
            "spec": {
                "type": "LoadBalancer" if spec.exposure == "public" else "ClusterIP",
                "selector": {NAME_LABEL: app.name},
                "ports": [{"name": "http", "port": 80, "targetPort": spec.port, "protocol": "TCP"}],
            },
        }

    def _hpa(self, conn: ConnectionInfo, app: AppTarget, spec: SpecInfo) -> dict[str, Any]:
        return {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": app.name, "namespace": conn.namespace, "labels": self._labels(app)},
            "spec": {
                "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": app.name},
                "minReplicas": max(spec.min_instances, 1),
                "maxReplicas": spec.max_instances,
                "metrics": [
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "cpu",
                            "target": {"type": "Utilization", "averageUtilization": CPU_TARGET_PERCENT},
                        },
                    }
                ],
            },
        }

    def start_rollout(
        self, conn: ConnectionInfo, app: AppTarget, image: ResolvedImage, spec: SpecInfo
    ) -> dict[str, Any]:
        path = self._deployment_path(conn, app.name)
        existing = self._request(conn, "GET", path, allow_404=True)
        if existing is not None:
            owner = ((existing.get("metadata") or {}).get("annotations") or {}).get(APP_ANNOTATION)
            if owner != app.id:
                raise ProviderError(
                    "service_name_taken",
                    f"A Deployment named {app.name} already exists in namespace {conn.namespace} "
                    "and is not managed by this application.",
                )
        applied = self._request(conn, "PATCH", path, body=self._deployment(conn, app, image, spec), apply=True)
        worker = app.application_type == "worker"
        service_path = self._service_path(conn, app.name)
        if worker:
            self._request(conn, "DELETE", service_path, allow_404=True)
        else:
            self._request(conn, "PATCH", service_path, body=self._service(conn, app, spec), apply=True)
        hpa_path = self._hpa_path(conn, app.name)
        if spec.max_instances > max(spec.min_instances, 1):
            self._request(conn, "PATCH", hpa_path, body=self._hpa(conn, app, spec), apply=True)
        else:
            self._request(conn, "DELETE", hpa_path, allow_404=True)
        generation = int((applied.get("metadata") or {}).get("generation") or 1)
        return {
            "service_id": f"{conn.namespace}/{app.name}",
            "name": app.name,
            "generation": generation,
            "image": image.pinned,
            "public": spec.exposure == "public" and not worker,
            "worker": worker,
            "port": spec.port,
        }

    def _failing_pods(self, conn: ConnectionInfo, name: str, image: str) -> str | None:
        pods = self._request(
            conn,
            "GET",
            f"/api/v1/namespaces/{self._ns(conn)}/pods",
            params={"labelSelector": f"{NAME_LABEL}={name}"},
        )
        for pod in pods.get("items") or []:
            containers = (pod.get("spec") or {}).get("containers") or []
            if not any(c.get("image") == image for c in containers):
                continue
            for status in (pod.get("status") or {}).get("containerStatuses") or []:
                waiting = (status.get("state") or {}).get("waiting") or {}
                if waiting.get("reason") in FAILING_REASONS:
                    detail = str(waiting.get("message") or "")
                    return f"{waiting['reason']}{': ' + detail if detail else ''}"
        return None

    def poll_rollout(self, conn: ConnectionInfo, handle: dict[str, Any]) -> RolloutStatus:
        name = str(handle["name"])
        service_id = str(handle["service_id"])
        deployment = self._request(conn, "GET", self._deployment_path(conn, name), allow_404=True)
        if deployment is None:
            return RolloutStatus(state="failed", service_id=service_id, message="The Deployment was not found.")
        status = deployment.get("status") or {}
        for condition in status.get("conditions") or []:
            if condition.get("type") == "Progressing" and condition.get("reason") == "ProgressDeadlineExceeded":
                reason = self._failing_pods(conn, name, str(handle["image"]))
                return RolloutStatus(
                    state="failed",
                    service_id=service_id,
                    message=safe_message(
                        f"The new version did not become ready: {reason}" if reason else "",
                        "The new version did not become ready in time. Check the image, port and health check.",
                    ),
                )
        reason = self._failing_pods(conn, name, str(handle["image"]))
        if reason:
            return RolloutStatus(
                state="failed",
                service_id=service_id,
                message=safe_message(f"The new version cannot start: {reason}", "The new version cannot start."),
            )
        wanted = int((deployment.get("spec") or {}).get("replicas") or 1)
        observed = int(status.get("observedGeneration") or 0)
        generation = int((deployment.get("metadata") or {}).get("generation") or 0)
        ready = (
            observed >= generation
            and int(status.get("updatedReplicas") or 0) >= wanted
            and int(status.get("availableReplicas") or 0) >= wanted
            and int(status.get("replicas") or 0) == int(status.get("updatedReplicas") or 0)
        )
        if not ready:
            return RolloutStatus(state="running", service_id=service_id)
        url: str | None = None
        if handle.get("public"):
            url = self._load_balancer_url(conn, name)
            if url is None:
                return RolloutStatus(state="running", service_id=service_id)
        return RolloutStatus(
            state="succeeded",
            service_id=service_id,
            service_url=url,
            effective=self._effective(conn, deployment, name, int(handle.get("port") or 80)),
        )

    def _load_balancer_url(self, conn: ConnectionInfo, name: str) -> str | None:
        service = self._request(conn, "GET", self._service_path(conn, name), allow_404=True)
        for ingress in (((service or {}).get("status") or {}).get("loadBalancer") or {}).get("ingress") or []:
            host = ingress.get("ip") or ingress.get("hostname")
            if host:
                return f"http://{host}"
        return None

    def _effective(self, conn: ConnectionInfo, deployment: dict[str, Any], name: str, port: int) -> EffectiveConfig:
        template = ((deployment.get("spec") or {}).get("template") or {}).get("spec") or {}
        container = (template.get("containers") or [{}])[0]
        resources = container.get("resources") or {}
        ports = container.get("ports") or []
        probe = (container.get("readinessProbe") or {}).get("httpGet") or {}
        hpa = self._request(conn, "GET", self._hpa_path(conn, name), allow_404=True)
        replicas = int((deployment.get("spec") or {}).get("replicas") or 1)
        minimum = int(((hpa or {}).get("spec") or {}).get("minReplicas") or replicas)
        maximum = int(((hpa or {}).get("spec") or {}).get("maxReplicas") or replicas)
        service = self._request(conn, "GET", self._service_path(conn, name), allow_404=True)
        public = ((service or {}).get("spec") or {}).get("type") == "LoadBalancer"
        return EffectiveConfig(
            port=int(ports[0].get("containerPort")) if ports else port,
            cpu=_cpu_text((resources.get("requests") or {}).get("cpu")),
            memory=str((resources.get("requests") or {}).get("memory") or "512Mi"),
            min_instances=minimum,
            max_instances=maximum,
            concurrency=None,
            exposure="public" if public else "private",
            health_check_path=probe.get("path"),
        )

    def check_service(self, conn: ConnectionInfo, app: AppTarget) -> ServiceState:
        deployment = self._request(conn, "GET", self._deployment_path(conn, app.name), allow_404=True)
        if deployment is None:
            return ServiceState(running=False, message="The Deployment no longer exists.")
        status = deployment.get("status") or {}
        wanted = int((deployment.get("spec") or {}).get("replicas") or 0)
        if wanted == 0:
            return ServiceState(running=False, message="The Deployment is scaled to zero.")
        if int(status.get("availableReplicas") or 0) >= 1:
            return ServiceState(running=True)
        if int(status.get("observedGeneration") or 0) < int((deployment.get("metadata") or {}).get("generation") or 0):
            return ServiceState(running=None)
        return ServiceState(running=False, message="No pod of the Deployment is ready.")

    # ----- deletion -----

    def start_delete(self, conn: ConnectionInfo, app: AppTarget) -> dict[str, Any]:
        for path in (
            self._hpa_path(conn, app.name),
            self._service_path(conn, app.name),
        ):
            self._request(conn, "DELETE", path, allow_404=True)
        self._request(
            conn,
            "DELETE",
            self._deployment_path(conn, app.name),
            body={"propagationPolicy": "Foreground"},
            allow_404=True,
        )
        return {"name": app.name}

    def poll_delete(self, conn: ConnectionInfo, handle: dict[str, Any]) -> DeleteStatus:
        name = str(handle["name"])
        if self._request(conn, "GET", self._deployment_path(conn, name), allow_404=True) is not None:
            return DeleteStatus(state="running")
        if self._request(conn, "GET", self._service_path(conn, name), allow_404=True) is not None:
            return DeleteStatus(state="running")
        return DeleteStatus(state="deleted")

    # ----- logs and metrics -----

    def _pods(self, conn: ConnectionInfo, name: str) -> list[dict[str, Any]]:
        pods = self._request(
            conn,
            "GET",
            f"/api/v1/namespaces/{self._ns(conn)}/pods",
            params={"labelSelector": f"{NAME_LABEL}={name}"},
        )
        items: list[dict[str, Any]] = list(pods.get("items") or [])
        return items

    def runtime_logs(self, conn: ConnectionInfo, app: AppTarget, limit: int) -> tuple[list[RuntimeLogEntry], bool]:
        if not app.provider_service_id:
            return [], False
        entries: list[RuntimeLogEntry] = []
        since = str(int(dt.timedelta(hours=1).total_seconds()))
        for pod in self._pods(conn, app.name)[:5]:
            pod_name = quote(str((pod.get("metadata") or {}).get("name", "")), safe="")
            text = self._request(
                conn,
                "GET",
                f"/api/v1/namespaces/{self._ns(conn)}/pods/{pod_name}/log",
                params={"container": "app", "timestamps": "true", "tailLines": str(limit), "sinceSeconds": since},
                allow_404=True,
                text=True,
            )
            for line in str(text or "").splitlines():
                match = _LOG_LINE.match(line)
                if not match:
                    continue
                try:
                    stamp = clock.as_utc(dt.datetime.fromisoformat(match.group(1).replace("Z", "+00:00")[:32]))
                except ValueError:
                    stamp = clock.utcnow()
                entries.append(RuntimeLogEntry(timestamp=stamp, stream="stdout", message=log_line(match.group(2))))
        entries.sort(key=lambda e: e.timestamp)
        return entries[-limit:], len(entries) > limit

    def metrics(self, conn: ConnectionInfo, app: AppTarget, minutes: int, step_seconds: int) -> list[MetricSeries]:
        """The current value: Kubernetes keeps no history without a monitoring stack."""
        now = clock.utcnow()
        deployment = self._request(conn, "GET", self._deployment_path(conn, app.name), allow_404=True) or {}
        ready = float(((deployment.get("status") or {}).get("readyReplicas")) or 0)
        series = [MetricSeries(name="instances", unit="count", points=[MetricPoint(now, ready)])]
        template = ((deployment.get("spec") or {}).get("template") or {}).get("spec") or {}
        resources = ((template.get("containers") or [{}])[0]).get("resources") or {}
        cpu_request = _parse_cpu((resources.get("requests") or {}).get("cpu"))
        memory_limit = _parse_bytes((resources.get("limits") or {}).get("memory"))
        try:
            usage = self._request(
                conn,
                "GET",
                f"/apis/metrics.k8s.io/v1beta1/namespaces/{self._ns(conn)}/pods",
                params={"labelSelector": f"{NAME_LABEL}={app.name}"},
            )
        except ProviderError:
            return series
        pods = usage.get("items") or []
        if pods and cpu_request > 0 and memory_limit > 0:
            cpu = sum(_parse_cpu((c.get("usage") or {}).get("cpu")) for p in pods for c in p.get("containers") or [])
            memory = sum(
                _parse_bytes((c.get("usage") or {}).get("memory")) for p in pods for c in p.get("containers") or []
            )
            series.append(
                MetricSeries("cpu_percent", "percent", [MetricPoint(now, 100 * cpu / (cpu_request * len(pods)))])
            )
            series.append(
                MetricSeries("memory_percent", "percent", [MetricPoint(now, 100 * memory / (memory_limit * len(pods)))])
            )
        return series

    # ----- pause and resume -----

    def set_paused(self, conn: ConnectionInfo, app: AppTarget, paused: bool) -> None:
        # With zero replicas the HorizontalPodAutoscaler stops scaling, so the Deployment stays at zero.
        replicas = 0 if paused else max(app.min_instances, 1)
        self._request(
            conn, "PATCH", f"{self._deployment_path(conn, app.name)}/scale", body={"spec": {"replicas": replicas}}
        )

    def service_host_allowed(self, host: str) -> bool:
        """Load balancer addresses: public IPs, or cloud load balancer host names."""
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return host.endswith((".elb.amazonaws.com", ".cloudapp.azure.com"))
