"""Provider responses that carry `null` where an object or list is expected must not crash the adapters."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpcore
import httpx
import pytest
from botocore.exceptions import ClientError

from app.providers.aws import AwsAppRunnerAdapter, _error_code, cloudwatch_series, translate
from app.providers.azure import AzureContainerAppsAdapter
from app.providers.base import ConnectionInfo, ProviderError
from app.providers.ecs import AwsEcsExpressAdapter
from app.providers.entra import _seconds
from app.providers.gcp import GcpCloudRunAdapter
from app.providers.kubernetes import KubernetesAdapter
from tests.conftest import ApiClient, make_settings


class _Calls:
    """A client whose every method returns the given response."""

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def __getattr__(self, name: str) -> Any:
        return lambda **_: self._response


def test_client_error_with_null_error_object() -> None:
    error = ClientError({"Error": {"Code": "X", "Message": "m"}}, "DescribeService")
    error.response["Error"] = None  # type: ignore[typeddict-item]
    assert _error_code(error) == ""
    assert translate(error).code == "invalid_request"


def test_app_runner_effective_config_with_null_sections(tmp_path: Path) -> None:
    adapter = AwsAppRunnerAdapter(make_settings(tmp_path))
    service = {
        "InstanceConfiguration": None,
        "SourceConfiguration": {"ImageRepository": None},
        "HealthCheckConfiguration": None,
        "NetworkConfiguration": {"IngressConfiguration": None},
        "AutoScalingConfigurationSummary": None,
    }
    effective = adapter._effective(_Calls({}), service)  # type: ignore[arg-type]
    assert effective.port == 8080
    assert effective.exposure == "public"
    assert effective.health_check_path is None
    assert effective.min_instances == 1


def test_cloudwatch_series_with_null_points() -> None:
    client = _Calls({"MetricDataResults": [{"Id": "m0", "Timestamps": None, "Values": None}]})
    series = cloudwatch_series(client, "AWS/AppRunner", [], (("Requests", "Sum", "requests", "count"),), 60, 60)
    assert [s.points for s in series] == [[]]


def test_ecs_null_lists(tmp_path: Path) -> None:
    adapter = AwsEcsExpressAdapter(make_settings(tmp_path))
    conn = ConnectionInfo(id="c", provider="aws_ecs_express", region="eu-west-1")
    assert adapter._cluster_exists(_Calls({"clusters": None}), conn) is False
    assert adapter._deployment_status(_Calls({"serviceDeployments": None}), "arn:x") == (None, None)


# ----- HTTP adapters: a JSON body that is not an object, or not JSON at all -----

K8S = ConnectionInfo(
    id="k1",
    provider="kubernetes",
    region="in-cluster",
    api_server="https://cluster.example.com",
    namespace="apps",
    kubernetes_auth="oidc",
    token_audience="cloudbridge",
    tenant_id="11111111-1111-1111-1111-111111111111",
    client_id="33333333-3333-3333-3333-333333333333",
    subscription_id="22222222-2222-2222-2222-222222222222",
    resource_group="rg",
)


def _http(body: bytes, status: int = 200) -> Callable[..., httpx.Client]:
    def handler(request: httpx.Request) -> httpx.Response:
        if "login.microsoftonline.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(status, content=body, headers={"Content-Type": "application/json"})

    return lambda *_: httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("body", [b"[]", b'"text"', b"<html>proxy</html>"])
def test_registry_token_exchange_with_unusable_json(tmp_path: Path, body: bytes) -> None:
    settings = make_settings(tmp_path)
    azure = AzureContainerAppsAdapter(settings, client_factory=_http(body), token_source=lambda _: "jwt")
    k8s = KubernetesAdapter(settings, client_factory=_http(body), token_source=lambda _: "jwt")
    with pytest.raises(ProviderError) as from_azure:
        azure._acr_refresh_token(K8S, "r.azurecr.io")
    with pytest.raises(ProviderError) as from_aks:
        k8s._acr_refresh_token(replace(K8S, kubernetes_auth="aks"), "r.azurecr.io")
    assert from_azure.value.code == from_aks.value.code == "provider_unavailable"


def test_kubernetes_unreadable_success_body_is_a_provider_error(tmp_path: Path) -> None:
    k8s = KubernetesAdapter(
        make_settings(tmp_path), client_factory=_http(b"<html>proxy</html>"), token_source=lambda _: "jwt"
    )
    with pytest.raises(ProviderError) as raised:
        k8s._request(K8S, "GET", "/api/v1/namespaces/apps")
    assert raised.value.code == "provider_unavailable" and raised.value.retryable


class _Response:
    def __init__(self, status: int, body: Any, content: bytes = b"x") -> None:
        self.status_code = status
        self._body = body
        self.content = content

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response

    def request(self, *_: Any, **__: Any) -> _Response:
        return self.response


def test_gcp_error_body_that_is_not_an_object(tmp_path: Path) -> None:
    gcp = GcpCloudRunAdapter(
        make_settings(tmp_path, gcp_subject_token_source="aws"),
        session_factory=lambda conn: _Session(_Response(400, ["not", "an", "object"])),
    )
    assert gcp._error(_Response(400, ["x"])).code == "invalid_request"


def test_gcp_unreadable_success_body_is_a_provider_error(tmp_path: Path) -> None:
    gcp = GcpCloudRunAdapter(
        make_settings(tmp_path, gcp_subject_token_source="aws"),
        session_factory=lambda conn: _Session(_Response(200, ValueError("not json"))),
    )
    conn = ConnectionInfo(id="g", provider="gcp_cloud_run", region="europe-west1", project_id="p")
    with pytest.raises(ProviderError) as raised:
        gcp._call(conn, "GET", "https://run.googleapis.com/v2/x")
    assert raised.value.code == "provider_unavailable"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(3600, 3600), ("1800", 1800), (1800.0, 1800), (None, 3600), ("soon", 3600), (-5, 3600), ("inf", 3600)],
)
def test_entra_token_lifetime_parsing(value: object, expected: int) -> None:
    assert _seconds(value) == expected


# ----- stored data that is damaged -----


def test_password_check_on_a_damaged_hash_is_a_mismatch() -> None:
    from app.security import hash_password, verify_password

    good = hash_password("correct horse battery")
    assert verify_password("correct horse battery", good)
    damaged = good.rsplit("$", 1)[0] + "$not*base64!"
    assert verify_password("correct horse battery", damaged) is False
    assert verify_password("x", "garbage") is False


# ----- addresses that urllib cannot parse -----

MALFORMED_URLS = ["https://[::1", "https://[::1/x", "http://[", "https://example.com:99999/x"]


@pytest.mark.parametrize("url", MALFORMED_URLS)
def test_malformed_alert_address_is_a_field_error(owner: ApiClient, url: str) -> None:
    response = owner.post(
        "/alert-channels", json={"name": "ops", "kind": "webhook", "url": url, "events": ["deployment_failed"]}
    )
    assert response.status_code == 422, response.text
    assert [f["field"] for f in response.json()["error"]["fields"]] == ["url"]


@pytest.mark.parametrize("url", MALFORMED_URLS)
def test_malformed_addresses_are_rejected_not_raised(url: str) -> None:
    from app.services.alerts import display_host, url_problem
    from app.validation import kubernetes_api_server, repository_host_is_public
    from app.worker.http_check import check

    assert url_problem("webhook", url, resolver=lambda host: ["8.8.8.8"])
    assert display_host(url) == ""
    assert kubernetes_api_server(url, resolver=lambda host: ["8.8.8.8"])
    assert repository_host_is_public(url, resolver=lambda host: ["8.8.8.8"])
    assert check(url, "/", lambda host: True).outcome == "failed"


# ----- alert delivery connects only to the public address it checked (no DNS rebinding) -----


class _RecordingStream:
    def __init__(self, calls: list[tuple[str, str]]) -> None:
        self.calls = calls

    def start_tls(self, ssl_context: Any, server_hostname: str | None = None, timeout: float | None = None) -> Any:
        self.calls.append(("tls", str(server_hostname)))
        raise httpcore.ConnectError("stop here")

    def close(self) -> None:
        pass


class _RecordingBackend(httpcore.NetworkBackend):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def connect_tcp(
        self, host: str, port: int, timeout: Any = None, local_address: Any = None, socket_options: Any = None
    ) -> Any:
        self.calls.append(("tcp", f"{host}:{port}"))
        return _RecordingStream(self.calls)


def _pinned(addresses: list[str]) -> tuple[httpx.Client, _RecordingBackend]:
    from app.services.alerts import PublicOnlyTransport

    transport = PublicOnlyTransport(resolver=lambda host: addresses)
    recorder = _RecordingBackend()
    transport._pool._network_backend._inner = recorder  # type: ignore[attr-defined]
    return httpx.Client(transport=transport), recorder


def test_alert_transport_connects_to_the_checked_ip_and_verifies_the_host_name() -> None:
    client, recorder = _pinned(["93.184.216.34"])
    with pytest.raises(httpx.ConnectError):
        client.post("https://hooks.example.com/x", content=b"{}")
    assert recorder.calls == [("tcp", "93.184.216.34:443"), ("tls", "hooks.example.com")]


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["10.0.0.5"], ["93.184.216.34", "169.254.169.254"], ["::1"], []])
def test_alert_transport_refuses_private_addresses(addresses: list[str]) -> None:
    client, recorder = _pinned(addresses)
    with pytest.raises(httpx.ConnectError):
        client.post("https://hooks.example.com/x", content=b"{}")
    assert recorder.calls == []


def test_rebinding_host_gets_no_request() -> None:
    """The check sees a public address, the delivery a private one: no connection is made."""
    from app.services.alerts import Alert, deliver, url_problem

    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    url = "https://rebind.example.com/hook"
    assert url_problem("webhook", url, resolver=lambda host: next(answers)) is None
    client, recorder = _pinned([])
    client._transport._pool._network_backend._resolver = lambda host: next(answers)  # type: ignore[attr-defined]
    alert = Alert(event="test", subject="s", message="m", occurred_at=dt.datetime.now(dt.UTC))
    assert deliver("webhook", url, None, alert, lambda: client) == "Could not connect"
    assert recorder.calls == []
