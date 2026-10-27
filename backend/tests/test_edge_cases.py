"""Failure paths and races: errors while restoring or verifying, late cancellations, health checks
of deployed (not edited) settings, proxy addresses and malformed registry responses."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import update
from starlette.requests import Request

from app import clock
from app.api.deps import client_ip
from app.models import Application, Deployment
from app.providers.base import ProviderError
from app.providers.images import OciResolver, parse_image
from tests.conftest import ApiClient, Env, make_settings
from tests.test_deployments import create_app, deploy, run_until_done, stage_statuses


def run_until_status(env: Env, owner: ApiClient, dep_id: str, status: str, limit: int = 15) -> dict[str, Any]:
    for _ in range(limit):
        env.run_worker()
        current = dict(owner.get(f"/deployments/{dep_id}").json())
        if current["status"] == status:
            return current
    raise AssertionError(f"deployment never reached {status}: {current}")


def failing_health_checks(env: Env) -> None:
    env.settings.verification_timeout_seconds = 0
    env.worker._flow._http_client_factory = lambda: httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    )


def test_permanent_error_while_restoring_ends_in_failed_rollback(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    run_until_done(env, owner, deploy(owner, app["id"])["id"])
    failing_health_checks(env)
    dep = deploy(owner, app["id"])
    run_until_status(env, owner, dep["id"], "rolling_back")
    env.aws.fail_next = [ProviderError("invalid_request", "AWS rejected the request.")]
    done = run_until_done(env, owner, dep["id"])
    assert done["status"] == "failed_rollback"
    assert done["failure"]["code"] == "rollback_failed"
    assert done["failure"]["message"].endswith("AWS rejected the request.")
    served = owner.get(f"/apps/{app['id']}").json()
    # The unverified version is still serving, so the application must not claim to be running fine.
    assert served["status"] == "failed" and served["health"] == "unhealthy"


def test_permanent_error_during_verification_restores_previous_version(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    run_until_done(env, owner, deploy(owner, app["id"])["id"])
    dep = deploy(owner, app["id"])
    run_until_status(env, owner, dep["id"], "verifying")
    env.aws.fail_next = [ProviderError("invalid_request", "AWS rejected the request.")]
    done = run_until_done(env, owner, dep["id"])
    assert done["status"] == "rolled_back"
    assert done["failure"]["code"] == "verification_failed"
    assert "AWS rejected the request." in done["failure"]["message"]
    assert "previous version r1 was restored" in done["failure"]["message"]
    assert stage_statuses(done)["verification"] == "failed"
    served = owner.get(f"/apps/{app['id']}").json()
    assert served["current_version"] == "r1" and served["status"] == "running"


def test_internal_errors_while_restoring_end_in_failed_rollback(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    run_until_done(env, owner, deploy(owner, app["id"])["id"])
    failing_health_checks(env)
    dep = deploy(owner, app["id"])
    run_until_status(env, owner, dep["id"], "rolling_back")
    with env.database.session() as db:
        row = db.get(Deployment, dep["id"])
        assert row is not None
        env.worker._flow._fail(db, row, db.get(Application, app["id"]), "internal_error", "Unexpected error.")
        db.commit()
    done = owner.get(f"/deployments/{dep['id']}").json()
    assert done["status"] == "failed_rollback" and done["failure"]["code"] == "rollback_failed"


def test_cancellation_that_races_into_provisioning_cancels_before_the_rollout(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    dep = deploy(owner, app["id"])
    run_until_status(env, owner, dep["id"], "deploying")
    assert not any(name == "start_rollout" for name, _ in env.aws.calls)
    with env.database.session() as db:  # the API accepted the cancellation just before the status changed
        db.execute(update(Deployment).values(cancel_requested_at=clock.utcnow()))
        db.commit()
    done = run_until_done(env, owner, dep["id"])
    assert done["status"] == "cancelled"
    assert not any(name == "start_rollout" for name, _ in env.aws.calls)
    assert owner.get(f"/apps/{app['id']}").json()["status"] == "draft"


def test_cancellation_after_the_rollout_started_lets_it_finish(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    env.aws.rollout_polls_until_done = 3
    dep = deploy(owner, app["id"])
    run_until_status(env, owner, dep["id"], "deploying")
    env.run_worker()  # starts the rollout
    assert any(name == "start_rollout" for name, _ in env.aws.calls)
    with env.database.session() as db:
        db.execute(update(Deployment).values(cancel_requested_at=clock.utcnow()))
        db.commit()
    done = run_until_done(env, owner, dep["id"])
    assert done["status"] == "succeeded"
    messages = [e["message"] for e in owner.get(f"/deployments/{dep['id']}/events").json()]
    assert messages.count("Cancellation arrived after provisioning started, so the rollout continues.") == 1


def test_cancel_endpoint_refuses_a_deployment_that_finished_meanwhile(
    env: Env, owner: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(env, owner)
    done = run_until_done(env, owner, deploy(owner, app["id"])["id"])
    monkeypatch.setattr("app.routers.deployments.is_cancellable", lambda dep: True)  # stale read
    response = owner.post(f"/deployments/{done['id']}/cancel")
    assert response.status_code == 409 and response.json()["error"]["code"] == "not_cancellable"
    events = [e["message"] for e in owner.get(f"/deployments/{done['id']}/events").json()]
    assert not any(m.startswith("Cancellation requested") for m in events)


def test_health_checks_use_the_deployed_settings_not_unsaved_edits(env: Env, owner: ApiClient) -> None:
    app = create_app(env, owner)
    run_until_done(env, owner, deploy(owner, app["id"])["id"])
    config = owner.get(f"/apps/{app['id']}/spec").json()
    fields = ("port", "cpu", "memory", "min_instances", "max_instances", "concurrency", "health_check_path")
    body = {key: config[key] for key in fields}
    body.update(version=config["version"], exposure="private", environment=config["environment"])
    body["secret_refs"] = config["secret_refs"]
    assert owner.put(f"/apps/{app['id']}/spec", json=body).status_code == 200  # edited, not deployed
    env.worker._http_client_factory = lambda: httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    )
    with env.database.session() as db:
        db.execute(update(Application).values(health_checked_at=None))
        db.commit()
    env.run_worker()
    # The deployed service is still public, so it is still checked over HTTP.
    assert owner.get(f"/apps/{app['id']}").json()["health"] == "unhealthy"


def _request(tmp_path: Path, forwarded: str, trust: bool) -> Request:
    settings = make_settings(tmp_path, trust_proxy_headers=trust)
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", forwarded.encode())],
        "client": ("10.0.0.1", 1234),
        "app": SimpleNamespace(state=SimpleNamespace(settings=settings)),
    }
    return Request(scope)


def test_client_address_comes_from_the_trusted_proxy(tmp_path: Path) -> None:
    # The client can put anything at the start of X-Forwarded-For; the proxy appends the real address.
    assert client_ip(_request(tmp_path, "1.2.3.4, 203.0.113.9", trust=True)) == "203.0.113.9"
    assert client_ip(_request(tmp_path, "1.2.3.4", trust=False)) == "10.0.0.1"
    assert client_ip(_request(tmp_path, " , ", trust=True)) == "10.0.0.1"


def test_registry_token_that_is_not_json_is_a_retryable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.docker.io":
            return httpx.Response(200, text="<html>maintenance</html>")
        return httpx.Response(
            401, headers={"www-authenticate": 'Bearer realm="https://auth.docker.io/token",service="registry"'}
        )

    resolver = OciResolver(
        lambda host: host == "auth.docker.io", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ProviderError) as raised:
        resolver.resolve(parse_image("docker.io/library/nginx:1.27"), "registry-1.docker.io", "library/nginx")
    assert raised.value.code == "provider_unavailable" and raised.value.retryable
