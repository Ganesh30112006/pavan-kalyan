"""deploy/aws/deploy.py: the CI/CD step that creates or updates CloudBridge's own ECS Express service."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import boto3
import pytest
from botocore.stub import ANY, Stubber

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "492462083767.dkr.ecr.ap-south-1.amazonaws.com/cloudbridge-app@sha256:" + "a" * 64
OLD = "492462083767.dkr.ecr.ap-south-1.amazonaws.com/cloudbridge-app@sha256:" + "b" * 64
ARN = "arn:aws:ecs:ap-south-1:492462083767:service/default/cloudbridge"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cloudbridge_deploy", ROOT / "deploy" / "aws" / "deploy.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deploy = load()
CONFIG: dict[str, Any] = json.loads((ROOT / "deploy" / "aws" / "service.json").read_text(encoding="utf-8"))


def service(deployments: list[dict[str, Any]]) -> dict[str, Any]:
    return {"services": [{"serviceArn": ARN, "status": "ACTIVE", "deployments": deployments}]}


def definition(image: str) -> dict[str, Any]:
    return {"taskDefinition": {"containerDefinitions": [{"image": image}]}}


def test_the_committed_service_definition_is_complete_and_production_safe() -> None:
    deploy.load_config(ROOT / "deploy" / "aws" / "service.json")
    env = CONFIG["environment"]
    assert env["CLOUDBRIDGE_ENVIRONMENT"] == "production"
    assert env["CLOUDBRIDGE_AWS_PRINCIPAL_ARN"] == CONFIG["task_role_arn"]
    # Credentials only ever arrive as Secrets Manager references, never as plain environment values.
    assert not any("PASSWORD" in k or "DATABASE_URL" in k for k in env)
    assert all(v.startswith("arn:aws:secretsmanager:") for v in CONFIG["secrets"].values())
    settings = deploy.service_settings(CONFIG, IMAGE)
    container = settings["primaryContainer"]
    assert container["image"] == IMAGE and container["containerPort"] == 8000
    assert {s["name"] for s in container["secrets"]} == {"CLOUDBRIDGE_DATABASE_URL", "CLOUDBRIDGE_BOOTSTRAP_PASSWORD"}
    assert settings["healthCheckPath"] == "/api/v1/health"


@pytest.mark.parametrize(
    ("deployments", "image", "expected"),
    [
        (
            [{"status": "PRIMARY", "rolloutState": "COMPLETED", "runningCount": 1, "taskDefinition": "td:2"}],
            IMAGE,
            "done",
        ),
        (
            [
                {"status": "PRIMARY", "rolloutState": "IN_PROGRESS", "runningCount": 0, "taskDefinition": "td:2"},
                {"status": "ACTIVE", "rolloutState": "COMPLETED", "runningCount": 1, "taskDefinition": "td:1"},
            ],
            IMAGE,
            "running",
        ),
        (
            [{"status": "PRIMARY", "rolloutState": "FAILED", "runningCount": 0, "taskDefinition": "td:2"}],
            IMAGE,
            "failed",
        ),
        # ECS rolled back: the only deployment left runs the old image.
        (
            [{"status": "PRIMARY", "rolloutState": "COMPLETED", "runningCount": 1, "taskDefinition": "td:1"}],
            OLD,
            "failed",
        ),
    ],
)
def test_rollout_state(deployments: list[dict[str, Any]], image: str, expected: str) -> None:
    ecs = boto3.client("ecs", region_name="ap-south-1")
    with Stubber(ecs) as stub:
        stub.add_response(
            "describe_services", service(deployments), {"cluster": "default", "services": ["cloudbridge"]}
        )
        stub.add_response("describe_task_definition", definition(image), {"taskDefinition": ANY})
        assert deploy.rollout_state(ecs, "default", "cloudbridge", IMAGE) == expected


def run(monkeypatch: pytest.MonkeyPatch, ecs: Any, tmp_path: Path) -> int:
    monkeypatch.setattr(deploy.boto3, "client", lambda *_args, **_kwargs: ecs)
    monkeypatch.setattr(deploy.time, "sleep", lambda _seconds: None)
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    code: int = deploy.deploy(CONFIG, IMAGE)
    return code


def express(url: str) -> dict[str, Any]:
    return {
        "service": {
            "activeConfigurations": [
                {"createdAt": "2026-11-25T00:00:00Z", "ingressPaths": [{"accessType": "PUBLIC", "endpoint": url}]}
            ]
        }
    }


def test_first_deploy_creates_the_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ecs = boto3.client("ecs", region_name="ap-south-1")
    with Stubber(ecs) as stub:
        stub.add_client_error("describe_services", "ClusterNotFoundException")
        stub.add_response(
            "create_express_gateway_service",
            {"service": {"serviceArn": ARN}},
            {
                "serviceName": "cloudbridge",
                "cluster": "default",
                "infrastructureRoleArn": CONFIG["infrastructure_role_arn"],
                "tags": ANY,
                **deploy.service_settings(CONFIG, IMAGE),
            },
        )
        done = [{"status": "PRIMARY", "rolloutState": "COMPLETED", "runningCount": 1, "taskDefinition": "td:1"}]
        stub.add_response("describe_services", service(done), {"cluster": "default", "services": ["cloudbridge"]})
        stub.add_response("describe_task_definition", definition(IMAGE), {"taskDefinition": "td:1"})
        stub.add_response(
            "describe_express_gateway_service", express("cb-1.ecs.ap-south-1.on.aws"), {"serviceArn": ARN}
        )
        assert run(monkeypatch, ecs, tmp_path) == 0
    assert (tmp_path / "github-output").read_text() == "url=https://cb-1.ecs.ap-south-1.on.aws\n"


def test_later_deploys_update_and_fail_when_ecs_rolls_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ecs = boto3.client("ecs", region_name="ap-south-1")
    with Stubber(ecs) as stub:
        stub.add_response("describe_services", service([]), {"cluster": "default", "services": ["cloudbridge"]})
        stub.add_response(
            "update_express_gateway_service",
            {"service": {}},
            {"serviceArn": ARN, **deploy.service_settings(CONFIG, IMAGE)},
        )
        rolled_back = [{"status": "PRIMARY", "rolloutState": "COMPLETED", "runningCount": 1, "taskDefinition": "td:1"}]
        stub.add_response(
            "describe_services", service(rolled_back), {"cluster": "default", "services": ["cloudbridge"]}
        )
        stub.add_response("describe_task_definition", definition(OLD), {"taskDefinition": "td:1"})
        stub.add_response("describe_express_gateway_service", express("https://cb-1.example"), {"serviceArn": ARN})
        assert run(monkeypatch, ecs, tmp_path) == 1
    assert not (tmp_path / "github-output").exists()


def test_images_must_be_pinned_by_digest(capsys: pytest.CaptureFixture[str]) -> None:
    assert deploy.main(["--config", "deploy/aws/service.json", "--image", "repo:latest"]) == 2
    assert "pinned by digest" in capsys.readouterr().err
