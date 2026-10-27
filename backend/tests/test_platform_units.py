"""Units: schedules, cost estimates, cloudbridge.yaml, alert addresses, and the ECS and Kubernetes adapters."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from botocore.stub import ANY, Stubber

from app.providers.base import AppTarget, ConnectionInfo, ProviderError, ResolvedImage, SpecInfo
from app.providers.ecs import AwsEcsExpressAdapter
from app.providers.kubernetes import KubernetesAdapter
from app.services import alerts, config_file, costs, schedules
from tests.conftest import make_settings

UTC = dt.UTC
DIGEST = "sha256:" + "c" * 64


# ---------- schedules ----------


def at(day: int, hour: int, minute: int = 0) -> dt.datetime:
    # 2026-10-12 is a Monday.
    return dt.datetime(2026, 10, 12 + day, hour, minute, tzinfo=UTC)


def test_overnight_and_same_day_windows() -> None:
    weekdays_night, errors = schedules.parse("UTC", [0, 1, 2, 3, 4], "20:00", "08:00")
    assert errors == [] and weekdays_night is not None
    assert weekdays_night.asleep_at(at(0, 21))  # Monday night
    assert weekdays_night.asleep_at(at(1, 7, 59))  # early Tuesday, window started Monday
    assert not weekdays_night.asleep_at(at(1, 8))
    assert weekdays_night.asleep_at(at(5, 7))  # early Saturday belongs to Friday's window
    assert not weekdays_night.asleep_at(at(5, 21))  # Saturday night is not scheduled
    assert not weekdays_night.asleep_at(at(0, 7))  # early Monday: Sunday had no window

    lunch, _ = schedules.parse("Asia/Kolkata", [6], "12:00", "13:00")
    assert lunch is not None
    assert lunch.asleep_at(dt.datetime(2026, 10, 18, 6, 45, tzinfo=UTC))  # 12:15 IST on Sunday
    assert not lunch.asleep_at(dt.datetime(2026, 10, 18, 8, 0, tzinfo=UTC))
    assert schedules.Schedule.from_json(lunch.to_json()) == lunch
    assert schedules.parse("UTC", [1], "09:00", "09:00")[1][0].field == "wake_at"
    assert schedules.Schedule.from_json({"timezone": "UTC"}) is None


# ---------- cost ----------


def test_cost_estimates() -> None:
    app_runner = costs.estimate("aws_app_runner", "1", "2Gi", 0, 2)
    # Provisioned instances pay memory only: 1 instance x 2 GB x $0.007 x 730 h.
    assert app_runner.monthly_low == pytest.approx(10.22)
    assert app_runner.monthly_high == pytest.approx(2 * (0.064 + 2 * 0.007) * 730, abs=0.01)
    aca = costs.estimate("azure_container_apps", "0.25", "512Mi", 0, 1)
    assert aca.monthly_low == 0.0  # scaled to zero
    assert aca.lines[1].item == "Monthly free grant" and aca.lines[1].high < 0
    ecs = costs.estimate("aws_ecs_express", "0.25", "512Mi", 1, 1)
    assert ecs.monthly_low == ecs.monthly_high and ecs.lines[1].item == "Load balancer"
    k8s = costs.estimate("kubernetes", "0.5", "1Gi", 2, 4)
    assert k8s.monthly_low is None and "1–2 vCPU" in k8s.assumptions[1]


# ---------- cloudbridge.yaml ----------

CURRENT: dict[str, Any] = {
    "port": 8080,
    "cpu": "1",
    "memory": "2Gi",
    "min_instances": 1,
    "max_instances": 3,
    "concurrency": None,
    "exposure": "public",
    "health_check_path": "/healthz",
    "environment": [{"name": "A", "value": "1"}],
    "secret_refs": [],
}


def test_config_file_merges_over_current_settings() -> None:
    merged = config_file.parse("cpu: 0.5\nmemory: 1Gi\nsecrets:\n  DB: k8s:db/password\n", CURRENT)
    assert merged["cpu"] == "0.5" and merged["memory"] == "1Gi" and merged["port"] == 8080
    assert merged["environment"] == CURRENT["environment"]
    assert merged["secret_refs"] == [{"name": "DB", "reference": "k8s:db/password"}]
    assert config_file.parse("", CURRENT)["port"] == 8080
    assert config_file.parse("environment: null\nconcurrency: 40", CURRENT)["environment"] == []
    with pytest.raises(config_file.ConfigFileError) as raised:
        config_file.parse("port: true\nexposure: everyone\ninstances: 3\n", CURRENT)
    assert {e.field for e in raised.value.errors} == {"port", "exposure", "instances"}
    with pytest.raises(config_file.ConfigFileError):
        config_file.parse("- just\n- a list\n", CURRENT)
    with pytest.raises(config_file.ConfigFileError):
        config_file.parse("x" * 70000, CURRENT)


def test_config_file_is_read_only_from_github() -> None:
    seen: list[str] = []

    def factory() -> httpx.Client:
        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text="port: 1")

        return httpx.Client(transport=httpx.MockTransport(handle))

    assert config_file.fetch_github("https://github.com/acme/orders.git", "feature/x", factory) == "port: 1"
    assert seen == ["https://raw.githubusercontent.com/acme/orders/feature/x/cloudbridge.yaml"]
    for url in ("https://gitlab.com/acme/orders", "http://github.com/acme/orders", "https://github.com/acme"):
        with pytest.raises(config_file.ConfigFetchError):
            config_file.fetch_github(url, "main", factory)


# ---------- alert addresses ----------


def test_alert_addresses() -> None:
    public: Callable[[str], list[str]] = lambda host: ["93.184.216.34"]  # noqa: E731
    private: Callable[[str], list[str]] = lambda host: ["10.0.0.5"]  # noqa: E731
    assert alerts.url_problem("slack", "https://hooks.slack.com/services/T/B/X", resolver=public) is None
    assert alerts.url_problem("slack", "https://example.com/hook", resolver=public) is not None
    assert alerts.url_problem("teams", "https://acme.webhook.office.com/webhookb2/x", resolver=public) is None
    assert alerts.url_problem("webhook", "https://hooks.example.com/cb", resolver=public) is None
    assert alerts.url_problem("webhook", "https://internal.example.com/cb", resolver=private) is not None
    assert alerts.url_problem("webhook", "http://hooks.example.com/cb", resolver=public) is not None
    assert alerts.url_problem("webhook", "https://user:pw@hooks.example.com/cb", resolver=public) is not None
    assert alerts.url_problem("webhook", "http://127.0.0.1:9/x") is not None
    assert alerts.url_problem("webhook", "http://127.0.0.1:9/x", allow_private=True) is None
    message = alerts.Alert(event="deployment_failed", subject="S", message="M", link="https://cb.example/app")
    assert alerts.payload("slack", message) == {"text": "*S*\nM\nhttps://cb.example/app"}
    assert alerts.payload("teams", message)["text"].startswith("**S**")


# ---------- Kubernetes ----------

K8S = ConnectionInfo(
    id="k1",
    provider="kubernetes",
    region="in-cluster",
    api_server="https://20.40.60.80:443",
    ca_certificate="-----BEGIN CERTIFICATE-----\nAA==\n-----END CERTIFICATE-----",
    namespace="apps",
    kubernetes_auth="oidc",
    token_audience="kubernetes",
)
APP = AppTarget(id="app-1", name="web", region="in-cluster", provider_service_id=None, min_instances=2)


class Cluster:
    """A tiny in-memory API server: answers by (method, path) and records request bodies."""

    def __init__(self, responses: dict[tuple[str, str], list[tuple[int, Any]]]) -> None:
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[tuple[str, str, Any, dict[str, str]]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body, dict(request.url.params)))
        queue = self.responses.get((request.method, request.url.path))
        if not queue:
            return httpx.Response(404, json={"message": "not found"})
        status, payload = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(payload, str):
            return httpx.Response(status, text=payload)
        return httpx.Response(status, json=payload)

    def adapter(self, tmp_path: Path) -> KubernetesAdapter:
        return KubernetesAdapter(
            make_settings(tmp_path),
            client_factory=lambda conn: httpx.Client(transport=httpx.MockTransport(self.handle)),
            token_source=lambda audience: f"signed-for-{audience}",
        )


SPEC = SpecInfo.from_snapshot(
    {
        "port": 8080,
        "cpu": "0.5",
        "memory": "1Gi",
        "min_instances": 2,
        "max_instances": 5,
        "concurrency": None,
        "exposure": "public",
        "health_check_path": "/healthz",
        "environment": [{"name": "MODE", "value": "prod"}],
        "secret_refs": [{"name": "DB", "reference": "k8s:db-creds/password"}],
    }
)
DEP = "/apis/apps/v1/namespaces/apps/deployments/web"
SVC = "/api/v1/namespaces/apps/services/web"
HPA = "/apis/autoscaling/v2/namespaces/apps/horizontalpodautoscalers/web"
PODS = "/api/v1/namespaces/apps/pods"


def test_kubernetes_validation_checks_rbac_and_skips_missing_metrics(tmp_path: Path) -> None:
    cluster = Cluster(
        {
            ("GET", "/version"): [(200, {"gitVersion": "v1.33.2"})],
            ("GET", "/apis/apps/v1/namespaces/apps/deployments"): [(200, {"items": []})],
            ("POST", "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews"): [(201, {"status": {"allowed": True}})],
        }
    )
    result = cluster.adapter(tmp_path).validate_connection(K8S, lambda *_: None)
    assert result.status == "connected"
    assert [c.status for c in result.checks] == ["passed", "passed", "passed", "passed", "skipped"]
    assert result.checks[1].detail == "Kubernetes v1.33.2"

    denied = Cluster(
        {
            ("GET", "/version"): [(200, {})],
            ("GET", "/apis/apps/v1/namespaces/apps/deployments"): [(200, {"items": []})],
            ("POST", "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews"): [(201, {"status": {"allowed": False}})],
        }
    )
    result = denied.adapter(tmp_path).validate_connection(K8S, lambda *_: None)
    assert result.status == "insufficient_permissions"
    assert "create deployments" in (result.checks[3].detail or "")


def test_kubernetes_rollout_applies_deployment_service_and_autoscaler(tmp_path: Path) -> None:
    pinned = f"docker.io/library/nginx@{DIGEST}"
    ready = {
        "metadata": {"generation": 2, "annotations": {"cloudbridge.io/application-id": "app-1"}},
        "spec": {
            "replicas": 2,
            "template": {
                "spec": {
                    "containers": [
                        {
                            "image": pinned,
                            "ports": [{"containerPort": 8080}],
                            "resources": {"requests": {"cpu": "500m", "memory": "1Gi"}},
                            "readinessProbe": {"httpGet": {"path": "/healthz"}},
                        }
                    ]
                }
            },
        },
        "status": {"observedGeneration": 2, "replicas": 2, "updatedReplicas": 2, "availableReplicas": 2},
    }
    cluster = Cluster(
        {
            ("GET", DEP): [(404, {}), (200, ready)],
            ("PATCH", DEP): [(200, {"metadata": {"generation": 2}})],
            ("PATCH", SVC): [(200, {})],
            ("PATCH", HPA): [(200, {})],
            ("GET", PODS): [(200, {"items": []})],
            ("GET", SVC): [
                (200, {"status": {"loadBalancer": {}}}),
                (
                    200,
                    {"spec": {"type": "LoadBalancer"}, "status": {"loadBalancer": {"ingress": [{"ip": "20.1.2.3"}]}}},
                ),
            ],
            ("GET", HPA): [(200, {"spec": {"minReplicas": 2, "maxReplicas": 5}})],
        }
    )
    adapter = cluster.adapter(tmp_path)
    handle = adapter.start_rollout(K8S, APP, ResolvedImage("docker.io/library/nginx", DIGEST), SPEC)
    deployment = next(body for method, path, body, _ in cluster.calls if method == "PATCH" and path == DEP)
    params = next(params for method, path, _, params in cluster.calls if method == "PATCH" and path == DEP)
    assert params == {"fieldManager": "cloudbridge", "force": "true"}
    assert "replicas" not in deployment["spec"]  # the autoscaler owns it
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == pinned and container["resources"]["requests"] == {"cpu": "500m", "memory": "1Gi"}
    assert {"name": "DB", "valueFrom": {"secretKeyRef": {"name": "db-creds", "key": "password"}}} in container["env"]
    service = next(body for method, path, body, _ in cluster.calls if method == "PATCH" and path == SVC)
    assert service["spec"]["type"] == "LoadBalancer" and service["spec"]["ports"][0]["targetPort"] == 8080
    hpa = next(body for method, path, body, _ in cluster.calls if method == "PATCH" and path == HPA)
    assert (hpa["spec"]["minReplicas"], hpa["spec"]["maxReplicas"]) == (2, 5)

    assert adapter.poll_rollout(K8S, handle).state == "running"  # no load balancer address yet
    done = adapter.poll_rollout(K8S, handle)
    assert done.state == "succeeded" and done.service_url == "http://20.1.2.3"
    assert done.effective is not None
    assert (done.effective.cpu, done.effective.min_instances, done.effective.max_instances) == ("0.5", 2, 5)
    assert adapter.service_host_allowed("20.1.2.3") and not adapter.service_host_allowed("10.1.2.3")


def test_kubernetes_reports_pods_that_cannot_start(tmp_path: Path) -> None:
    pinned = f"docker.io/library/nginx@{DIGEST}"
    pod = {
        "spec": {"containers": [{"image": pinned}]},
        "status": {
            "containerStatuses": [{"state": {"waiting": {"reason": "ImagePullBackOff", "message": "pull denied"}}}]
        },
    }
    cluster = Cluster(
        {
            ("GET", DEP): [(200, {"metadata": {"generation": 1}, "status": {"conditions": []}})],
            ("GET", PODS): [(200, {"items": [pod]})],
        }
    )
    status = cluster.adapter(tmp_path).poll_rollout(K8S, {"name": "web", "service_id": "apps/web", "image": pinned})
    assert status.state == "failed" and "ImagePullBackOff: pull denied" in (status.message or "")


def test_kubernetes_pause_delete_logs_and_metrics(tmp_path: Path) -> None:
    deployed = AppTarget(id="app-1", name="web", region="in-cluster", provider_service_id="apps/web", min_instances=2)
    cluster = Cluster(
        {
            ("PATCH", f"{DEP}/scale"): [(200, {})],
            ("DELETE", HPA): [(200, {})],
            ("DELETE", SVC): [(200, {})],
            ("DELETE", DEP): [(200, {})],
            ("GET", DEP): [
                (200, {"status": {}}),
                (404, {}),
                (
                    200,
                    {
                        "spec": {
                            "template": {
                                "spec": {
                                    "containers": [
                                        {"resources": {"requests": {"cpu": "500m"}, "limits": {"memory": "1Gi"}}}
                                    ]
                                }
                            }
                        },
                        "status": {"readyReplicas": 2},
                    },
                ),
            ],
            ("GET", SVC): [(404, {})],
            ("GET", PODS): [(200, {"items": [{"metadata": {"name": "web-1"}}]})],
            ("GET", f"{PODS}/web-1/log"): [(200, "2026-10-17T10:00:00.123456789Z listening on 8080\nnot a log line\n")],
            ("GET", "/apis/metrics.k8s.io/v1beta1/namespaces/apps/pods"): [
                (
                    200,
                    {
                        "items": [
                            {"containers": [{"usage": {"cpu": "250m", "memory": "512Mi"}}]},
                            {"containers": [{"usage": {"cpu": "250000000n", "memory": "256Mi"}}]},
                        ]
                    },
                )
            ],
        }
    )
    adapter = cluster.adapter(tmp_path)
    adapter.set_paused(K8S, deployed, True)
    adapter.set_paused(K8S, deployed, False)
    scales = [body for method, path, body, _ in cluster.calls if path.endswith("/scale")]
    assert scales == [{"spec": {"replicas": 0}}, {"spec": {"replicas": 2}}]

    handle = adapter.start_delete(K8S, deployed)
    assert adapter.poll_delete(K8S, handle).state == "running"
    assert adapter.poll_delete(K8S, handle).state == "deleted"

    entries, truncated = adapter.runtime_logs(K8S, deployed, 50)
    assert [e.message for e in entries] == ["listening on 8080"] and truncated is False

    series = {s.name: s.points[0].value for s in adapter.metrics(K8S, deployed, 60, 60)}
    assert series["instances"] == 2
    assert series["cpu_percent"] == pytest.approx(50.0)
    assert series["memory_percent"] == pytest.approx(37.5)


def test_kubernetes_refuses_builds_and_unknown_registries(tmp_path: Path) -> None:
    adapter = Cluster({}).adapter(tmp_path)
    with pytest.raises(ProviderError) as raised:
        adapter.prepare(K8S, APP, needs_build=True)
    assert raised.value.code == "builds_not_supported"
    with pytest.raises(ProviderError) as raised:
        adapter.resolve_image(K8S, "registry.internal.example/app:1")
    assert raised.value.code == "unsupported_registry"


# ---------- ECS Express Mode ----------

ECS = ConnectionInfo(
    id="e1",
    provider="aws_ecs_express",
    region="ap-south-1",
    account_id="123456789012",
    role_arn="arn:aws:iam::123456789012:role/CloudBridgeDeploy",
    external_id="cb-x",
    ecs_cluster="default",
)
SERVICE_ARN = "arn:aws:ecs:ap-south-1:123456789012:service/default/web"
DEPLOYMENT_ARN = "arn:aws:ecs:ap-south-1:123456789012:service-deployment/default/web/abc"


class StubbedEcs(AwsEcsExpressAdapter):
    def __init__(self, tmp_path: Path, client: Any) -> None:
        super().__init__(make_settings(tmp_path))
        self.client = client

        self.others: dict[str, Any] = {}

    def _client(self, conn: ConnectionInfo, service: str, region: str | None = None) -> Any:
        return self.client if service == "ecs" else self.others[service]


def express_service(status: str = "ACTIVE") -> dict[str, Any]:
    return {
        "serviceArn": SERVICE_ARN,
        "status": {"statusCode": status},
        "currentDeployment": DEPLOYMENT_ARN,
        "activeConfigurations": [
            {
                "cpu": "512",
                "memory": "1024",
                "healthCheckPath": "/",
                "primaryContainer": {"image": "x", "containerPort": 8000},
                "scalingTarget": {"minTaskCount": 1, "maxTaskCount": 3},
                "ingressPaths": [{"accessType": "PUBLIC", "endpoint": "web.ecs.ap-south-1.on.aws"}],
            }
        ],
    }


def test_ecs_express_create_poll_and_delete(tmp_path: Path) -> None:
    import boto3

    client = boto3.client("ecs", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="y")
    stub = Stubber(client)
    stub.add_response("describe_services", {"services": []}, {"cluster": "default", "services": ["web"]})
    stub.add_response(
        "create_express_gateway_service",
        {"service": {"serviceArn": SERVICE_ARN, "currentDeployment": DEPLOYMENT_ARN}},
        {
            "serviceName": "web",
            "cluster": "default",
            "infrastructureRoleArn": "arn:aws:iam::123456789012:role/CloudBridgeECSInfrastructure",
            "tags": ANY,
            "executionRoleArn": "arn:aws:iam::123456789012:role/CloudBridgeECSExecution",
            "healthCheckPath": "/",
            "primaryContainer": {
                "image": f"public.ecr.aws/x/web@{DIGEST}",
                "containerPort": 8000,
                "environment": [{"name": "MODE", "value": "prod"}],
            },
            "cpu": "512",
            "memory": "1024",
            "scalingTarget": {
                "minTaskCount": 1,
                "maxTaskCount": 3,
                "autoScalingMetric": "AVERAGE_CPU",
                "autoScalingTargetValue": 60,
            },
        },
    )
    stub.add_response("describe_express_gateway_service", {"service": express_service()}, {"serviceArn": SERVICE_ARN})
    stub.add_response(
        "describe_service_deployments",
        {"serviceDeployments": [{"status": "IN_PROGRESS"}]},
        {"serviceDeploymentArns": [DEPLOYMENT_ARN]},
    )
    stub.add_response("describe_express_gateway_service", {"service": express_service()}, {"serviceArn": SERVICE_ARN})
    stub.add_response(
        "describe_service_deployments",
        {"serviceDeployments": [{"status": "SUCCESSFUL"}]},
        {"serviceDeploymentArns": [DEPLOYMENT_ARN]},
    )
    stub.add_response("delete_express_gateway_service", {}, {"serviceArn": SERVICE_ARN})
    stub.add_response(
        "describe_express_gateway_service", {"service": express_service("INACTIVE")}, {"serviceArn": SERVICE_ARN}
    )
    spec = SpecInfo.from_snapshot(
        {
            "port": 8000,
            "cpu": "0.5",
            "memory": "1Gi",
            "min_instances": 1,
            "max_instances": 3,
            "concurrency": None,
            "exposure": "public",
            "health_check_path": None,
            "environment": [{"name": "MODE", "value": "prod"}],
            "secret_refs": [],
        }
    )
    # An image-sourced app has no build resources: the cleanup lookups find nothing to delete.
    ecr = boto3.client("ecr", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="y")
    codebuild = boto3.client("codebuild", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="y")
    ecr_stub, codebuild_stub = Stubber(ecr), Stubber(codebuild)
    ecr_stub.add_client_error("describe_repositories", "RepositoryNotFoundException", "none")
    codebuild_stub.add_response("batch_get_projects", {"projects": []}, {"names": ["cloudbridge-web"]})
    with stub, ecr_stub, codebuild_stub:
        adapter = StubbedEcs(tmp_path, client)
        adapter.others = {"ecr": ecr, "codebuild": codebuild}
        app = AppTarget(id="app-1", name="web", region="ap-south-1", provider_service_id=None)
        handle = adapter.start_rollout(ECS, app, ResolvedImage("public.ecr.aws/x/web", DIGEST), spec)
        assert handle["service_id"] == SERVICE_ARN and handle["deployment"] == DEPLOYMENT_ARN
        assert adapter.poll_rollout(ECS, handle).state == "running"
        done = adapter.poll_rollout(ECS, handle)
        assert done.state == "succeeded" and done.service_url == "https://web.ecs.ap-south-1.on.aws"
        assert done.effective is not None
        assert (done.effective.cpu, done.effective.memory, done.effective.max_instances) == ("0.5", "1Gi", 3)
        deployed = AppTarget(id="app-1", name="web", region="ap-south-1", provider_service_id=SERVICE_ARN)
        delete = adapter.start_delete(ECS, deployed)
        assert adapter.poll_delete(ECS, delete).state == "deleted"
    stub.assert_no_pending_responses()
    ecr_stub.assert_no_pending_responses()
    codebuild_stub.assert_no_pending_responses()
    assert adapter.service_host_allowed("web.ecs.ap-south-1.on.aws")
    assert not adapter.service_host_allowed("example.com")


def test_kubernetes_load_balancer_without_status_is_still_pending(tmp_path: Path) -> None:
    # The API server can return null for status fields that are not set yet.
    cluster = Cluster({("GET", SVC): [(200, {"status": {"loadBalancer": None}})]})
    assert cluster.adapter(tmp_path)._load_balancer_url(K8S, "web") is None
    ready = Cluster({("GET", SVC): [(200, {"status": {"loadBalancer": {"ingress": [{"ip": "20.1.2.3"}]}}})]})
    assert ready.adapter(tmp_path)._load_balancer_url(K8S, "web") == "http://20.1.2.3"
