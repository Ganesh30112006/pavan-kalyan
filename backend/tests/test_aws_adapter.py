"""The App Runner adapter against botocore's Stubber: real request shapes, no network."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import boto3
import pytest
import yaml
from botocore.exceptions import ClientError, NoCredentialsError
from botocore.stub import ANY, Stubber

from app.providers.aws import (
    APP_TAG_KEY,
    BUILDSPEC,
    AwsAppRunnerAdapter,
    _cpu_from_units,
    _memory_from_units,
    service_name,
    translate,
)
from app.providers.base import AppTarget, ConnectionInfo, ResolvedImage, SourceInfo, SpecInfo
from tests.conftest import make_settings

CONN = ConnectionInfo(
    id="0f6c1a52-1111-2222-3333-444455556666",
    provider="aws_app_runner",
    region="eu-west-1",
    account_id="123456789012",
    role_arn="arn:aws:iam::123456789012:role/CloudBridge",
    external_id="cb-abc",
)
SERVICE_ARN = "arn:aws:apprunner:eu-west-1:123456789012:service/orders-api/0123456789abcdef"
EXPIRES = dt.datetime(2099, 1, 1, tzinfo=dt.UTC)


class StubbedSessions:
    """Hands out boto3 clients wired to Stubbers, keyed by service name."""

    def __init__(self) -> None:
        self.session: Any = boto3.Session(aws_access_key_id="x", aws_secret_access_key="y", region_name="eu-west-1")
        self.clients: dict[str, Any] = {}
        self.stubbers: dict[str, Stubber] = {}

    def stub(self, service: str) -> Stubber:
        if service not in self.stubbers:
            client = self.session.client(service, region_name="eu-west-1")
            self.clients[service] = client
            self.stubbers[service] = Stubber(client)
            self.stubbers[service].activate()
        return self.stubbers[service]

    def client(self, service: str, **_: Any) -> Any:
        self.stub(service)
        return self.clients[service]


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> StubbedSessions:
    stubs = StubbedSessions()
    monkeypatch.setattr("app.providers.aws.boto3.Session", lambda **_: stubs)
    return stubs


def adapter(tmp_path: Path, stubs: StubbedSessions) -> AwsAppRunnerAdapter:
    return AwsAppRunnerAdapter(make_settings(tmp_path), base_session=stubs)


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
        {
            "RoleArn": CONN.role_arn,
            "RoleSessionName": "cloudbridge-0f6c1a52",
            "ExternalId": "cb-abc",
            "DurationSeconds": 3600,
        },
    )


def test_buildspec_is_valid_yaml_and_quotes_user_input() -> None:
    spec = yaml.safe_load(BUILDSPEC)
    commands = spec["phases"]["pre_build"]["commands"] + spec["phases"]["build"]["commands"]
    assert spec["env"]["exported-variables"] == ["IMAGE_DIGEST", "GIT_COMMIT"]
    for variable in ("REPO_URL", "GIT_REF", "DOCKERFILE_PATH", "BUILD_CONTEXT", "IMAGE_URI"):
        assert all(f"${variable}" not in c or f'"${variable}"' in c or f'/${variable}"' in c for c in commands)


def test_unit_conversions() -> None:
    assert (
        _cpu_from_units("1024") == "1" and _cpu_from_units("0.25 vCPU") == "0.25" and _cpu_from_units("256") == "0.25"
    )
    assert (
        _memory_from_units("2048") == "2Gi"
        and _memory_from_units("0.5 GB") == "512Mi"
        and _memory_from_units("3 GB") == "3Gi"
    )
    assert service_name("api") == "api-app" and service_name("orders") == "orders"


def test_validate_connection_success(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    stubs.stub("apprunner").add_response("list_services", {"ServiceSummaryList": []}, {"MaxResults": 1})
    stubs.stub("ecr").add_response("describe_repositories", {"repositories": []}, {"maxResults": 1})
    stubs.stub("sts").add_response(
        "get_caller_identity",
        {"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/x", "UserId": "u"},
    )
    stubs.stub("codebuild").add_client_error("list_projects", "AccessDeniedException", "not allowed")
    stages: list[str] = []
    result = adapter(tmp_path, stubs).validate_connection(CONN, lambda stage, checks: stages.append(stage))
    assert result.status == "connected" and result.error_code is None
    assert [c.status for c in result.checks] == ["passed", "passed", "passed", "passed", "skipped"]
    assert stages[0] == "connecting" and "validating_permissions" in stages and "checking_readiness" in stages


def test_validate_connection_bad_trust_policy(tmp_path: Path, stubs: StubbedSessions) -> None:
    stubs.stub("sts").add_client_error(
        "assume_role", "AccessDenied", "User is not authorized to perform sts:AssumeRole"
    )
    result = adapter(tmp_path, stubs).validate_connection(CONN, lambda *_: None)
    assert result.status == "invalid_credentials"
    assert result.checks[0].status == "failed"
    assert "trust policy" in (result.checks[0].detail or "")
    assert all(c.status == "skipped" for c in result.checks[1:])


def test_validate_connection_missing_permission(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    stubs.stub("apprunner").add_client_error(
        "list_services", "AccessDeniedException", "not authorized: apprunner:ListServices"
    )
    result = adapter(tmp_path, stubs).validate_connection(CONN, lambda *_: None)
    assert result.status == "insufficient_permissions"
    assert [c.status for c in result.checks][:3] == ["passed", "failed", "skipped"]


def test_platform_without_aws_identity(tmp_path: Path) -> None:
    class NoCreds:
        def client(self, *args: Any, **kwargs: Any) -> Any:
            raise NoCredentialsError()

    result = AwsAppRunnerAdapter(make_settings(tmp_path), base_session=NoCreds()).validate_connection(
        CONN, lambda *_: None
    )
    assert result.status == "error" and result.error_code == "platform_not_configured"


def test_resolve_private_ecr_image(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    digest = "sha256:" + "c" * 64
    stubs.stub("ecr").add_response(
        "describe_images",
        {"imageDetails": [{"imageDigest": digest}]},
        {"registryId": "123456789012", "repositoryName": "team/orders", "imageIds": [{"imageTag": "1.4.0"}]},
    )
    resolved = adapter(tmp_path, stubs).resolve_image(
        CONN, "123456789012.dkr.ecr.eu-west-1.amazonaws.com/team/orders:1.4.0"
    )
    assert resolved == ResolvedImage("123456789012.dkr.ecr.eu-west-1.amazonaws.com/team/orders", digest)


def test_create_service_rollout(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    apprunner = stubs.stub("apprunner")
    apprunner.add_response(
        "list_auto_scaling_configurations",
        {"AutoScalingConfigurationSummaryList": []},
        {"AutoScalingConfigurationName": "cloudbridge-1-3-100", "LatestOnly": True},
    )
    apprunner.add_response(
        "create_auto_scaling_configuration",
        {
            "AutoScalingConfiguration": {
                "AutoScalingConfigurationArn": "arn:aws:apprunner:eu-west-1:123456789012:autoscalingconfiguration/cloudbridge-1-3-100/1/x"
            }
        },
        {
            "AutoScalingConfigurationName": "cloudbridge-1-3-100",
            "MinSize": 1,
            "MaxSize": 3,
            "MaxConcurrency": 100,
            "Tags": ANY,
        },
    )
    apprunner.add_response("list_services", {"ServiceSummaryList": []}, {"MaxResults": 20})
    expected = {
        "ServiceName": "orders-api",
        "SourceConfiguration": {
            "ImageRepository": {
                "ImageIdentifier": f"123456789012.dkr.ecr.eu-west-1.amazonaws.com/orders@sha256:{'a' * 64}",
                "ImageRepositoryType": "ECR",
                "ImageConfiguration": {
                    "Port": "8080",
                    "RuntimeEnvironmentVariables": {"LOG_LEVEL": "info"},
                    "RuntimeEnvironmentSecrets": {"DB": "arn:aws:secretsmanager:eu-west-1:123456789012:secret:db"},
                },
            },
            "AutoDeploymentsEnabled": False,
            "AuthenticationConfiguration": {
                "AccessRoleArn": "arn:aws:iam::123456789012:role/CloudBridgeAppRunnerECRAccess"
            },
        },
        "InstanceConfiguration": {
            "Cpu": "1024",
            "Memory": "2048",
            "InstanceRoleArn": "arn:aws:iam::123456789012:role/CloudBridgeAppRunnerInstance",
        },
        "AutoScalingConfigurationArn": ANY,
        "HealthCheckConfiguration": {"Protocol": "HTTP", "Path": "/healthz"},
        "NetworkConfiguration": {
            "IngressConfiguration": {"IsPubliclyAccessible": True},
            "EgressConfiguration": {"EgressType": "DEFAULT"},
        },
        "Tags": ANY,
    }
    service = {
        "ServiceName": "orders-api",
        "ServiceId": "0123456789abcdef0123456789abcdef",
        "ServiceArn": SERVICE_ARN,
        "ServiceUrl": "abc.eu-west-1.awsapprunner.com",
        "CreatedAt": EXPIRES,
        "UpdatedAt": EXPIRES,
        "Status": "OPERATION_IN_PROGRESS",
        "SourceConfiguration": {},
        "InstanceConfiguration": {},
        "NetworkConfiguration": {},
        "AutoScalingConfigurationSummary": {},
    }
    apprunner.add_response(
        "create_service", {"Service": service, "OperationId": "11111111-2222-3333-4444-555555555555"}, expected
    )
    spec = SpecInfo(
        port=8080, cpu="1", memory="2Gi", min_instances=0, max_instances=3, concurrency=None, exposure="public",
        health_check_path="/healthz", environment=[("LOG_LEVEL", "info")],
        secret_refs=[("DB", "arn:aws:secretsmanager:eu-west-1:123456789012:secret:db")],
    )  # fmt: skip
    target = AppTarget(id="app-1", name="orders-api", region="eu-west-1", provider_service_id=None)
    image = ResolvedImage("123456789012.dkr.ecr.eu-west-1.amazonaws.com/orders", "sha256:" + "a" * 64)
    handle = adapter(tmp_path, stubs).start_rollout(CONN, target, image, spec)
    assert handle == {"service_id": SERVICE_ARN, "operation_id": "11111111-2222-3333-4444-555555555555"}
    apprunner.assert_no_pending_responses()


def test_poll_rollout_success_reads_effective_configuration(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    apprunner = stubs.stub("apprunner")
    apprunner.add_response(
        "list_operations",
        {"OperationSummaryList": [{"Id": "11111111-2222-3333-4444-555555555555", "Status": "SUCCEEDED"}]},
        {"ServiceArn": SERVICE_ARN, "MaxResults": 20},
    )
    scaling_arn = "arn:aws:apprunner:eu-west-1:123456789012:autoscalingconfiguration/cloudbridge-1-3-100/1/x"
    apprunner.add_response(
        "describe_service",
        {
            "Service": {
                "ServiceName": "orders-api",
                "ServiceId": "0123456789abcdef0123456789abcdef",
                "ServiceArn": SERVICE_ARN,
                "ServiceUrl": "abc.eu-west-1.awsapprunner.com",
                "CreatedAt": EXPIRES,
                "UpdatedAt": EXPIRES,
                "Status": "RUNNING",
                "SourceConfiguration": {
                    "ImageRepository": {
                        "ImageIdentifier": "x",
                        "ImageRepositoryType": "ECR",
                        "ImageConfiguration": {"Port": "8080"},
                    }
                },
                "InstanceConfiguration": {"Cpu": "1024", "Memory": "2048"},
                "HealthCheckConfiguration": {"Protocol": "HTTP", "Path": "/healthz"},
                "NetworkConfiguration": {"IngressConfiguration": {"IsPubliclyAccessible": False}},
                "AutoScalingConfigurationSummary": {"AutoScalingConfigurationArn": scaling_arn},
            }
        },
        {"ServiceArn": SERVICE_ARN},
    )
    apprunner.add_response(
        "describe_auto_scaling_configuration",
        {"AutoScalingConfiguration": {"MinSize": 1, "MaxSize": 3, "MaxConcurrency": 100}},
        {"AutoScalingConfigurationArn": scaling_arn},
    )
    status = adapter(tmp_path, stubs).poll_rollout(
        CONN, {"service_id": SERVICE_ARN, "operation_id": "11111111-2222-3333-4444-555555555555"}
    )
    assert status.state == "succeeded"
    assert status.service_url == "https://abc.eu-west-1.awsapprunner.com"
    assert status.effective is not None
    assert (status.effective.cpu, status.effective.memory, status.effective.exposure) == ("1", "2Gi", "private")
    assert (status.effective.min_instances, status.effective.concurrency, status.effective.health_check_path) == (
        1,
        100,
        "/healthz",
    )


def test_poll_rollout_reports_provider_rollback(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    apprunner = stubs.stub("apprunner")
    apprunner.add_response(
        "list_operations",
        {"OperationSummaryList": [{"Id": "66666666-2222-3333-4444-555555555555", "Status": "ROLLBACK_SUCCEEDED"}]},
        {"ServiceArn": SERVICE_ARN, "MaxResults": 20},
    )
    apprunner.add_response(
        "describe_service",
        {
            "Service": {
                "ServiceName": "orders-api",
                "ServiceId": "0123456789abcdef0123456789abcdef",
                "ServiceArn": SERVICE_ARN,
                "CreatedAt": EXPIRES,
                "UpdatedAt": EXPIRES,
                "Status": "RUNNING",
                "SourceConfiguration": {},
                "InstanceConfiguration": {},
                "NetworkConfiguration": {},
                "AutoScalingConfigurationSummary": {},
            }
        },  # fmt: skip
        {"ServiceArn": SERVICE_ARN},
    )
    status = adapter(tmp_path, stubs).poll_rollout(
        CONN, {"service_id": SERVICE_ARN, "operation_id": "66666666-2222-3333-4444-555555555555"}
    )
    assert status.state == "failed"
    assert "kept the previous version" in (status.message or "")


def test_codebuild_failure_is_summarised_safely(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    stubs.stub("codebuild").add_response(
        "batch_get_builds",
        {
            "builds": [
                {
                    "id": "cloudbridge-orders:1",
                    "buildStatus": "FAILED",
                    "phases": [
                        {
                            "phaseType": "BUILD",
                            "phaseStatus": "FAILED",
                            "contexts": [{"message": "COMMAND_EXECUTION_ERROR: token=abc123 failed"}],
                        }
                    ],
                }
            ]
        },
        {"ids": ["cloudbridge-orders:1"]},
    )
    status = adapter(tmp_path, stubs).poll_build(CONN, {"build_id": "cloudbridge-orders:1", "image_uri": "x:1"})
    assert status.state == "failed"
    assert "abc123" not in (status.message or "") and "[redacted]" in (status.message or "")


TARGET = AppTarget(id="app-1", name="orders-api", region="eu-west-1", provider_service_id=SERVICE_ARN)


def test_build_setup_and_start_use_valid_requests(tmp_path: Path, stubs: StubbedSessions) -> None:
    """botocore validates every request against the service model, so this checks parameter names and types."""
    assume_role(stubs)
    stubs.stub("apprunner").add_response("list_services", {"ServiceSummaryList": []}, {"MaxResults": 1})
    stubs.stub("ecr").add_client_error("create_repository", "RepositoryAlreadyExistsException", "exists")
    codebuild = stubs.stub("codebuild")
    codebuild.add_client_error("create_project", "ResourceAlreadyExistsException", "exists")
    codebuild.add_response("update_project", {"project": {"name": "cloudbridge-orders-api"}})
    codebuild.add_response("start_build", {"build": {"id": "cloudbridge-orders-api:abc"}})
    aws = adapter(tmp_path, stubs)
    assert aws.prepare(CONN, TARGET, needs_build=True).ready is True
    source = SourceInfo("repository", None, "https://github.com/acme/orders.git", "branch", "main", "Dockerfile", ".")
    handle = aws.start_build(CONN, TARGET, source, "r1-abcdef12", "0f6c1a52-1111-2222-3333-444455556666")
    assert handle == {
        "build_id": "cloudbridge-orders-api:abc",
        "image_uri": "123456789012.dkr.ecr.eu-west-1.amazonaws.com/cloudbridge/orders-api:r1-abcdef12",
    }
    codebuild.assert_no_pending_responses()


def test_successful_build_reports_digest_and_logs(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    digest = "sha256:" + "d" * 64
    stubs.stub("codebuild").add_response(
        "batch_get_builds",
        {
            "builds": [
                {
                    "id": "b:1",
                    "buildStatus": "SUCCEEDED",
                    "exportedEnvironmentVariables": [
                        {"name": "IMAGE_DIGEST", "value": digest},
                        {"name": "GIT_COMMIT", "value": "abc1234"},
                    ],
                    "logs": {"groupName": "/aws/codebuild/x", "streamName": "s"},
                }
            ]
        },
    )
    stubs.stub("logs").add_response(
        "get_log_events", {"events": [{"timestamp": 1_700_000_000_000, "message": "done\n"}]}
    )
    status = adapter(tmp_path, stubs).poll_build(CONN, {"build_id": "b:1", "image_uri": "registry/x:r1"})
    assert status.state == "succeeded" and status.image_digest == digest and status.commit == "abc1234"
    assert status.image_uri == "registry/x" and status.log_lines[0][1] == "done"


def test_delete_and_logs_requests(tmp_path: Path, stubs: StubbedSessions, caplog: pytest.LogCaptureFixture) -> None:
    assume_role(stubs)
    apprunner = stubs.stub("apprunner")
    apprunner.add_response(
        "delete_service",
        {"Service": {"ServiceName": "orders-api", "ServiceId": "0123456789abcdef0123456789abcdef", "ServiceArn": SERVICE_ARN,
                     "CreatedAt": EXPIRES, "UpdatedAt": EXPIRES, "Status": "OPERATION_IN_PROGRESS", "SourceConfiguration": {},
                     "InstanceConfiguration": {}, "NetworkConfiguration": {}, "AutoScalingConfigurationSummary": {}},
         "OperationId": "11111111-2222-3333-4444-555555555555"},
        {"ServiceArn": SERVICE_ARN},
    )  # fmt: skip
    apprunner.add_client_error("describe_service", "ResourceNotFoundException", "gone")
    ecr, codebuild = stub_build_cleanup(stubs, repo_app_id="app-1", project_tags={APP_TAG_KEY: "app-1"})
    ecr.add_response(
        "delete_repository", {"repository": {"repositoryName": REPO}}, {"repositoryName": REPO, "force": True}
    )
    codebuild.add_response("delete_project", {}, {"name": PROJECT})
    stubs.stub("logs").add_response(
        "filter_log_events", {"events": [{"timestamp": 1_700_000_000_000, "message": "hello"}]}
    )
    aws = adapter(tmp_path, stubs)
    handle = aws.start_delete(CONN, TARGET)
    assert aws.poll_delete(CONN, handle).state == "deleted"
    ecr.assert_no_pending_responses()
    codebuild.assert_no_pending_responses()
    assert cleanup_warnings(caplog) == []
    entries, truncated = aws.runtime_logs(CONN, TARGET, 200)
    assert [e.message for e in entries] == ["hello"] and truncated is False


REPO = "cloudbridge/orders-api"
PROJECT = "cloudbridge-orders-api"
REPO_ARN = "arn:aws:ecr:eu-west-1:123456789012:repository/cloudbridge/orders-api"


def stub_build_cleanup(
    stubs: StubbedSessions, repo_app_id: str, project_tags: dict[str, str]
) -> tuple[Stubber, Stubber]:
    """The lookups made before CloudBridge deletes a deleted application's ECR repository and CodeBuild project."""
    ecr = stubs.stub("ecr")
    ecr.add_response(
        "describe_repositories",
        {"repositories": [{"repositoryName": REPO, "repositoryArn": REPO_ARN}]},
        {"repositoryNames": [REPO]},
    )
    ecr.add_response(
        "list_tags_for_resource",
        {"tags": [{"Key": "managed-by", "Value": "cloudbridge"}, {"Key": APP_TAG_KEY, "Value": repo_app_id}]},
        {"resourceArn": REPO_ARN},
    )
    codebuild = stubs.stub("codebuild")
    tags = [{"key": "managed-by", "value": "cloudbridge"}] + [{"key": k, "value": v} for k, v in project_tags.items()]
    codebuild.add_response("batch_get_projects", {"projects": [{"name": PROJECT, "tags": tags}]}, {"names": [PROJECT]})
    return ecr, codebuild


def cleanup_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    # Cleanup is best effort and swallows errors, including a Stubber's "unexpected call", so a wrong
    # delete call would only show up as one of these warnings.
    return [r.getMessage() for r in caplog.records if r.getMessage().endswith("left in place")]


def test_delete_keeps_build_resources_that_belong_to_another_application(
    tmp_path: Path, stubs: StubbedSessions, caplog: pytest.LogCaptureFixture
) -> None:
    assume_role(stubs)
    ecr, codebuild = stub_build_cleanup(stubs, repo_app_id="someone-else", project_tags={APP_TAG_KEY: "someone-else"})
    never_deployed = AppTarget(id="app-1", name="orders-api", region="eu-west-1", provider_service_id=None)
    aws = adapter(tmp_path, stubs)
    assert aws.poll_delete(CONN, aws.start_delete(CONN, never_deployed)).state == "deleted"
    ecr.assert_no_pending_responses()  # looked up, never deleted
    codebuild.assert_no_pending_responses()
    assert cleanup_warnings(caplog) == []


def test_delete_succeeds_when_the_role_cannot_clean_up_build_resources(
    tmp_path: Path, stubs: StubbedSessions, caplog: pytest.LogCaptureFixture
) -> None:
    assume_role(stubs)
    stubs.stub("ecr").add_client_error("describe_repositories", "AccessDeniedException", "not allowed")
    stubs.stub("codebuild").add_client_error("batch_get_projects", "AccessDeniedException", "not allowed")
    never_deployed = AppTarget(id="app-1", name="orders-api", region="eu-west-1", provider_service_id=None)
    aws = adapter(tmp_path, stubs)
    assert aws.poll_delete(CONN, aws.start_delete(CONN, never_deployed)).state == "deleted"
    assert cleanup_warnings(caplog) == ["ECR repository left in place", "CodeBuild project left in place"]


def test_expired_platform_login_is_not_reported_as_an_outage(tmp_path: Path) -> None:
    from botocore.exceptions import LoginRefreshRequired

    class Expired:
        def client(self, *args: Any, **kwargs: Any) -> Any:
            raise LoginRefreshRequired()

    result = AwsAppRunnerAdapter(make_settings(tmp_path), base_session=Expired()).validate_connection(
        CONN, lambda *_: None
    )
    assert result.status == "error" and result.error_code == "platform_not_configured"
    assert "expired" in (result.checks[0].detail or "")


def _service(status: str) -> dict[str, Any]:
    return {
        "ServiceName": "orders-api",
        "ServiceId": "0123456789abcdef0123456789abcdef",
        "ServiceArn": SERVICE_ARN,
        "CreatedAt": EXPIRES,
        "UpdatedAt": EXPIRES,
        "Status": status,
        "SourceConfiguration": {},
        "InstanceConfiguration": {},
        "NetworkConfiguration": {},
        "AutoScalingConfigurationSummary": {},
    }


def _existing_scaling(apprunner: Stubber) -> None:
    apprunner.add_response(
        "list_auto_scaling_configurations",
        {
            "AutoScalingConfigurationSummaryList": [
                {
                    "AutoScalingConfigurationArn": "arn:aws:apprunner:eu-west-1:123456789012:autoscalingconfiguration/cloudbridge-1-3-100/1/x",
                    "Status": "active",  # as the live API returns it
                }
            ]
        },
        {"AutoScalingConfigurationName": "cloudbridge-1-3-100", "LatestOnly": True},
    )


ROLLOUT_SPEC = SpecInfo(
    port=8080, cpu="1", memory="2Gi", min_instances=1, max_instances=3, concurrency=None, exposure="public",
    health_check_path=None, environment=[], secret_refs=[],
)  # fmt: skip
ROLLOUT_IMAGE = ResolvedImage("123456789012.dkr.ecr.eu-west-1.amazonaws.com/orders", "sha256:" + "a" * 64)


def test_rollout_updates_a_running_service(tmp_path: Path, stubs: StubbedSessions) -> None:
    assume_role(stubs)
    apprunner = stubs.stub("apprunner")
    _existing_scaling(apprunner)
    apprunner.add_response("describe_service", {"Service": _service("RUNNING")}, {"ServiceArn": SERVICE_ARN})
    apprunner.add_response(
        "update_service",
        {"Service": _service("OPERATION_IN_PROGRESS"), "OperationId": "22222222-2222-3333-4444-555555555555"},
        {
            "ServiceArn": SERVICE_ARN,
            "SourceConfiguration": ANY,
            "InstanceConfiguration": {"Cpu": "1024", "Memory": "2048"},
            "AutoScalingConfigurationArn": ANY,
            "HealthCheckConfiguration": {"Protocol": "TCP"},
            "NetworkConfiguration": ANY,
        },
    )
    target = AppTarget(id="app-1", name="orders-api", region="eu-west-1", provider_service_id=SERVICE_ARN)
    handle = adapter(tmp_path, stubs).start_rollout(CONN, target, ROLLOUT_IMAGE, ROLLOUT_SPEC)
    assert handle == {"service_id": SERVICE_ARN, "operation_id": "22222222-2222-3333-4444-555555555555"}
    apprunner.assert_no_pending_responses()


def test_rollout_replaces_a_service_whose_creation_failed(tmp_path: Path, stubs: StubbedSessions) -> None:
    from app.providers.base import ProviderError

    assume_role(stubs)
    apprunner = stubs.stub("apprunner")
    _existing_scaling(apprunner)
    apprunner.add_response("describe_service", {"Service": _service("CREATE_FAILED")}, {"ServiceArn": SERVICE_ARN})
    apprunner.add_response(
        "delete_service",
        {"Service": _service("OPERATION_IN_PROGRESS"), "OperationId": "33333333-2222-3333-4444-555555555555"},
        {"ServiceArn": SERVICE_ARN},
    )
    target = AppTarget(id="app-1", name="orders-api", region="eu-west-1", provider_service_id=SERVICE_ARN)
    aws = adapter(tmp_path, stubs)
    with pytest.raises(ProviderError) as raised:
        aws.start_rollout(CONN, target, ROLLOUT_IMAGE, ROLLOUT_SPEC)
    assert raised.value.code == "provider_busy" and raised.value.retryable

    # Once the failed service is gone, the next attempt creates a new one.
    _existing_scaling(apprunner)
    apprunner.add_response("describe_service", {"Service": _service("DELETED")}, {"ServiceArn": SERVICE_ARN})
    new_arn = SERVICE_ARN.replace("0123456789abcdef", "fedcba9876543210")
    created = dict(_service("OPERATION_IN_PROGRESS"), ServiceArn=new_arn)
    apprunner.add_response(
        "create_service", {"Service": created, "OperationId": "44444444-2222-3333-4444-555555555555"}
    )
    handle = aws.start_rollout(CONN, target, ROLLOUT_IMAGE, ROLLOUT_SPEC)
    assert handle == {"service_id": new_arn, "operation_id": "44444444-2222-3333-4444-555555555555"}
    apprunner.assert_no_pending_responses()


def test_app_runner_access_denied_reported_as_invalid_request_is_a_permission_problem() -> None:
    # Seen live: CreateService without iam:CreateServiceLinkedRole in a fresh account.

    from app.providers.aws import translate

    error = ClientError(
        {
            "Error": {
                "Code": "InvalidRequestException",
                "Message": "AccessDenied. Couldn't create a service-linked role for App Runner.",
            }
        },
        "CreateService",
    )
    translated = translate(error)
    assert translated.code == "insufficient_permissions"
    assert "service-linked role" in translated.message


def test_fresh_service_linked_role_is_retried() -> None:
    # First ECS Express deployment in an account: AWS creates the role and reports it cannot assume it yet.
    error = ClientError(
        {
            "Error": {
                "Code": "InvalidParameterException",
                "Message": "Unable to assume the service linked role. Please verify that the ECS service linked role exists.",
            }
        },
        "CreateExpressGatewayService",
    )
    mapped = translate(error)
    assert mapped.retryable and mapped.code == "provider_unavailable"
