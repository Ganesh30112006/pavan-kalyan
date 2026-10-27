from __future__ import annotations

from app.providers.base import ProviderError
from tests.conftest import ApiClient, Env, image_app
from tests.contract import CAPABILITIES, CONNECTION, SETUP, check, field_errors

AWS = {
    "name": "Production AWS",
    "provider": "aws_app_runner",
    "default_region": "eu-west-1",
    "aws": {"account_id": "123456789012", "role_arn": "arn:aws:iam::123456789012:role/CloudBridgeDeploy"},
}
GCP = {
    "name": "Production GCP",
    "provider": "gcp_cloud_run",
    "default_region": "europe-west1",
    "gcp": {
        "project_id": "acme-prod-1",
        "workload_identity_provider": "projects/123456/locations/global/workloadIdentityPools/cloudbridge/providers/aws-main",
        "service_account_email": "deployer@acme-prod-1.iam.gserviceaccount.com",
    },
}


def test_capabilities(owner: ApiClient) -> None:
    for provider in ("aws_app_runner", "gcp_cloud_run"):
        response = owner.get(f"/providers/{provider}/capabilities")
        assert response.status_code == 200
        check(response.json(), CAPABILITIES)
    aws = owner.get("/providers/aws_app_runner/capabilities").json()
    assert aws["min_instances_floor"] == 1 and "worker" not in aws["supported_application_types"]
    assert owner.get("/providers/azure/capabilities").status_code == 404


def test_create_aws_connection_generates_external_id(owner: ApiClient) -> None:
    response = owner.post("/cloud-connections", json=AWS, headers={"Idempotency-Key": "k-1"})
    assert response.status_code == 201
    body = response.json()
    check(body, CONNECTION)
    assert body["status"] == "pending_validation"
    assert body["setup"]["external_id"].startswith("cb-")
    assert body["setup"]["trusted_principal"] == "arn:aws:iam::999999999999:role/cloudbridge-worker"
    assert body["config"]["role_arn"] == AWS["aws"]["role_arn"]  # type: ignore[index]

    again = owner.post("/cloud-connections", json=AWS, headers={"Idempotency-Key": "k-1"})
    assert again.status_code == 201 and again.json()["id"] == body["id"]
    reused = owner.post("/cloud-connections", json={**AWS, "name": "Other"}, headers={"Idempotency-Key": "k-1"})
    assert reused.status_code == 409 and reused.json()["error"]["code"] == "idempotency_key_reused"
    duplicate = owner.post("/cloud-connections", json=AWS)
    assert duplicate.status_code == 409
    assert field_errors(duplicate.json()) == {"name": "This name is already in use."}

    listed = owner.get("/cloud-connections").json()
    assert [c["id"] for c in listed] == [body["id"]]


def test_create_gcp_connection(owner: ApiClient) -> None:
    response = owner.post("/cloud-connections", json=GCP)
    assert response.status_code == 201
    body = response.json()
    check(body, CONNECTION)
    assert body["setup"]["external_id"] is None
    assert body["config"]["project_id"] == "acme-prod-1"


def test_aws_setup_names_the_roles_the_setup_script_creates(owner: ApiClient) -> None:
    aws = owner.post("/cloud-connections", json=AWS).json()
    assert aws["setup"]["aws"] == {
        "ecr_repository_prefix": "cloudbridge",
        "apprunner_access_role": "CloudBridgeAppRunnerECRAccess",
        "apprunner_instance_role": "CloudBridgeAppRunnerInstance",
        "codebuild_role": "CloudBridgeCodeBuild",
        "ecs_execution_role": "CloudBridgeECSExecution",
        "ecs_infrastructure_role": "CloudBridgeECSInfrastructure",
    }
    assert owner.post("/cloud-connections", json=GCP).json()["setup"]["aws"] is None


def test_provider_setup_is_known_before_connecting(env: Env, owner: ApiClient) -> None:
    env.settings.azure_federation_issuer = "https://abc.tokens.sts.global.api.aws"
    env.settings.azure_federation_subject = "arn:aws:iam::999999999999:role/cloudbridge-worker"
    env.settings.aws_codebuild_role_name = "AcmeBuild"

    for provider in ("azure_container_apps", "kubernetes"):
        body = owner.get(f"/providers/{provider}/setup").json()
        check(body, SETUP)
        assert body == {
            "external_id": None,
            "trusted_principal": "arn:aws:iam::999999999999:role/cloudbridge-worker",
            "federation_issuer": "https://abc.tokens.sts.global.api.aws",
            "federation_audience": "api://AzureADTokenExchange",
            "aws": None,
        }
    aws = owner.get("/providers/aws_ecs_express/setup").json()
    assert aws["external_id"] is None and aws["federation_issuer"] is None
    assert aws["trusted_principal"] == "arn:aws:iam::999999999999:role/cloudbridge-worker"
    assert aws["aws"]["codebuild_role"] == "AcmeBuild"
    assert owner.get("/providers/azure/setup").status_code == 404
    assert env.client().get("/providers/aws_app_runner/setup").status_code == 401


def test_connection_validation_errors(owner: ApiClient) -> None:
    bad = {
        **AWS,
        "default_region": "mars-1",
        "aws": {"account_id": "12", "role_arn": "arn:aws:iam::210987654321:role/X"},
    }
    errors = field_errors(owner.post("/cloud-connections", json=bad).json())
    assert set(errors) == {"default_region", "aws.account_id", "aws.role_arn"}
    mismatch = {**AWS, "aws": {"account_id": "123456789012", "role_arn": "arn:aws:iam::210987654321:role/X"}}
    assert field_errors(owner.post("/cloud-connections", json=mismatch).json()) == {
        "aws.role_arn": "The role must belong to the AWS account entered above."
    }
    assert "aws" in field_errors(owner.post("/cloud-connections", json={**AWS, "aws": None}).json())
    gcp_bad = {**GCP, "gcp": {"project_id": "X", "workload_identity_provider": "x", "service_account_email": "x"}}
    assert set(field_errors(owner.post("/cloud-connections", json=gcp_bad).json())) == {
        "gcp.project_id",
        "gcp.workload_identity_provider",
        "gcp.service_account_email",
    }


def test_validation_runs_in_the_worker(env: Env, owner: ApiClient) -> None:
    conn = owner.post("/cloud-connections", json=AWS).json()
    started = owner.post(f"/cloud-connections/{conn['id']}/test")
    assert started.status_code == 202
    assert started.json()["status"] == "validating"
    check(started.json(), CONNECTION)
    env.run_worker()
    done = owner.get(f"/cloud-connections/{conn['id']}").json()
    check(done, CONNECTION)
    assert done["status"] == "connected"
    assert done["last_validated_at"] is not None
    assert done["validation"]["stage"] is None
    assert all(c["status"] == "passed" for c in done["validation"]["checks"])


def test_failed_validation_reports_checks(env: Env, owner: ApiClient) -> None:
    env.aws.validation_status = "insufficient_permissions"
    conn = owner.post("/cloud-connections", json=AWS).json()
    owner.post(f"/cloud-connections/{conn['id']}/test")
    env.run_worker()
    done = owner.get(f"/cloud-connections/{conn['id']}").json()
    assert done["status"] == "insufficient_permissions"
    assert done["validation"]["error_code"] == "insufficient_permissions"
    assert done["validation"]["checks"][1]["status"] == "failed"
    actions = {e["action"]: e["outcome"] for e in owner.get("/audit-events").json()["items"]}
    assert actions["provider_connection.validated"] == "failure"


def test_platform_not_configured_is_reported_honestly(env: Env, owner: ApiClient) -> None:
    def explode(conn, progress):
        raise ProviderError(
            "platform_not_configured", "CloudBridge has no AWS identity configured.", connection_status="error"
        )

    env.aws.validate_connection = explode  # type: ignore[method-assign]
    conn = owner.post("/cloud-connections", json=AWS).json()
    owner.post(f"/cloud-connections/{conn['id']}/test")
    env.run_worker()
    done = owner.get(f"/cloud-connections/{conn['id']}").json()
    assert done["status"] == "error"
    assert done["validation"]["error_code"] == "platform_not_configured"


def test_delete_connection_in_use_is_refused(env: Env, owner: ApiClient) -> None:
    conn_id = env.add_connection()
    assert owner.post("/apps", json=image_app(conn_id)).status_code == 201
    refused = owner.delete(f"/cloud-connections/{conn_id}")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "connection_in_use"

    free = owner.post("/cloud-connections", json=AWS).json()
    assert owner.delete(f"/cloud-connections/{free['id']}").status_code == 204
    assert owner.get(f"/cloud-connections/{free['id']}").status_code == 404


def test_connections_are_tenant_isolated(env: Env, owner: ApiClient) -> None:
    other_org = env.add_org("Other")
    foreign = env.add_connection(org_id=other_org)
    assert owner.get(f"/cloud-connections/{foreign}").status_code == 404
    assert owner.post(f"/cloud-connections/{foreign}/test").status_code == 404
    assert foreign not in [c["id"] for c in owner.get("/cloud-connections").json()]


def test_developer_cannot_manage_connections(env: Env) -> None:
    env.add_user("dev@example.com", "developer")
    dev = env.client()
    dev.login("dev@example.com")
    denied = dev.post("/cloud-connections", json=AWS)
    assert denied.status_code == 403
    assert dev.get("/cloud-connections").status_code == 200
