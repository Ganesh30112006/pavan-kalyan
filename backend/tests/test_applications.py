from __future__ import annotations

from app.providers.base import ProviderError
from tests.conftest import ApiClient, Env, image_app, repo_source
from tests.contract import (
    APP_SUMMARY,
    APPLICATION,
    CONFIGURATION,
    LOG_PAGE,
    VALIDATION_RESULT,
    check,
    field_errors,
    page,
)


def test_create_and_read_application(env: Env, owner: ApiClient) -> None:
    conn = env.add_connection()
    response = owner.post("/apps", json=image_app(conn), headers={"Idempotency-Key": "create-1"})
    assert response.status_code == 201, response.text
    app = response.json()
    check(app, APPLICATION)
    assert app["status"] == "draft" and app["health"] == "unknown"
    assert app["provider"] == "aws_app_runner"
    assert app["created_by"] == "Ada Owner"
    assert app["latest_deployment"] is None

    again = owner.post("/apps", json=image_app(conn), headers={"Idempotency-Key": "create-1"})
    assert again.status_code == 201 and again.json()["id"] == app["id"]

    assert owner.get(f"/apps/{app['id']}").json() == app
    config = owner.get(f"/apps/{app['id']}/spec").json()
    check(config, CONFIGURATION)
    assert config["version"] == 1 and config["effective"] is None
    assert config["secret_refs"][0]["name"] == "DATABASE_URL"


def test_duplicate_name_is_a_conflict(env: Env, owner: ApiClient) -> None:
    conn = env.add_connection()
    owner.post("/apps", json=image_app(conn))
    response = owner.post("/apps", json=image_app(conn))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_name"
    assert field_errors(response.json()) == {"name": "This name is already in use."}


def test_field_errors_use_frontend_paths(env: Env, owner: ApiClient) -> None:
    conn = env.add_connection()
    body = image_app(
        conn,
        name="Bad Name",
        source={**image_app(conn)["source"], "image": "https://example.com/x"},
        environment=[{"name": "1BAD", "value": "x"}, {"name": "OK", "value": "y"}],
        secret_refs=[
            {"name": "OK", "reference": "arn:aws:secretsmanager:eu-west-1:123456789012:secret:a"},
            {"name": "TOKEN", "reference": "plain-text"},
        ],
        health_check_path="healthz",
        min_instances=5,
        max_instances=2,
    )
    errors = field_errors(owner.post("/apps", json=body).json())
    assert set(errors) == {
        "name",
        "source.image",
        "environment.0.name",
        "secret_refs.0.name",
        "secret_refs.1.reference",
        "health_check_path",
        "max_instances",
    }
    assert errors["secret_refs.0.name"] == "Secret 1: OK is already used by another variable or secret."


def test_types_are_strict(env: Env, owner: ApiClient) -> None:
    conn = env.add_connection()
    response = owner.post("/apps", json=image_app(conn, port="8080", min_instances=True))
    assert response.status_code == 422
    errors = field_errors(response.json())
    assert errors == {"port": "Must be a whole number.", "min_instances": "Must be a whole number."}
    assert field_errors(owner.post("/apps", json=image_app(conn, port=70000)).json()) == {
        "port": "Container port must be between 1 and 65535."
    }


def test_provider_capabilities_are_enforced(env: Env, owner: ApiClient) -> None:
    conn = env.add_connection()
    body = image_app(
        conn,
        application_type="worker",
        cpu="1",
        memory="8Gi",
        region="mars-1",
        max_instances=30,
        concurrency=500,
        source={**image_app(conn)["source"], "image": "docker.io/library/nginx:1.27"},
        secret_refs=[{"name": "DB", "reference": "projects/acme/secrets/db"}],
    )
    errors = field_errors(owner.post("/apps", json=body).json())
    assert errors["application_type"] == "AWS App Runner does not run background worker applications."
    assert errors["region"] == "AWS App Runner is not available in mars-1."
    assert errors["memory"] == "AWS App Runner supports 2Gi, 3Gi, 4Gi of memory with 1 vCPU."
    assert errors["max_instances"] == "AWS App Runner allows at most 25 instances."
    assert errors["concurrency"] == "AWS App Runner allows at most 200 concurrent requests per instance."
    assert "source.image" in errors
    assert errors["secret_refs.0.reference"].startswith("Secret 1: this reference points to Google Secret Manager")


def test_cloud_run_rules(env: Env, owner: ApiClient) -> None:
    conn = env.add_connection("gcp_cloud_run")
    good = image_app(
        conn,
        region="europe-west1",
        min_instances=0,
        source={**image_app(conn)["source"], "image": "europe-west1-docker.pkg.dev/acme/apps/orders:1.0"},
        secret_refs=[{"name": "DB", "reference": "projects/acme/secrets/db/versions/latest"}],
    )
    created = owner.post("/apps", json=good)
    assert created.status_code == 201, created.text
    bad = {**good, "name": "orders-two", "cpu": "1", "memory": "16Gi", "environment": [{"name": "PORT", "value": "1"}]}
    errors = field_errors(owner.post("/apps", json=bad).json())
    assert errors["cpu"] == "Google Cloud Run needs at least 4 vCPU for 16Gi of memory."
    assert errors["environment.0.name"] == "Variable 1: PORT is reserved by Google Cloud Run."


def test_repository_source_validation(env: Env, owner: ApiClient) -> None:
    conn = env.add_connection()
    ok = owner.post("/apps", json=image_app(conn, source=repo_source()))
    assert ok.status_code == 201
    app = ok.json()
    assert app["source"]["image"] is None and app["source"]["git_ref"] == "main"
    bad = image_app(
        conn,
        name="other-api",
        source=repo_source(
            repository_url="http://user:pw@10.0.0.1/x",
            git_ref="-x",
            dockerfile_path="../Dockerfile",
            build_context="/abs",
        ),
    )
    errors = field_errors(owner.post("/apps", json=bad).json())
    assert set(errors) == {"source.repository_url", "source.git_ref", "source.dockerfile_path", "source.build_context"}
    commit = image_app(conn, name="third-api", source=repo_source(ref_type="commit", git_ref="main"))
    assert field_errors(owner.post("/apps", json=commit).json()) == {
        "source.git_ref": "Enter a commit SHA of 7 to 40 hexadecimal characters."
    }


def test_connection_must_be_connected_and_owned(env: Env, owner: ApiClient) -> None:
    pending = env.add_connection(status="pending_validation")
    assert "connection_id" in field_errors(owner.post("/apps", json=image_app(pending)).json())
    foreign = env.add_connection(org_id=env.add_org("Other"))
    assert (
        field_errors(owner.post("/apps", json=image_app(foreign)).json())["connection_id"] == "Select a cloud account."
    )


def test_list_filters_sort_and_pagination(env: Env, owner: ApiClient) -> None:
    aws = env.add_connection()
    gcp = env.add_connection("gcp_cloud_run")
    for name in ("alpha-api", "bravo-api", "charlie-api"):
        assert owner.post("/apps", json=image_app(aws, name=name, description=f"{name} service")).status_code == 201
    gcp_body = image_app(
        gcp,
        name="delta-web",
        region="europe-west1",
        secret_refs=[],
        source={**image_app(gcp)["source"], "image": "europe-west1-docker.pkg.dev/acme/apps/web:1"},
    )
    assert owner.post("/apps", json=gcp_body).status_code == 201

    listed = owner.get("/apps", params={"page": 1, "page_size": 2}).json()
    check(listed, page(APP_SUMMARY))
    assert listed["total"] == 4 and len(listed["items"]) == 2
    assert listed["items"][0]["name"] == "delta-web"  # newest first

    by_name = owner.get("/apps", params={"sort": "name"}).json()["items"]
    assert [a["name"] for a in by_name] == ["alpha-api", "bravo-api", "charlie-api", "delta-web"]
    desc = owner.get("/apps", params={"sort": "-name"}).json()["items"]
    assert desc[0]["name"] == "delta-web"
    assert owner.get("/apps", params={"provider": "gcp_cloud_run"}).json()["total"] == 1
    assert owner.get("/apps", params={"search": "BRAVO"}).json()["items"][0]["name"] == "bravo-api"
    assert owner.get("/apps", params={"search": "%"}).json()["total"] == 0
    assert owner.get("/apps", params={"health": "unhealthy,degraded"}).json()["total"] == 0
    assert owner.get("/apps", params={"health": "unknown", "status": "draft"}).json()["total"] == 4
    assert owner.get("/apps", params={"sort": "health"}).status_code == 200
    assert owner.get("/apps", params={"page": 3, "page_size": 2}).json()["items"] == []
    assert owner.get("/apps", params={"sort": "secret"}).status_code == 422
    assert owner.get("/apps", params={"health": "sick"}).status_code == 422
    assert owner.get("/apps", params={"page_size": 500}).status_code == 422


def test_update_configuration_with_optimistic_locking(env: Env, owner: ApiClient) -> None:
    app = owner.post("/apps", json=image_app(env.add_connection())).json()
    config = owner.get(f"/apps/{app['id']}/spec").json()
    update = {k: config[k] for k in ("port", "cpu", "memory", "min_instances", "max_instances", "concurrency",
                                     "exposure", "health_check_path", "environment", "secret_refs")}  # fmt: skip
    update.update(version=1, max_instances=5, concurrency=50, environment=[{"name": "LOG_LEVEL", "value": "debug"}])
    saved = owner.put(f"/apps/{app['id']}/spec", json=update)
    assert saved.status_code == 200, saved.text
    check(saved.json(), CONFIGURATION)
    assert saved.json()["version"] == 2 and saved.json()["max_instances"] == 5
    stale = owner.put(f"/apps/{app['id']}/spec", json=update)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"
    invalid = owner.put(f"/apps/{app['id']}/spec", json={**update, "version": 2, "cpu": "3"})
    assert field_errors(invalid.json())["cpu"].startswith("AWS App Runner does not offer 3 vCPU")


def test_validate_endpoint(env: Env, owner: ApiClient) -> None:
    app = owner.post("/apps", json=image_app(env.add_connection())).json()
    result = owner.post(f"/apps/{app['id']}/validate")
    assert result.status_code == 200
    body = result.json()
    check(body, VALIDATION_RESULT)
    assert body["valid"] is True
    assert body["adjustments"][0]["field"] == "min_instances"
    assert ("resolve_image", "123456789012.dkr.ecr.eu-west-1.amazonaws.com/orders:1.4.0") in env.aws.calls

    env.aws.image_error = ProviderError("image_not_found", "The image or tag was not found in Amazon ECR.")
    missing = owner.post(f"/apps/{app['id']}/validate").json()
    assert missing["valid"] is False
    assert missing["issues"] == [
        {"field": "source.image", "severity": "error", "message": "The image or tag was not found in Amazon ECR."}
    ]
    env.aws.image_error = ProviderError("provider_unavailable", "AWS could not be reached.", retryable=True)
    unreachable = owner.post(f"/apps/{app['id']}/validate").json()
    assert unreachable["valid"] is True and unreachable["issues"][0]["severity"] == "warning"


def test_logs_before_deployment_are_empty(env: Env, owner: ApiClient) -> None:
    app = owner.post("/apps", json=image_app(env.add_connection())).json()
    logs = owner.get(f"/apps/{app['id']}/logs")
    assert logs.status_code == 200
    check(logs.json(), LOG_PAGE)
    assert logs.json() == {"entries": [], "truncated": False}


def test_delete_draft_application(env: Env, owner: ApiClient) -> None:
    app = owner.post("/apps", json=image_app(env.add_connection())).json()
    assert owner.delete(f"/apps/{app['id']}").status_code == 204
    assert owner.get(f"/apps/{app['id']}").status_code == 404
    assert owner.get("/apps").json()["total"] == 0
    # The name can be reused once the application is gone.
    assert owner.post("/apps", json=image_app(app["connection_id"])).status_code == 201


def test_permissions_and_denied_audit(env: Env, owner: ApiClient) -> None:
    app = owner.post("/apps", json=image_app(env.add_connection())).json()
    env.add_user("vic@example.com", "viewer", "Vic Viewer")
    viewer = env.client()
    viewer.login("vic@example.com")
    assert viewer.get(f"/apps/{app['id']}").status_code == 200
    assert viewer.delete(f"/apps/{app['id']}").status_code == 403
    assert viewer.post("/apps", json=image_app(app["connection_id"], name="new-app")).status_code == 403
    assert viewer.get("/audit-events").status_code == 403
    denied = [e for e in owner.get("/audit-events", params={"outcome": "denied"}).json()["items"]]
    assert {e["action"] for e in denied} >= {"application.deleted", "application.created", "audit.read"}
    assert all(e["actor"]["display_name"] == "Vic Viewer" for e in denied)


def test_tenant_isolation(env: Env, owner: ApiClient) -> None:
    other_org = env.add_org("Other")
    env.add_user("eve@other.example", "owner", org_id=other_org)
    eve = env.client()
    eve.login("eve@other.example")
    app = owner.post("/apps", json=image_app(env.add_connection())).json()
    assert eve.get(f"/apps/{app['id']}").status_code == 404
    assert eve.get(f"/apps/{app['id']}/spec").status_code == 404
    assert eve.delete(f"/apps/{app['id']}").status_code == 404
    assert eve.get("/apps").json()["total"] == 0


def test_plain_variables_cannot_carry_credentials(env: Env, owner: ApiClient) -> None:
    # CloudBridge never stores long-lived credentials: they must come from the cloud's secret manager.
    conn = env.add_connection()
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    for name, value in (
        ("AWS_SECRET_ACCESS_KEY", "anything"),
        ("DEPLOY_KEY", pem),
        ("UPSTREAM_ID", "AKIAIOSFODNN7EXAMPLE"),
        ("GITHUB", "ghp_" + "a" * 36),
    ):
        response = owner.post("/apps", json=image_app(conn, name="leaky", environment=[{"name": name, "value": value}]))
        assert response.status_code == 422, (name, response.text)
        fields = response.json()["error"]["fields"]
        assert fields[0]["field"] == "environment.0.value" and "secret reference" in fields[0]["message"]
        assert value not in response.text  # the value is never echoed back

    # Ordinary settings whose names merely mention tokens or passwords are still fine.
    ok = owner.post(
        "/apps",
        json=image_app(
            conn,
            name="fine",
            environment=[
                {"name": "TOKEN_TTL_SECONDS", "value": "3600"},
                {"name": "PASSWORD_MIN_LENGTH", "value": "12"},
            ],
        ),
    )
    assert ok.status_code == 201, ok.text
