"""The Cloud Run adapter against a recorded HTTP session, the OCI digest resolver, and the HTTP checker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.providers.base import AppTarget, ConnectionInfo, ProviderError, ResolvedImage, SpecInfo
from app.providers.gcp import GcpCloudRunAdapter
from app.providers.images import OciResolver, parse_image
from app.redaction import redact, safe_message
from app.worker import http_check
from tests.conftest import make_settings

CONN = ConnectionInfo(
    id="c1",
    provider="gcp_cloud_run",
    region="europe-west1",
    project_id="acme-prod-1",
    workload_identity_provider="projects/1/locations/global/workloadIdentityPools/pool/providers/prov",
    service_account_email="deployer@acme-prod-1.iam.gserviceaccount.com",
)
SERVICE = "projects/acme-prod-1/locations/europe-west1/services/orders-api"


class FakeResponse:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status_code = status
        self._body = body if body is not None else {}
        self.content = json.dumps(self._body).encode()

    def json(self) -> Any:
        return self._body


class FakeCredentials:
    valid = True
    token = "ya29.test-token-value-000000000000"


class RecordedSession:
    def __init__(self, routes: list[tuple[str, str, FakeResponse]]) -> None:
        self.routes = list(routes)
        self.requests: list[tuple[str, str, Any, Any]] = []
        self.credentials = FakeCredentials()

    def request(self, method: str, url: str, json: Any = None, params: Any = None, timeout: Any = None) -> FakeResponse:
        self.requests.append((method, url, json, params))
        for i, (m, fragment, response) in enumerate(self.routes):
            if m == method and url.endswith(fragment):
                del self.routes[i]
                return response
        raise AssertionError(f"unexpected request {method} {url}")


def adapter(tmp_path: Path, session: RecordedSession) -> GcpCloudRunAdapter:
    settings = make_settings(tmp_path, gcp_subject_token_source="aws")
    return GcpCloudRunAdapter(settings, session_factory=lambda conn: session)


def test_validate_connection(tmp_path: Path) -> None:
    session = RecordedSession(
        [
            ("GET", "/locations/europe-west1/services", FakeResponse(200, {})),
            ("GET", "/locations/europe-west1/repositories", FakeResponse(200, {})),
            ("GET", "/locations/europe-west1/builds", FakeResponse(403, {"error": {"message": "Permission denied"}})),
        ]
    )
    result = adapter(tmp_path, session).validate_connection(CONN, lambda *_: None)
    assert result.status == "connected"
    assert [c.status for c in result.checks] == ["passed", "passed", "passed", "skipped"]


def test_disabled_api_is_reported(tmp_path: Path) -> None:
    body = {"error": {"message": "Cloud Run Admin API has not been used in project 1 before or it is disabled.",
                      "details": [{"reason": "SERVICE_DISABLED"}]}}  # fmt: skip
    session = RecordedSession([("GET", "/locations/europe-west1/services", FakeResponse(403, body))])
    result = adapter(tmp_path, session).validate_connection(CONN, lambda *_: None)
    assert result.status == "insufficient_permissions" and result.error_code == "api_disabled"
    assert "Cloud Run Admin API" in (result.checks[1].detail or "")


def test_platform_identity_missing(tmp_path: Path) -> None:
    result = GcpCloudRunAdapter(make_settings(tmp_path)).validate_connection(CONN, lambda *_: None)
    assert result.status == "error" and result.error_code == "platform_not_configured"


def test_create_service_rollout_and_public_access(tmp_path: Path) -> None:
    session = RecordedSession(
        [
            ("GET", SERVICE, FakeResponse(404, {"error": {"message": "not found"}})),
            (
                "POST",
                "/locations/europe-west1/services",
                FakeResponse(200, {"name": "projects/p/locations/l/operations/op1"}),
            ),
            ("GET", "/operations/op1", FakeResponse(200, {"done": True, "response": {}})),
            ("GET", f"{SERVICE}:getIamPolicy", FakeResponse(200, {"etag": "BwX"})),
            ("POST", f"{SERVICE}:setIamPolicy", FakeResponse(200, {})),
            (
                "GET",
                SERVICE,
                FakeResponse(
                    200,
                    {
                        "uri": "https://orders-api-abc-ew.a.run.app",
                        "ingress": "INGRESS_TRAFFIC_ALL",
                        "template": {
                            "scaling": {"maxInstanceCount": 3},
                            "maxInstanceRequestConcurrency": 80,
                            "containers": [
                                {
                                    "resources": {"limits": {"cpu": "1", "memory": "512Mi"}},
                                    "ports": [{"containerPort": 8080}],
                                    "startupProbe": {"httpGet": {"path": "/healthz"}},
                                }
                            ],
                        },
                    },
                ),
            ),
        ]
    )
    gcp = adapter(tmp_path, session)
    spec = SpecInfo(
        port=8080, cpu="1", memory="512Mi", min_instances=0, max_instances=3, concurrency=None, exposure="public",
        health_check_path="/healthz", environment=[("LOG_LEVEL", "info")],
        secret_refs=[("DB", "projects/acme-prod-1/secrets/db/versions/3")],
    )  # fmt: skip
    target = AppTarget(id="app-1", name="orders-api", region="europe-west1", provider_service_id=None)
    image = ResolvedImage("europe-west1-docker.pkg.dev/acme-prod-1/apps/orders", "sha256:" + "a" * 64)
    handle = gcp.start_rollout(CONN, target, image, spec)
    method, _url, body, params = session.requests[1]
    assert (method, params) == ("POST", {"serviceId": "orders-api"})
    container = body["template"]["containers"][0]
    assert container["image"] == f"{image.repository}@{image.digest}"
    assert container["env"][1] == {
        "name": "DB",
        "valueSource": {"secretKeyRef": {"secret": "projects/acme-prod-1/secrets/db", "version": "3"}},
    }
    assert body["ingress"] == "INGRESS_TRAFFIC_ALL" and "maxInstanceRequestConcurrency" not in body["template"]

    status = gcp.poll_rollout(CONN, handle)
    assert status.state == "succeeded" and status.service_url == "https://orders-api-abc-ew.a.run.app"
    assert status.effective is not None and status.effective.min_instances == 0
    assert status.effective.health_check_path == "/healthz"
    policy = session.requests[4][2]["policy"]
    assert policy == {"bindings": [{"role": "roles/run.invoker", "members": ["allUsers"]}], "etag": "BwX"}


def test_existing_service_owned_by_someone_else(tmp_path: Path) -> None:
    session = RecordedSession([("GET", SERVICE, FakeResponse(200, {"labels": {"team": "other"}}))])
    target = AppTarget(id="app-1", name="orders-api", region="europe-west1", provider_service_id=None)
    spec = SpecInfo(8080, "1", "512Mi", 0, 1, None, "public", None, [], [])
    with pytest.raises(ProviderError) as error:
        adapter(tmp_path, session).start_rollout(CONN, target, ResolvedImage("r/x", "sha256:" + "a" * 64), spec)
    assert error.value.code == "service_name_taken"


def test_failed_operation_message_is_safe(tmp_path: Path) -> None:
    session = RecordedSession(
        [
            (
                "GET",
                "/operations/op1",
                FakeResponse(200, {"done": True, "error": {"message": "Container failed to start on 10.0.0.4"}}),
            )
        ]
    )
    status = adapter(tmp_path, session).poll_rollout(
        CONN, {"service_id": SERVICE, "operation": "projects/p/locations/l/operations/op1"}
    )
    assert status.state == "failed" and "10.0.0.4" not in (status.message or "")


def test_parse_image() -> None:
    ref = parse_image("registry.example.com:5000/team/app:1.2@sha256:" + "a" * 64)
    assert (ref.host, ref.path, ref.tag, ref.digest) == (
        "registry.example.com:5000",
        "team/app",
        "1.2",
        "sha256:" + "a" * 64,
    )
    assert parse_image("docker.io/library/nginx").reference == "latest"


def test_oci_resolver_with_token_challenge() -> None:
    digest = "sha256:" + "e" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.docker.io":
            return httpx.Response(200, json={"token": "t"})
        if request.headers.get("authorization") == "Bearer t":
            return httpx.Response(200, headers={"Docker-Content-Digest": digest})
        return httpx.Response(
            401,
            headers={"WWW-Authenticate": 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"'},
        )

    resolver = OciResolver(
        lambda host: host == "auth.docker.io", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    resolved = resolver.resolve(parse_image("docker.io/library/nginx:1.27"), "registry-1.docker.io", "library/nginx")
    assert resolved == ResolvedImage("docker.io/library/nginx", digest)


def test_oci_resolver_refuses_unexpected_token_realm() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer realm="https://169.254.169.254/latest"'})

    resolver = OciResolver(
        lambda host: host == "auth.docker.io", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ProviderError) as error:
        resolver.resolve(parse_image("docker.io/library/nginx:1"), "registry-1.docker.io", "library/nginx")
    assert error.value.code == "image_access_denied"


def test_http_check_only_contacts_allowed_hosts() -> None:
    def factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(204)))

    allowed = lambda host: host.endswith(".run.app")  # noqa: E731
    assert http_check.check("https://x.run.app", "/healthz", allowed, factory).outcome == "passed"
    assert http_check.check("https://169.254.169.254", "/", allowed, factory).outcome == "failed"
    assert http_check.check("http://x.run.app", "/", allowed, factory).outcome == "failed"


def test_redaction() -> None:
    text = "AKIAABCDEFGHIJKLMNOP password=hunter2 https://user:pass@host/x Bearer abc.def"
    cleaned = redact(text)
    assert "AKIAABCDEFGHIJKLMNOP" not in cleaned and "hunter2" not in cleaned and "pass@" not in cleaned
    assert safe_message("failed at db.internal", "fallback") == "fallback"
