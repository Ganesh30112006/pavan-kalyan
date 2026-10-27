from __future__ import annotations

from typing import Any

import httpx

from app.providers.base import ProviderError
from tests.conftest import ApiClient, Env, image_app, repo_source
from tests.contract import CONFIGURATION, DEPLOYMENT, DEPLOYMENT_SUMMARY, EVENT, LOG_PAGE, check, page
from tests.fakes import BUILT_DIGEST, DIGEST


def create_app(env: Env, owner: ApiClient, **overrides: Any) -> dict[str, Any]:
    response = owner.post("/apps", json=image_app(env.add_connection(), **overrides))
    assert response.status_code == 201, response.text
    return dict(response.json())


def deploy(owner: ApiClient, app_id: str, key: str | None = None) -> dict[str, Any]:
    headers = {"Idempotency-Key": key} if key else {}
    response = owner.post(f"/apps/{app_id}/deployments", headers=headers)
    assert response.status_code == 202, response.text
    return dict(response.json())


def stage_statuses(dep: dict[str, Any]) -> dict[str, str]:
    return {s["name"]: s["status"] for s in dep["stages"]}


def test_image_deployment_end_to_end(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    dep = deploy(owner, app["id"], key="dep-1")
    check(dep, DEPLOYMENT)
    assert dep["status"] == "queued" and dep["kind"] == "deploy" and dep["version"] == "r1"
    assert dep["allowed_actions"] == {"rollback": False, "cancel": True, "approve": False}
    assert dep["source_ref"] == "orders:1.4.0"
    assert owner.post(f"/apps/{app['id']}/deployments", headers={"Idempotency-Key": "dep-1"}).json()["id"] == dep["id"]
    busy = owner.post(f"/apps/{app['id']}/deployments")
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "deployment_in_progress"
    assert owner.get(f"/apps/{app['id']}").json()["status"] == "deploying"

    seen = []
    for _ in range(12):
        env.run_worker()
        current = owner.get(f"/deployments/{dep['id']}").json()
        check(current, DEPLOYMENT)
        seen.append(current["status"])
        if current["finished_at"]:
            break
    assert current["status"] == "succeeded", current
    assert {"validating", "publishing", "deploying"} <= set(seen)
    assert stage_statuses(current) == {
        "queued": "succeeded",
        "validation": "succeeded",
        "build": "skipped",
        "publishing": "succeeded",
        "provisioning": "succeeded",
        "verification": "succeeded",
    }
    assert current["image_digest"] == DIGEST
    assert current["service_url"] == "https://orders.example.awsapprunner.com"
    assert current["allowed_actions"] == {"rollback": False, "cancel": False, "approve": False}
    assert env.aws.deployed_images == [f"123456789012.dkr.ecr.eu-west-1.amazonaws.com/orders@{DIGEST}"]

    served = owner.get(f"/apps/{app['id']}").json()
    assert served["status"] == "running" and served["health"] == "healthy"
    assert served["current_version"] == "r1" and served["image_digest"] == DIGEST
    assert served["latest_deployment"]["status"] == "succeeded"

    config = owner.get(f"/apps/{app['id']}/spec").json()
    check(config, CONFIGURATION)
    assert config["effective"]["min_instances"] == 1 and config["effective"]["concurrency"] == 100
    assert config["adjustments"] == [
        {
            "field": "min_instances",
            "requested": "0",
            "effective": "1",
            "reason": "AWS App Runner keeps at least 1 instance running.",
        }
    ]

    events = owner.get(f"/deployments/{dep['id']}/events").json()
    for event in events:
        check(event, EVENT)
    assert events[0]["message"] == "Deployment of version r1 queued."
    logs = owner.get(f"/deployments/{dep['id']}/logs").json()
    check(logs, LOG_PAGE)
    assert logs["entries"] and logs["truncated"] is False

    runtime = owner.get(f"/apps/{app['id']}/logs").json()
    check(runtime, LOG_PAGE)
    assert runtime["entries"][0]["message"] == "GET /healthz 200"


def test_deployment_lists(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    dep = deploy(owner, app["id"])
    listed = owner.get("/deployments").json()
    check(listed, page(DEPLOYMENT_SUMMARY))
    assert listed["items"][0]["id"] == dep["id"]
    assert owner.get("/deployments", params={"status": "QUEUED,validating"}).json()["total"] == 1
    assert owner.get("/deployments", params={"status": "failed,failed_rollback"}).json()["total"] == 0
    assert owner.get("/deployments", params={"search": "orders"}).json()["total"] == 1
    assert owner.get("/deployments", params={"search": dep["id"][:8]}).json()["total"] == 1
    assert owner.get("/deployments", params={"application_id": app["id"], "sort": "-status"}).json()["total"] == 1
    assert owner.get("/deployments", params={"provider": "gcp_cloud_run"}).json()["total"] == 0
    assert owner.get("/deployments", params={"status": "exploded"}).status_code == 422
    per_app = owner.get(f"/apps/{app['id']}/deployments", params={"page": 1, "page_size": 5}).json()
    check(per_app, page(DEPLOYMENT_SUMMARY))
    assert per_app["total"] == 1


def run_until_done(env: Env, owner: ApiClient, dep_id: str, limit: int = 15) -> dict[str, Any]:
    for _ in range(limit):
        env.run_worker()
        current = dict(owner.get(f"/deployments/{dep_id}").json())
        if current["finished_at"]:
            return current
    raise AssertionError(f"deployment did not finish: {current}")


def test_rollback_restores_an_earlier_version(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    first = run_until_done(env, owner, deploy(owner, app["id"])["id"])
    assert first["allowed_actions"]["rollback"] is False  # it is serving
    second = run_until_done(env, owner, deploy(owner, app["id"])["id"])
    assert second["version"] == "r2" and second["status"] == "succeeded"
    first = owner.get(f"/deployments/{first['id']}").json()
    assert first["allowed_actions"] == {"rollback": True, "cancel": False, "approve": False}

    rollback = owner.post(f"/deployments/{first['id']}/rollback", headers={"Idempotency-Key": "rb-1"})
    assert rollback.status_code == 202
    rb = rollback.json()
    check(rb, DEPLOYMENT)
    assert rb["kind"] == "rollback" and rb["status"] == "rolling_back" and rb["rollback_of"] == first["id"]
    assert rb["version"] == "r1"
    assert (
        owner.post(f"/deployments/{first['id']}/rollback", headers={"Idempotency-Key": "rb-1"}).json()["id"] == rb["id"]
    )
    done = run_until_done(env, owner, rb["id"])
    assert done["status"] == "rolled_back"
    assert stage_statuses(done)["build"] == "skipped" and stage_statuses(done)["publishing"] == "skipped"
    assert owner.get(f"/apps/{app['id']}").json()["current_version"] == "r1"
    refused = owner.post(f"/deployments/{rb['id']}/rollback")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "rollback_not_permitted"
    assert owner.post(f"/deployments/{second['id']}/rollback").status_code == 202


def test_repository_build_and_cancel(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner, source=repo_source())
    env.aws.build_polls_until_done = 2
    dep = deploy(owner, app["id"])
    done = run_until_done(env, owner, dep["id"])
    assert done["status"] == "succeeded"
    assert done["image_digest"] == BUILT_DIGEST
    assert done["source_ref"] == "main@0123456789ab"
    assert stage_statuses(done)["build"] == "succeeded"
    logs = [e["message"] for e in owner.get(f"/deployments/{dep['id']}/logs").json()["entries"]]
    assert "Successfully built" in logs

    env.aws.build_polls_until_done = 100
    second = deploy(owner, app["id"])
    env.run_worker(3)
    assert owner.get(f"/deployments/{second['id']}").json()["status"] == "building"
    cancel = owner.post(f"/deployments/{second['id']}/cancel")
    assert cancel.status_code == 202
    assert cancel.json()["allowed_actions"]["cancel"] is False
    finished = run_until_done(env, owner, second["id"])
    assert finished["status"] == "cancelled"
    assert any(name == "cancel_build" for name, _ in env.aws.calls)
    assert owner.get(f"/apps/{app['id']}").json()["current_version"] == "r1"
    assert owner.post(f"/deployments/{second['id']}/cancel").status_code == 409


def test_build_failure(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner, source=repo_source())
    env.aws.build_result = "failed"
    done = run_until_done(env, owner, deploy(owner, app["id"])["id"])
    assert done["status"] == "failed"
    assert done["failure"] == {"code": "build_failed", "message": "Build phase BUILD failed: exit status 1"}
    assert stage_statuses(done)["build"] == "failed" and stage_statuses(done)["provisioning"] == "skipped"
    assert owner.get(f"/apps/{app['id']}").json()["status"] == "failed"
    assert owner.get("/deployments", params={"status": "failed,failed_rollback"}).json()["total"] == 1


def test_verification_failure_restores_previous_version(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    run_until_done(env, owner, deploy(owner, app["id"])["id"])
    env.settings.verification_timeout_seconds = 0
    env.worker._flow._http_client_factory = lambda: httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    )
    done = run_until_done(env, owner, deploy(owner, app["id"])["id"])
    assert done["status"] == "rolled_back" and done["kind"] == "deploy"
    assert done["failure"]["code"] == "verification_failed"
    assert "previous version r1 was restored" in done["failure"]["message"]
    assert stage_statuses(done)["verification"] == "failed"
    served = owner.get(f"/apps/{app['id']}").json()
    assert served["current_version"] == "r1" and served["status"] == "running"
    assert len(env.aws.deployed_images) == 3


def test_verification_failure_without_previous_version(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    env.aws.service_running = False
    done = run_until_done(env, owner, deploy(owner, app["id"])["id"])
    assert done["status"] == "failed"
    assert done["failure"]["code"] == "verification_failed"
    served = owner.get(f"/apps/{app['id']}").json()
    assert served["status"] == "failed" and served["health"] == "unhealthy"


def test_rollout_failure_keeps_previous_version(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    run_until_done(env, owner, deploy(owner, app["id"])["id"])
    env.aws.rollout_result = "failed"
    done = run_until_done(env, owner, deploy(owner, app["id"])["id"])
    assert done["status"] == "failed" and done["failure"]["code"] == "provisioning_failed"
    served = owner.get(f"/apps/{app['id']}").json()
    assert served["status"] == "running" and served["current_version"] == "r1"


def test_transient_provider_errors_are_retried(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    env.aws.fail_next = [ProviderError("provider_unavailable", "AWS is busy or unavailable.", retryable=True)] * 2
    done = run_until_done(env, owner, deploy(owner, app["id"])["id"])
    assert done["status"] == "succeeded"
    warnings = [e for e in owner.get(f"/deployments/{done['id']}/events").json() if e["level"] == "warning"]
    assert warnings[0]["message"] == "AWS is busy or unavailable."


def test_permanent_provider_error_fails_with_its_message(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    env.aws.image_error = ProviderError("image_not_found", "The image or tag was not found in Amazon ECR.")
    done = run_until_done(env, owner, deploy(owner, app["id"])["id"])
    assert done["status"] == "failed"
    assert done["failure"] == {"code": "image_unavailable", "message": "The image or tag was not found in Amazon ECR."}
    assert stage_statuses(done)["publishing"] == "failed"


def test_disconnected_connection_fails_validation(env: Env, owner: ApiClient) -> None:
    from sqlalchemy import update

    from app.models import ProviderConnection

    app = create_app(env, owner)
    with env.database.session() as db:
        db.execute(update(ProviderConnection).values(status="invalid_credentials"))
        db.commit()
    done = run_until_done(env, owner, deploy(owner, app["id"])["id"])
    assert done["failure"]["code"] == "connection_unavailable"


def test_deleting_a_deployed_application(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    run_until_done(env, owner, deploy(owner, app["id"])["id"])
    response = owner.delete(f"/apps/{app['id']}")
    assert response.status_code == 202
    assert owner.get(f"/apps/{app['id']}").json()["status"] == "deleting"
    assert owner.post(f"/apps/{app['id']}/deployments").status_code == 409
    env.run_worker(3)
    assert env.aws.deleted is True
    assert owner.get(f"/apps/{app['id']}").status_code == 404
    history = owner.get("/deployments").json()
    assert history["total"] == 1  # history survives the application


def test_viewer_cannot_deploy_or_roll_back(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    first = run_until_done(env, owner, deploy(owner, app["id"])["id"])
    env.add_user("vic@example.com", "viewer")
    viewer = env.client()
    viewer.login("vic@example.com")
    assert viewer.post(f"/apps/{app['id']}/deployments").status_code == 403
    assert viewer.get(f"/deployments/{first['id']}").json()["allowed_actions"] == {
        "rollback": False,
        "cancel": False,
        "approve": False,
    }
    assert viewer.post(f"/deployments/{first['id']}/rollback").status_code == 403


def test_health_monitor_updates_health(env: Env, owner: ApiClient) -> None:
    from sqlalchemy import update

    from app.models import Application

    app = create_app(env, owner)
    run_until_done(env, owner, deploy(owner, app["id"])["id"])
    env.aws.service_running = False
    with env.database.session() as db:
        db.execute(update(Application).values(health_checked_at=None))
        db.commit()
    env.run_worker()
    assert owner.get(f"/apps/{app['id']}").json()["health"] == "unhealthy"
    assert owner.get("/apps", params={"health": "unhealthy"}).json()["total"] == 1
