"""The ECS Express Mode adapter against botocore's Stubber: real request and response shapes, no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.providers.base import AppTarget, ConnectionInfo, ProviderError, ResolvedImage, SpecInfo
from app.providers.ecs import AwsEcsExpressAdapter, _cpu_from_units, _memory_from_mib
from tests.conftest import make_settings
from tests.test_aws_adapter import EXPIRES, StubbedSessions

CONN = ConnectionInfo(
    id="0f6c1a52-1111-2222-3333-444455556666",
    provider="aws_ecs_express",
    region="eu-west-1",
    account_id="123456789012",
    role_arn="arn:aws:iam::123456789012:role/CloudBridge",
    external_id="cb-abc",
)
SERVICE_ARN = "arn:aws:ecs:eu-west-1:123456789012:service/default/orders-api"
DEPLOYMENT_ARN = "arn:aws:ecs:eu-west-1:123456789012:service-deployment/default/orders-api/abc"
IMAGE = ResolvedImage(repository="123456789012.dkr.ecr.eu-west-1.amazonaws.com/orders", digest="sha256:" + "a" * 64)
SPEC = SpecInfo.from_snapshot(
    {
        "port": 8080,
        "cpu": "0.5",
        "memory": "1Gi",
        "min_instances": 1,
        "max_instances": 2,
        "concurrency": None,
        "exposure": "public",
        "health_check_path": "/healthz",
        "environment": [{"name": "LOG_LEVEL", "value": "info"}],
        "secret_refs": [],
    }
)


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> StubbedSessions:
    stubs = StubbedSessions()
    monkeypatch.setattr("app.providers.aws.boto3.Session", lambda **_: stubs)
    return stubs


def adapter(tmp_path: Path, stubs: StubbedSessions) -> AwsEcsExpressAdapter:
    return AwsEcsExpressAdapter(make_settings(tmp_path), base_session=stubs)


def assume_role(stubs: StubbedSessions) -> None:
    stubs.stub("sts").add_response(
        "assume_role",
        {
            "Credentials": {
                "AccessKeyId": "ASIAEXAMPLEEXAMPLE00",
                "SecretAccessKey": "secret",
                "SessionToken": "token",
                "Expiration": EXPIRES,
            }
        },
        None,
    )


def created_service() -> dict[str, object]:
    return {
        "service": {
            "serviceArn": SERVICE_ARN,
            "status": {"statusCode": "ACTIVE"},
            "currentDeployment": DEPLOYMENT_ARN,
        }
    }


def test_rollout_recreates_a_service_deleted_outside_cloudbridge(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    ecs = stubs.stub("ecs")
    ecs.add_client_error(
        "describe_express_gateway_service",
        service_error_code="ResourceNotFoundException",
        service_message="Service not found.",
        expected_params={"serviceArn": SERVICE_ARN},
    )
    ecs.add_response("create_express_gateway_service", created_service(), None)
    target = AppTarget(id="app-1", name="orders-api", region="eu-west-1", provider_service_id=SERVICE_ARN)
    handle = adapter(tmp_path, stubs).start_rollout(CONN, target, IMAGE, SPEC)
    assert handle["service_id"] == SERVICE_ARN and handle["deployment"] == DEPLOYMENT_ARN
    ecs.assert_no_pending_responses()


def test_rollout_updates_an_active_service(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    ecs = stubs.stub("ecs")
    ecs.add_response("describe_express_gateway_service", created_service(), {"serviceArn": SERVICE_ARN})
    updated = {"service": {"serviceArn": SERVICE_ARN, "status": {"statusCode": "ACTIVE"}}}  # no deployment
    ecs.add_response("update_express_gateway_service", updated, None)
    target = AppTarget(id="app-1", name="orders-api", region="eu-west-1", provider_service_id=SERVICE_ARN)
    handle = adapter(tmp_path, stubs).start_rollout(CONN, target, IMAGE, SPEC)
    assert handle["service_id"] == SERVICE_ARN and handle["deployment"] is None
    assert handle["previous_deployment"] == DEPLOYMENT_ARN
    ecs.assert_no_pending_responses()


def _service(deployment: str, image: str) -> dict[str, object]:
    return {
        "service": {
            "serviceArn": SERVICE_ARN,
            "status": {"statusCode": "ACTIVE"},
            "currentDeployment": deployment,
            "activeConfigurations": [
                {
                    "cpu": "512",
                    "memory": "1024",
                    "primaryContainer": {"image": image, "containerPort": 8080},
                    "ingressPaths": [{"accessType": "PUBLIC", "endpoint": "or-abc.ecs.eu-west-1.on.aws"}],
                }
            ],
        }
    }


def _handle() -> dict[str, object]:
    return {"service_id": SERVICE_ARN, "deployment": None, "previous_deployment": DEPLOYMENT_ARN, "image": IMAGE.pinned}


def test_update_waits_for_the_new_deployment_instead_of_reporting_the_old_one(
    tmp_path: Path, stubs: StubbedSessions
) -> None:
    assume_role(stubs)
    ecs = stubs.stub("ecs")
    old_image = IMAGE.repository + "@sha256:" + "b" * 64
    new_deployment = DEPLOYMENT_ARN + "-new"
    handle = _handle()
    ecs_adapter = adapter(tmp_path, stubs)
    # Right after the update ECS still reports the old, successful deployment running the old image.
    ecs.add_response("describe_express_gateway_service", _service(DEPLOYMENT_ARN, old_image), None)
    assert ecs_adapter.poll_rollout(CONN, handle).state == "running"
    assert handle["deployment"] is None
    # Then the new deployment appears, and its result counts.
    ecs.add_response("describe_express_gateway_service", _service(new_deployment, IMAGE.pinned), None)
    ecs.add_response(
        "describe_service_deployments",
        {"serviceDeployments": [{"serviceDeploymentArn": new_deployment, "status": "SUCCESSFUL"}]},
        {"serviceDeploymentArns": [new_deployment]},
    )
    assert ecs_adapter.poll_rollout(CONN, handle).state == "succeeded"
    assert handle["deployment"] == new_deployment


def test_redeploying_the_same_image_does_not_wait_for_a_deployment(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    ecs = stubs.stub("ecs")
    ecs.add_response("describe_express_gateway_service", _service(DEPLOYMENT_ARN, IMAGE.pinned), None)
    ecs.add_response(
        "describe_service_deployments",
        {"serviceDeployments": [{"serviceDeploymentArn": DEPLOYMENT_ARN, "status": "SUCCESSFUL"}]},
        None,
    )
    assert adapter(tmp_path, stubs).poll_rollout(CONN, _handle()).state == "succeeded"


def test_rollout_refuses_a_service_of_someone_else(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    ecs = stubs.stub("ecs")
    ecs.add_response("describe_services", {"services": [{"serviceArn": SERVICE_ARN, "status": "ACTIVE"}]}, None)
    ecs.add_response("list_tags_for_resource", {"tags": [{"key": "owner", "value": "other-team"}]}, None)
    target = AppTarget(id="app-1", name="orders-api", region="eu-west-1", provider_service_id=None)
    with pytest.raises(ProviderError) as raised:
        adapter(tmp_path, stubs).start_rollout(CONN, target, IMAGE, SPEC)
    assert raised.value.code == "service_name_taken"


def test_poll_rollout_reports_success_with_url_and_effective_config(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    ecs = stubs.stub("ecs")
    service = {
        "serviceArn": SERVICE_ARN,
        "status": {"statusCode": "ACTIVE"},
        "currentDeployment": DEPLOYMENT_ARN,
        "activeConfigurations": [
            {
                "cpu": "512",
                "memory": "1024",
                "healthCheckPath": "/healthz",
                "primaryContainer": {"image": IMAGE.pinned, "containerPort": 8080},
                "scalingTarget": {"minTaskCount": 1, "maxTaskCount": 2},
                "ingressPaths": [{"accessType": "PUBLIC", "endpoint": "or-abc.ecs.eu-west-1.on.aws"}],
            }
        ],
    }
    ecs.add_response("describe_express_gateway_service", {"service": service}, None)
    ecs.add_response(
        "describe_service_deployments",
        {"serviceDeployments": [{"serviceDeploymentArn": DEPLOYMENT_ARN, "status": "SUCCESSFUL"}]},
        None,
    )
    status = adapter(tmp_path, stubs).poll_rollout(CONN, {"service_id": SERVICE_ARN, "deployment": DEPLOYMENT_ARN})
    assert status.state == "succeeded"
    assert status.service_url == "https://or-abc.ecs.eu-west-1.on.aws"
    assert status.effective is not None
    assert (status.effective.cpu, status.effective.memory, status.effective.port) == ("0.5", "1Gi", 8080)


def test_effective_config_with_missing_values_uses_defaults(tmp_path: Path, stubs: StubbedSessions) -> None:
    effective = adapter(tmp_path, stubs)._ecs_effective(
        {"activeConfigurations": [None, {"cpu": None, "memory": None, "primaryContainer": None}]}
    )
    assert (effective.cpu, effective.memory, effective.port) == ("1", "2Gi", 80)


def test_failed_rollout_reports_the_reason(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    ecs = stubs.stub("ecs")
    ecs.add_response("describe_express_gateway_service", created_service(), None)
    ecs.add_response(
        "describe_service_deployments",
        {
            "serviceDeployments": [
                {"serviceDeploymentArn": DEPLOYMENT_ARN, "status": "ROLLBACK_SUCCESSFUL", "statusReason": "x"}
            ]
        },
        None,
    )
    status = adapter(tmp_path, stubs).poll_rollout(CONN, {"service_id": SERVICE_ARN, "deployment": DEPLOYMENT_ARN})
    assert status.state == "failed" and "kept the previous version" in (status.message or "")


def test_unit_conversions() -> None:
    assert _cpu_from_units("512") == "0.5" and _cpu_from_units("8192") == "8" and _cpu_from_units("x") == "x"
    assert _memory_from_mib("2048") == "2Gi" and _memory_from_mib("1536") == "1536Mi" and _memory_from_mib("?") == "?"
