"""The Azure Container Apps adapter against a recorded HTTP transport, and Azure connections through the API."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.providers.azure import AzureContainerAppsAdapter
from app.providers.base import AppTarget, ConnectionInfo, ProviderError, ResolvedImage, SourceInfo, SpecInfo
from tests.conftest import ApiClient, make_settings
from tests.contract import CONNECTION, check, field_errors

TENANT = "11111111-1111-1111-1111-111111111111"
SUB = "22222222-2222-2222-2222-222222222222"
CLIENT = "33333333-3333-3333-3333-333333333333"
CONN = ConnectionInfo(
    id="c1",
    provider="azure_container_apps",
    region="eastasia",
    tenant_id=TENANT,
    subscription_id=SUB,
    client_id=CLIENT,
    resource_group="cloudbridge-rg",
    environment_name="cloudbridge-env",
    registry_name="acmeregistry",
)
GROUP = f"/subscriptions/{SUB}/resourceGroups/cloudbridge-rg"
APP_ID = f"{GROUP}/providers/Microsoft.App/containerApps/orders-api"
IDENTITY = f"{GROUP}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cloudbridge-runtime"
APP = AppTarget(id="app-1", name="orders-api", region="eastasia", provider_service_id=None)
DIGEST = "sha256:" + "a" * 64
ENVIRONMENT = {
    "location": "East Asia",
    "properties": {
        "appLogsConfiguration": {
            "destination": "log-analytics",
            "logAnalyticsConfiguration": {"customerId": "44444444-4444-4444-4444-444444444444"},
        }
    },
}

Route = tuple[str, str, int, Any]


class Recorder:
    """Serves queued responses in order of matching (method, URL fragment); fails on anything unexpected."""

    def __init__(self, routes: list[Route]) -> None:
        self.routes = list(routes)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for i, (method, fragment, status, body) in enumerate(self.routes):
            if method == request.method and fragment in url:
                del self.routes[i]
                if isinstance(body, str):
                    return httpx.Response(status, text=body)
                return httpx.Response(status, json=body)
        raise AssertionError(f"unexpected request {request.method} {url}")

    def factory(self) -> Callable[[], httpx.Client]:
        return lambda: httpx.Client(transport=httpx.MockTransport(self.handler))


TOKEN: Route = (
    "POST",
    f"login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
    200,
    {"access_token": "arm-token", "expires_in": 3600},
)


def adapter(tmp_path: Path, recorder: Recorder) -> AzureContainerAppsAdapter:
    return AzureContainerAppsAdapter(
        make_settings(tmp_path), client_factory=recorder.factory(), token_source=lambda audience: "aws-signed-jwt"
    )


def spec(**overrides: Any) -> SpecInfo:
    data: dict[str, Any] = {
        "port": 8080,
        "cpu": "0.5",
        "memory": "1Gi",
        "min_instances": 0,
        "max_instances": 3,
        "concurrency": 50,
        "exposure": "public",
        "health_check_path": "/healthz",
        "environment": [{"name": "LOG_LEVEL", "value": "info"}],
        "secret_refs": [],
    }
    data.update(overrides)
    return SpecInfo.from_snapshot(data)


def test_token_exchange_uses_the_federated_assertion_and_is_cached(tmp_path: Path) -> None:
    env_route: Route = ("GET", "managedEnvironments/cloudbridge-env", 200, ENVIRONMENT)
    recorder = Recorder([TOKEN, env_route, env_route])
    a = adapter(tmp_path, recorder)
    a.check_access(CONN)
    a.check_access(CONN)
    token_requests = [r for r in recorder.requests if "oauth2" in str(r.url)]
    assert len(token_requests) == 1
    form = dict(httpx.QueryParams(token_requests[0].content.decode()))
    assert form["client_id"] == CLIENT
    assert form["client_assertion"] == "aws-signed-jwt"
    assert form["client_assertion_type"] == "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    assert form["scope"] == "https://management.azure.com/.default"
    assert recorder.requests[1].headers["Authorization"] == "Bearer arm-token"


def test_missing_federated_credential_is_reported_without_raw_output(tmp_path: Path) -> None:
    failure = {
        "error": "invalid_client",
        "error_description": "AADSTS70021: No matching federated identity record found",
    }
    recorder = Recorder([("POST", "oauth2/v2.0/token", 400, failure)])
    progress: list[Any] = []
    result = adapter(tmp_path, recorder).validate_connection(CONN, lambda stage, checks: progress.append(stage))
    assert result.status == "invalid_credentials"
    assert result.checks[0].status == "failed"
    assert "federated credential" in (result.checks[0].detail or "")
    assert "AADSTS" not in (result.checks[0].detail or "")
    assert [c.status for c in result.checks[1:]] == ["skipped"] * 4


def test_platform_without_identity_fails_clearly(tmp_path: Path) -> None:
    a = AzureContainerAppsAdapter(make_settings(tmp_path), client_factory=Recorder([]).factory())
    with pytest.raises(ProviderError) as raised:
        a.check_access(CONN)
    assert raised.value.code == "platform_not_configured"


def test_validation_passes_and_flags_missing_identity(tmp_path: Path) -> None:
    recorder = Recorder(
        [
            TOKEN,
            ("GET", f"{GROUP}?api-version", 200, {"name": "cloudbridge-rg"}),
            ("GET", "managedEnvironments/cloudbridge-env", 200, ENVIRONMENT),
            ("GET", "registries/acmeregistry", 200, {"name": "acmeregistry"}),
            ("GET", "userAssignedIdentities/cloudbridge-runtime", 404, {"error": {"code": "ResourceNotFound"}}),
        ]
    )
    result = adapter(tmp_path, recorder).validate_connection(CONN, lambda *_: None)
    assert result.status == "connected"
    assert [c.status for c in result.checks] == ["passed", "passed", "passed", "passed", "skipped"]
    assert "cloudbridge-runtime" in (result.checks[4].detail or "")


def test_environment_in_another_region_fails_validation(tmp_path: Path) -> None:
    recorder = Recorder(
        [
            TOKEN,
            ("GET", f"{GROUP}?api-version", 200, {}),
            ("GET", "managedEnvironments/cloudbridge-env", 200, {**ENVIRONMENT, "location": "West Europe"}),
        ]
    )
    result = adapter(tmp_path, recorder).validate_connection(CONN, lambda *_: None)
    assert result.status == "error"
    assert "westeurope" in (result.checks[2].detail or "")


def test_missing_role_is_insufficient_permissions(tmp_path: Path) -> None:
    denied = {"error": {"code": "AuthorizationFailed", "message": "The client does not have authorization"}}
    recorder = Recorder([TOKEN, ("GET", f"{GROUP}?api-version", 403, denied)])
    result = adapter(tmp_path, recorder).validate_connection(CONN, lambda *_: None)
    assert result.status == "insufficient_permissions"


def test_rollout_creates_the_app_with_identity_secrets_and_scaling(tmp_path: Path) -> None:
    image = ResolvedImage("acmeregistry.azurecr.io/cloudbridge/orders-api", DIGEST)
    recorder = Recorder(
        [
            TOKEN,
            ("GET", "managedEnvironments/cloudbridge-env", 200, ENVIRONMENT),
            ("GET", f"{APP_ID}?api-version", 404, {"error": {"code": "ResourceNotFound"}}),
            ("GET", "userAssignedIdentities/cloudbridge-runtime", 200, {"id": IDENTITY}),
            ("PUT", f"{APP_ID}?api-version", 201, {"properties": {"provisioningState": "InProgress"}}),
        ]
    )
    secret = "https://acme-vault.vault.azure.net/secrets/db-password"
    handle = adapter(tmp_path, recorder).start_rollout(
        CONN, APP, image, spec(secret_refs=[{"name": "DB_PASSWORD", "reference": secret}])
    )
    assert handle == {"service_id": APP_ID, "image": image.pinned, "port": 8080}
    body = json.loads(recorder.requests[-1].content)
    assert body["location"] == "East Asia"
    assert body["tags"]["cloudbridge-application"] == "app-1"
    assert body["identity"] == {"type": "UserAssigned", "userAssignedIdentities": {IDENTITY: {}}}
    config = body["properties"]["configuration"]
    assert config["ingress"]["external"] is True and config["ingress"]["targetPort"] == 8080
    assert config["registries"] == [{"server": "acmeregistry.azurecr.io", "identity": IDENTITY}]
    assert config["secrets"] == [{"name": "kv0-db-password", "keyVaultUrl": secret, "identity": IDENTITY}]
    container = body["properties"]["template"]["containers"][0]
    assert container["image"] == image.pinned
    assert container["resources"] == {"cpu": 0.5, "memory": "1Gi"}
    assert {"name": "DB_PASSWORD", "secretRef": "kv0-db-password"} in container["env"]
    assert container["probes"][0]["httpGet"] == {"path": "/healthz", "port": 8080}
    scale = body["properties"]["template"]["scale"]
    assert scale["minReplicas"] == 0 and scale["maxReplicas"] == 3
    assert scale["rules"][0]["http"]["metadata"]["concurrentRequests"] == "50"


def test_public_image_without_secrets_needs_no_identity(tmp_path: Path) -> None:
    image = ResolvedImage("mcr.microsoft.com/k8se/quickstart", DIGEST)
    recorder = Recorder(
        [
            TOKEN,
            ("GET", "managedEnvironments/cloudbridge-env", 200, ENVIRONMENT),
            ("GET", f"{APP_ID}?api-version", 404, {}),
            ("PUT", f"{APP_ID}?api-version", 200, {}),
        ]
    )
    adapter(tmp_path, recorder).start_rollout(CONN, APP, image, spec(exposure="private"))
    body = json.loads(recorder.requests[-1].content)
    assert "identity" not in body
    assert "registries" not in body["properties"]["configuration"]
    assert body["properties"]["configuration"]["ingress"]["external"] is False


def test_rollout_refuses_an_app_owned_by_someone_else(tmp_path: Path) -> None:
    recorder = Recorder(
        [
            TOKEN,
            ("GET", "managedEnvironments/cloudbridge-env", 200, ENVIRONMENT),
            ("GET", f"{APP_ID}?api-version", 200, {"tags": {"cloudbridge-application": "other"}}),
        ]
    )
    with pytest.raises(ProviderError) as raised:
        adapter(tmp_path, recorder).start_rollout(CONN, APP, ResolvedImage("mcr.microsoft.com/x", DIGEST), spec())
    assert raised.value.code == "service_name_taken"


def test_poll_rollout_waits_for_the_new_revision_then_reports_effective_config(tmp_path: Path) -> None:
    pinned = f"mcr.microsoft.com/k8se/quickstart@{DIGEST}"
    app_body = {
        "properties": {
            "provisioningState": "Succeeded",
            "latestRevisionName": "orders-api--r2",
            "configuration": {
                "ingress": {
                    "fqdn": "orders-api.example.eastasia.azurecontainerapps.io",
                    "external": True,
                    "targetPort": 8080,
                }
            },
            "template": {
                "containers": [
                    {
                        "image": pinned,
                        "resources": {"cpu": 0.5, "memory": "1Gi"},
                        "probes": [{"type": "Startup", "httpGet": {"path": "/healthz"}}],
                    }
                ],
                "scale": {
                    "minReplicas": 0,
                    "maxReplicas": 3,
                    "rules": [{"http": {"metadata": {"concurrentRequests": "50"}}}],
                },
            },
        }
    }
    old_revision = {"properties": {"template": {"containers": [{"image": "old"}]}, "provisioningState": "Provisioned"}}
    new_revision = {
        "properties": {
            "template": {"containers": [{"image": pinned}]},
            "provisioningState": "Provisioned",
            "runningState": "Running",
            "healthState": "Healthy",
        }
    }
    recorder = Recorder(
        [
            TOKEN,
            ("GET", f"{APP_ID}?api-version", 200, {"properties": {"provisioningState": "InProgress"}}),
            ("GET", f"{APP_ID}?api-version", 200, app_body),
            ("GET", "revisions/orders-api--r2", 200, old_revision),
            ("GET", f"{APP_ID}?api-version", 200, app_body),
            ("GET", "revisions/orders-api--r2", 200, new_revision),
        ]
    )
    a = adapter(tmp_path, recorder)
    handle = {"service_id": APP_ID, "image": pinned}
    assert a.poll_rollout(CONN, handle).state == "running"
    assert a.poll_rollout(CONN, handle).state == "running"
    done = a.poll_rollout(CONN, handle)
    assert done.state == "succeeded"
    assert done.service_url == "https://orders-api.example.eastasia.azurecontainerapps.io"
    assert done.effective is not None
    assert (done.effective.cpu, done.effective.memory, done.effective.concurrency) == ("0.5", "1Gi", 50)
    assert done.effective.health_check_path == "/healthz" and done.effective.exposure == "public"


def test_failed_revision_fails_the_rollout(tmp_path: Path) -> None:
    pinned = f"mcr.microsoft.com/x@{DIGEST}"
    recorder = Recorder(
        [
            TOKEN,
            (
                "GET",
                f"{APP_ID}?api-version",
                200,
                {"properties": {"provisioningState": "Succeeded", "latestRevisionName": "r1"}},
            ),
            (
                "GET",
                "revisions/r1",
                200,
                {
                    "properties": {
                        "template": {"containers": [{"image": pinned}]},
                        "provisioningState": "Failed",
                        "provisioningError": "Startup probe failed",
                    }
                },
            ),
        ]
    )
    status = adapter(tmp_path, recorder).poll_rollout(CONN, {"service_id": APP_ID, "image": pinned})
    assert status.state == "failed"
    assert "Startup probe failed" in (status.message or "")


def test_worker_without_ingress_finishes_without_a_url(tmp_path: Path) -> None:
    # Live Azure returns "ingress": null for an app with no ingress.
    pinned = f"mcr.microsoft.com/x@{DIGEST}"
    app_body = {
        "properties": {
            "provisioningState": "Succeeded",
            "latestRevisionName": "r1",
            "configuration": {"ingress": None},
            "template": {"containers": [{"image": pinned, "resources": {"cpu": 0.5, "memory": "1Gi"}}]},
        }
    }
    revision = {
        "properties": {
            "template": {"containers": [{"image": pinned}]},
            "provisioningState": "Provisioned",
            "runningState": "Running",
        }
    }
    recorder = Recorder(
        [TOKEN, ("GET", f"{APP_ID}?api-version", 200, app_body), ("GET", "revisions/r1", 200, revision)]
    )
    status = adapter(tmp_path, recorder).poll_rollout(CONN, {"service_id": APP_ID, "image": pinned, "port": 80})
    assert status.state == "succeeded" and status.service_url is None


def test_domain_records_are_returned_when_the_host_name_analysis_errors(tmp_path: Path) -> None:
    # Live Azure answers 500 from listCustomHostNameAnalysis for a name that does not resolve yet.
    environment = {**ENVIRONMENT, "properties": {"customDomainConfiguration": {"customDomainVerificationId": "ABC123"}}}
    app_body = {"properties": {"configuration": {"ingress": {"fqdn": "orders-api.example.azurecontainerapps.io"}}}}
    recorder = Recorder(
        [
            TOKEN,
            ("GET", "managedEnvironments/cloudbridge-env", 200, environment),
            ("GET", f"{APP_ID}?api-version", 200, app_body),
            ("GET", f"{APP_ID}?api-version", 200, app_body),
            ("POST", "listCustomHostNameAnalysis", 500, {"error": {"code": "InternalServerError", "message": "x"}}),
        ]
    )
    target = AppTarget(id="app-1", name="orders-api", region="eastasia", provider_service_id=APP_ID)
    state = adapter(tmp_path, recorder).add_domain(CONN, target, "www.example.org")
    assert state.status == "pending_dns"
    assert sorted((r.type, r.name) for r in state.records) == [
        ("CNAME", "www.example.org"),
        ("TXT", "asuid.www.example.org"),
    ]


def test_build_runs_on_acr_tasks_and_reports_the_digest(tmp_path: Path) -> None:
    recorder = Recorder(
        [
            TOKEN,
            ("POST", "registries/acmeregistry/scheduleRun", 200, {"properties": {"runId": "ca1", "status": "Queued"}}),
            ("GET", "runs/ca1?api-version", 200, {"properties": {"status": "Running"}}),
            (
                "GET",
                "runs/ca1?api-version",
                200,
                {
                    "properties": {
                        "status": "Succeeded",
                        "outputImages": [{"repository": "cloudbridge/orders-api", "tag": "t1", "digest": DIGEST}],
                    }
                },
            ),
            (
                "POST",
                "runs/ca1/listLogSasUrl",
                200,
                {"logLink": "https://store.blob.core.windows.net/logs/ca1.log?sig=x"},
            ),
            ("GET", "store.blob.core.windows.net", 200, "Step 1/3 : FROM node\r\nSuccessfully built\n"),
        ]
    )
    a = adapter(tmp_path, recorder)
    source = SourceInfo(
        "repository", None, "https://github.com/acme/orders.git", "commit", "abc1234", "app/Dockerfile", "app"
    )
    handle = a.start_build(CONN, APP, source, "t1", "token")
    body = json.loads(recorder.requests[-1].content)
    assert body["sourceLocation"] == "https://github.com/acme/orders.git#abc1234:app"
    assert body["dockerFilePath"] == "Dockerfile"
    assert body["imageNames"] == ["cloudbridge/orders-api:t1"]
    assert a.poll_build(CONN, handle).state == "running"
    done = a.poll_build(CONN, handle)
    assert done.state == "succeeded"
    assert done.image_uri == "acmeregistry.azurecr.io/cloudbridge/orders-api"
    assert done.image_digest == DIGEST and done.commit == "abc1234"
    assert [line for _, line in done.log_lines] == ["Step 1/3 : FROM node", "Successfully built"]


def test_build_log_link_on_an_unexpected_host_is_ignored(tmp_path: Path) -> None:
    recorder = Recorder(
        [
            TOKEN,
            (
                "GET",
                "runs/ca2?api-version",
                200,
                {"properties": {"status": "Failed", "runErrorMessage": "docker build failed"}},
            ),
            ("POST", "runs/ca2/listLogSasUrl", 200, {"logLink": "https://169.254.169.254/latest"}),
        ]
    )
    status = adapter(tmp_path, recorder).poll_build(CONN, {"run_id": "ca2", "image_uri": "x.azurecr.io/a/b:t"})
    assert status.state == "failed" and status.log_lines == []
    assert "docker build failed" in (status.message or "")


def test_acr_image_digest_uses_the_exchanged_registry_token(tmp_path: Path) -> None:
    challenge = 'Bearer realm="https://acmeregistry.azurecr.io/oauth2/token",service="acmeregistry.azurecr.io"'
    recorder = Recorder(
        [
            TOKEN,
            ("POST", "acmeregistry.azurecr.io/oauth2/exchange", 200, {"refresh_token": "acr-refresh"}),
            ("HEAD", "/v2/cloudbridge/orders-api/manifests/v1", 401, {}),
            ("GET", "acmeregistry.azurecr.io/oauth2/token", 200, {"access_token": "acr-access"}),
            ("HEAD", "/v2/cloudbridge/orders-api/manifests/v1", 200, {}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        response = recorder.handler(request)
        if request.method == "HEAD" and response.status_code == 401:
            return httpx.Response(401, headers={"www-authenticate": challenge})
        if request.method == "HEAD":
            return httpx.Response(200, headers={"docker-content-digest": DIGEST})
        return response

    a = AzureContainerAppsAdapter(
        make_settings(tmp_path),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        token_source=lambda audience: "jwt",
    )
    resolved = a.resolve_image(CONN, "acmeregistry.azurecr.io/cloudbridge/orders-api:v1")
    assert resolved.pinned == f"acmeregistry.azurecr.io/cloudbridge/orders-api@{DIGEST}"
    exchange = dict(httpx.QueryParams(recorder.requests[1].content.decode()))
    assert exchange["access_token"] == "arm-token" and exchange["service"] == "acmeregistry.azurecr.io"
    assert recorder.requests[3].headers["Authorization"].startswith("Basic ")


def test_other_registries_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProviderError) as raised:
        adapter(tmp_path, Recorder([])).resolve_image(CONN, "123456789012.dkr.ecr.us-east-1.amazonaws.com/a:1")
    assert raised.value.code == "unsupported_registry"


def test_service_state_delete_and_logs(tmp_path: Path) -> None:
    deployed = AppTarget(id="app-1", name="orders-api", region="eastasia", provider_service_id=APP_ID)
    rows = [["2026-10-16T10:00:01Z", "stderr", "boom"], ["2026-10-16T10:00:00Z", "stdout", "listening on 8080"]]
    recorder = Recorder(
        [
            TOKEN,
            (
                "GET",
                f"{APP_ID}?api-version",
                200,
                {"properties": {"provisioningState": "Succeeded", "runningStatus": "Running"}},
            ),
            ("DELETE", f"{APP_ID}?api-version", 202, {}),
            ("GET", f"{APP_ID}?api-version", 200, {"properties": {"provisioningState": "Deleting"}}),
            ("GET", f"{APP_ID}?api-version", 404, {}),
            ("GET", "managedEnvironments/cloudbridge-env", 200, ENVIRONMENT),
            ("POST", "oauth2/v2.0/token", 200, {"access_token": "logs-token", "expires_in": 3600}),
            (
                "POST",
                "api.loganalytics.io/v1/workspaces/44444444-4444-4444-4444-444444444444/query",
                200,
                {"tables": [{"rows": rows}]},
            ),
        ]
    )
    a = adapter(tmp_path, recorder)
    assert a.check_service(CONN, deployed).running is True
    handle = a.start_delete(CONN, deployed)
    assert a.poll_delete(CONN, handle).state == "running"
    assert a.poll_delete(CONN, handle).state == "deleted"
    entries, truncated = a.runtime_logs(CONN, deployed, 10)
    assert [(e.stream, e.message) for e in entries] == [("stdout", "listening on 8080"), ("stderr", "boom")]
    assert truncated is False
    query = json.loads(recorder.requests[-1].content)["query"]
    assert "ContainerAppName_s == 'orders-api'" in query
    assert recorder.requests[-1].headers["Authorization"] == "Bearer logs-token"


def test_health_checks_only_reach_container_apps_hosts(tmp_path: Path) -> None:
    a = adapter(tmp_path, Recorder([]))
    assert a.service_host_allowed("orders-api.happyhill-1.eastasia.azurecontainerapps.io")
    assert not a.service_host_allowed("example.com")


AZURE = {
    "name": "Production Azure",
    "provider": "azure_container_apps",
    "default_region": "eastasia",
    "azure": {
        "tenant_id": TENANT,
        "subscription_id": SUB,
        "client_id": CLIENT,
        "resource_group": "cloudbridge-rg",
        "environment_name": "cloudbridge-env",
        "registry_name": "AcmeRegistry",
    },
}


def test_create_azure_connection(owner: ApiClient) -> None:
    response = owner.post("/cloud-connections", json=AZURE)
    assert response.status_code == 201
    body = response.json()
    check(body, CONNECTION)
    assert body["config"]["subscription_id"] == SUB
    assert body["config"]["registry_name"] == "acmeregistry"
    assert body["setup"]["federation_audience"] == "api://AzureADTokenExchange"


def test_azure_connection_field_errors(owner: ApiClient) -> None:
    bad = {
        **AZURE,
        "azure": {
            "tenant_id": "x",
            "subscription_id": "y",
            "client_id": "z",
            "resource_group": "",
            "environment_name": "Bad_Env",
        },
    }
    response = owner.post("/cloud-connections", json=bad)
    assert response.status_code == 422
    fields = set(field_errors(response.json()))
    assert fields >= {"azure.tenant_id", "azure.subscription_id", "azure.client_id", "azure.environment_name"}


@pytest.mark.parametrize(
    ("ours", "azure"), [("512Mi", "0.5Gi"), ("1Gi", "1Gi"), ("1536Mi", "1.5Gi"), ("2560Mi", "2.5Gi"), ("4Gi", "4Gi")]
)
def test_memory_is_sent_in_decimal_gibibytes_and_read_back(ours: str, azure: str) -> None:
    from app.providers.azure import _memory_from_azure, _memory_to_azure

    assert _memory_to_azure(ours) == azure
    assert _memory_from_azure(azure) == ours


def test_unknown_client_id_is_explained_from_error_codes(tmp_path: Path) -> None:
    failure = {"error": "unauthorized_client", "error_description": "Application not found.", "error_codes": [700016]}
    recorder = Recorder([("POST", "oauth2/v2.0/token", 400, failure)])
    result = adapter(tmp_path, recorder).validate_connection(CONN, lambda *_: None)
    assert result.status == "invalid_credentials"
    assert "Check the client ID" in (result.checks[0].detail or "")


def test_client_without_federated_credentials_is_explained(tmp_path: Path) -> None:
    failure = {
        "error": "invalid_client",
        "error_description": "AADSTS70025: no federated credentials",
        "error_codes": [70025],
    }
    recorder = Recorder([("POST", "oauth2/v2.0/token", 401, failure)])
    result = adapter(tmp_path, recorder).validate_connection(CONN, lambda *_: None)
    assert "federated credential" in (result.checks[0].detail or "")


def test_express_environment_limit_is_explained(tmp_path: Path) -> None:
    express = {"error": {"code": "ExpressEnvironmentFeatureNotSupported", "message": "'KeyVaultUrl in secrets' ..."}}
    recorder = Recorder(
        [
            TOKEN,
            ("GET", "managedEnvironments/cloudbridge-env", 200, ENVIRONMENT),
            ("GET", f"{APP_ID}?api-version", 404, {}),
            ("GET", "userAssignedIdentities/cloudbridge-runtime", 200, {"id": IDENTITY}),
            ("PUT", f"{APP_ID}?api-version", 400, express),
        ]
    )
    secret = [{"name": "DB", "reference": "https://acme-vault.vault.azure.net/secrets/db"}]
    with pytest.raises(ProviderError) as raised:
        adapter(tmp_path, recorder).start_rollout(
            CONN, APP, ResolvedImage("mcr.microsoft.com/x", DIGEST), spec(secret_refs=secret)
        )
    assert "express environment" in raised.value.message
