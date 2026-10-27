"""Platform features through the API: API tokens, GitHub webhooks, previews, approvals, promotion,
pausing and schedules, metrics, cost estimates, custom domains, alert channels and cloudbridge.yaml."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.models import Application
from app.worker import Worker
from app.worker.notify import Notifier
from tests.conftest import ApiClient, Env, http_ok_factory, image_app, repo_source
from tests.contract import (
    ALERT_CHANNEL,
    API_TOKEN,
    AUTOMATION,
    COST,
    DEPLOYMENT,
    DOMAIN,
    METRICS,
    SCHEDULE,
    check,
    field_errors,
)
from tests.test_deployments import run_until_done

SHA1 = "1" * 40
SHA2 = "2" * 40


def repo_app(env: Env, owner: ApiClient, **overrides: Any) -> dict[str, Any]:
    body = image_app(env.add_connection(), name="orders-api", source=repo_source(), **overrides)
    response = owner.post("/apps", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def deployed_app(env: Env, owner: ApiClient, **overrides: Any) -> dict[str, Any]:
    app = owner.post("/apps", json=image_app(env.add_connection(), **overrides)).json()
    dep = owner.post(f"/apps/{app['id']}/deployments").json()
    assert run_until_done(env, owner, dep["id"])["status"] == "succeeded"
    return dict(owner.get(f"/apps/{app['id']}").json())


def automation(owner: ApiClient, app_id: str, **changes: Any) -> Any:
    body = {
        "environment": "production",
        "promote_to_id": None,
        "auto_deploy": False,
        "auto_deploy_branch": None,
        "previews_enabled": False,
        "config_from_repo": False,
        "requires_approval": False,
    }
    body.update(changes)
    return owner.put(f"/apps/{app_id}/automation", json=body)


def github(env: Env, app_id: str, secret: str, event: str, payload: dict[str, Any], delivery: str) -> Any:
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    client = TestClient(env.test_client.app, base_url="http://testserver")
    return client.post(
        f"/api/v1/hooks/github/{app_id}",
        content=raw,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )


REPO = {"clone_url": "https://github.com/acme/orders.git", "html_url": "https://github.com/acme/orders"}


def push(sha: str, branch: str = "main") -> dict[str, Any]:
    return {"ref": f"refs/heads/{branch}", "after": sha, "repository": REPO, "pusher": {"name": "ada"}}


def pull_request(action: str, number: int, sha: str, fork: bool = False) -> dict[str, Any]:
    head_repo = {"full_name": "someone/orders" if fork else "acme/orders"}
    return {
        "action": action,
        "number": number,
        "repository": REPO,
        "pull_request": {
            "number": number,
            "title": "Add checkout",
            "head": {"sha": sha, "repo": head_repo},
            "base": {"ref": "main", "repo": {"full_name": "acme/orders"}},
        },
    }


# ---------- API tokens ----------


def test_api_tokens_authenticate_without_cookies_or_csrf(env: Env, owner: ApiClient) -> None:
    created = owner.post("/me/api-tokens", json={"name": "CI", "expires_in_days": 30})
    assert created.status_code == 201
    token = created.json()["token"]
    assert token.startswith("cbt_")
    check({k: v for k, v in created.json().items() if k != "token"}, API_TOKEN)
    listed = owner.get("/me/api-tokens").json()
    assert [t["name"] for t in listed] == ["CI"] and "token" not in listed[0]

    app = owner.post("/apps", json=image_app(env.add_connection())).json()
    bare = TestClient(env.test_client.app, base_url="http://testserver")
    headers = {"Authorization": f"Bearer {token}"}
    assert bare.get("/api/v1/apps", headers=headers).json()["total"] == 1
    dep = bare.post(f"/api/v1/apps/{app['id']}/deployments", headers=headers)
    assert dep.status_code == 202, dep.text
    assert dep.json()["trigger"] == "api"
    # A token cannot mint tokens.
    assert bare.post("/api/v1/me/api-tokens", headers=headers, json={"name": "x"}).status_code == 403

    assert owner.delete(f"/me/api-tokens/{listed[0]['id']}").status_code == 204
    assert bare.get("/api/v1/apps", headers=headers).status_code == 401
    assert bare.get("/api/v1/apps", headers={"Authorization": "Bearer cbt_unknown"}).status_code == 401


def test_cookie_requests_still_need_csrf(env: Env, owner: ApiClient) -> None:
    response = owner.client.post("/api/v1/me/api-tokens", json={"name": "x"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


# ---------- GitHub webhooks ----------


def test_push_deploys_the_pushed_commit_once(env: Env, owner: ApiClient) -> None:
    app = repo_app(env, owner)
    updated = automation(owner, app["id"], auto_deploy=True)
    assert updated.status_code == 200, updated.text
    check(updated.json(), AUTOMATION)
    assert updated.json()["webhook"]["secret_configured"] is True
    secret = owner.post(f"/apps/{app['id']}/webhook-secret").json()["secret"]

    response = github(env, app["id"], secret, "push", push(SHA1), "d-1")
    assert response.status_code == 202, response.text
    deps = owner.get(f"/apps/{app['id']}/deployments").json()["items"]
    assert len(deps) == 1
    dep = owner.get(f"/deployments/{deps[0]['id']}").json()
    check(dep, DEPLOYMENT)
    assert dep["trigger"] == "push" and dep["source_ref"] == f"main@{SHA1[:12]}"
    assert dep["initiated_by"] == "Push by ada"

    assert github(env, app["id"], secret, "push", push(SHA1), "d-1").json()["outcome"] == "duplicate delivery"
    assert github(env, app["id"], secret, "push", push(SHA2, "feature"), "d-2").json()["outcome"].startswith("ignored")
    bad = github(env, app["id"], "wrong-secret", "push", push(SHA2), "d-3")
    assert bad.status_code == 401
    other = dict(push(SHA2), repository={"clone_url": "https://github.com/evil/orders.git"})
    assert "another repository" in github(env, app["id"], secret, "push", other, "d-4").json()["outcome"]

    # A push while a deployment runs is deferred, then deployed when the application is free.
    deferred = github(env, app["id"], secret, "push", push(SHA2), "d-5")
    assert deferred.json()["outcome"].startswith("deferred")
    done = run_until_done(env, owner, deps[0]["id"])
    assert done["source_ref"] == f"main@{SHA1[:12]}"  # the build's commit does not replace the push label
    env.run_worker(1)
    latest = owner.get(f"/apps/{app['id']}/deployments").json()["items"][0]
    assert latest["source_ref"] == f"main@{SHA2[:12]}" and latest["trigger"] == "push"


def test_pull_requests_get_preview_applications(env: Env, owner: ApiClient) -> None:
    app = repo_app(env, owner)
    automation(owner, app["id"], previews_enabled=True)
    secret = owner.post(f"/apps/{app['id']}/webhook-secret").json()["secret"]

    opened = github(env, app["id"], secret, "pull_request", pull_request("opened", 7, SHA1), "p-1")
    assert opened.status_code == 202, opened.text
    previews = owner.get(f"/apps/{app['id']}/previews").json()
    assert [p["name"] for p in previews] == ["orders-api-pr-7"]
    preview = owner.get(f"/apps/{previews[0]['id']}").json()
    assert preview["environment"] == "preview" and preview["preview_of_id"] == app["id"]
    dep = owner.get(f"/apps/{preview['id']}/deployments").json()["items"][0]
    assert run_until_done(env, owner, dep["id"])["status"] == "succeeded"

    forked = github(env, app["id"], secret, "pull_request", pull_request("opened", 8, SHA2, fork=True), "p-2")
    assert forked.json()["outcome"] == "ignored: pull request from a fork"

    synced = github(env, app["id"], secret, "pull_request", pull_request("synchronize", 7, SHA2), "p-3")
    assert synced.status_code == 202
    second = owner.get(f"/apps/{preview['id']}/deployments").json()["items"][0]
    assert second["source_ref"] == f"PR #7@{SHA2[:12]}"
    run_until_done(env, owner, second["id"])

    closed = github(env, app["id"], secret, "pull_request", pull_request("closed", 7, SHA2), "p-4")
    assert closed.json()["outcome"] in ("preview deleting", "preview deleted")
    env.run_worker(3)
    assert owner.get(f"/apps/{preview['id']}").status_code == 404


# ---------- approvals ----------


def test_production_deployments_wait_for_an_approver(env: Env, owner: ApiClient) -> None:
    app = owner.post("/apps", json=image_app(env.add_connection())).json()
    env.add_user("dev@example.com", "developer")
    dev = env.client()
    dev.login("dev@example.com")
    assert automation(dev, app["id"], requires_approval=True).status_code == 403
    assert automation(owner, app["id"], requires_approval=True).status_code == 200

    dep = dev.post(f"/apps/{app['id']}/deployments").json()
    assert dep["status"] == "awaiting_approval"
    assert dep["allowed_actions"]["approve"] is False
    assert owner.get(f"/deployments/{dep['id']}").json()["allowed_actions"]["approve"] is True
    env.run_worker(2)
    assert owner.get(f"/deployments/{dep['id']}").json()["status"] == "awaiting_approval"
    assert dev.post(f"/deployments/{dep['id']}/approve").status_code == 403

    approved = owner.post(f"/deployments/{dep['id']}/approve")
    assert approved.status_code == 202
    assert approved.json()["approved_by"] == "Ada Owner"
    assert run_until_done(env, owner, dep["id"])["status"] == "succeeded"
    assert owner.post(f"/deployments/{dep['id']}/approve").status_code == 409

    second = dev.post(f"/apps/{app['id']}/deployments").json()
    rejected = owner.post(f"/deployments/{second['id']}/reject").json()
    assert rejected["status"] == "cancelled" and rejected["failure"]["code"] == "rejected"
    third = dev.post(f"/apps/{app['id']}/deployments").json()
    cancelled = dev.post(f"/deployments/{third['id']}/cancel")
    assert cancelled.status_code == 202 and cancelled.json()["status"] == "cancelled"
    assert owner.get(f"/apps/{app['id']}").json()["status"] == "running"
    # Rollbacks never wait.
    first = owner.get(f"/deployments/{dep['id']}").json()
    assert first["allowed_actions"]["rollback"] is False  # it is the current version


# ---------- promotion ----------


def test_promotion_deploys_the_same_image_digest(env: Env, owner: ApiClient) -> None:
    staging = deployed_app(env, owner, name="orders-staging")
    production = owner.post("/apps", json=image_app(env.add_connection(), name="orders-prod")).json()
    gcp_app = owner.post(
        "/apps",
        json=image_app(
            env.add_connection("gcp_cloud_run"),
            name="orders-gcp",
            region="europe-west1",
            secret_refs=[],
            source={**image_app("x")["source"], "image": "europe-west1-docker.pkg.dev/acme/apps/web:1"},
        ),
    ).json()
    mixed = automation(owner, staging["id"], environment="staging", promote_to_id=gcp_app["id"])
    assert "promote_to_id" in field_errors(mixed.json())
    assert automation(owner, staging["id"], environment="staging", promote_to_id=production["id"]).status_code == 200

    promoted = owner.post(f"/apps/{staging['id']}/promote", headers={"Idempotency-Key": "promote-1"})
    assert promoted.status_code == 202, promoted.text
    dep = promoted.json()
    assert dep["application_id"] == production["id"] and dep["trigger"] == "promotion"
    assert dep["source_ref"].startswith("orders-staging r1")
    done = run_until_done(env, owner, dep["id"])
    assert done["status"] == "succeeded"
    assert done["image_digest"] == staging["image_digest"]
    assert owner.post(f"/apps/{production['id']}/promote").status_code == 409

    # A retried request replays the original deployment (no second one), even after the promotion target
    # has changed.
    other = owner.post("/apps", json=image_app(env.add_connection(), name="orders-prod-2")).json()
    assert automation(owner, staging["id"], environment="staging", promote_to_id=other["id"]).status_code == 200
    replay = owner.post(f"/apps/{staging['id']}/promote", headers={"Idempotency-Key": "promote-1"})
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == dep["id"] and replay.json()["application_name"] == "orders-prod"


# ---------- pausing and sleep schedules ----------


def test_pause_resume_and_sleep_schedule(env: Env, owner: ApiClient) -> None:
    app = deployed_app(env, owner)
    paused = owner.post(f"/apps/{app['id']}/pause")
    assert paused.status_code == 200
    check(paused.json(), SCHEDULE)
    assert env.aws.paused is True
    assert owner.get(f"/apps/{app['id']}").json()["status"] == "stopped"
    assert owner.post(f"/apps/{app['id']}/deployments").json()["error"]["code"] == "application_paused"
    assert owner.post(f"/apps/{app['id']}/resume").json()["paused"] is False
    assert env.aws.paused is False

    bad = owner.put(
        f"/apps/{app['id']}/schedule", json={"timezone": "Mars/Base", "days": [], "sleep_at": "25:00", "wake_at": "x"}
    )
    assert set(field_errors(bad.json())) == {"timezone", "days", "sleep_at", "wake_at"}

    now = clock.utcnow()
    sleep_at = (now - dt.timedelta(minutes=5)).strftime("%H:%M")
    wake_at = (now + dt.timedelta(hours=2)).strftime("%H:%M")
    days = list(range(7))
    schedule = owner.put(
        f"/apps/{app['id']}/schedule", json={"timezone": "UTC", "days": days, "sleep_at": sleep_at, "wake_at": wake_at}
    )
    assert schedule.status_code == 200 and schedule.json()["asleep_now"] is True
    env.worker._last_schedules = None
    env.run_worker(1)
    state = owner.get(f"/apps/{app['id']}/schedule").json()
    assert state["paused"] is True and state["paused_by_schedule"] is True and env.aws.paused is True

    # A window that starts on neither today nor yesterday cannot cover now.
    other_day = [(now.weekday() + 3) % 7]
    owner.put(
        f"/apps/{app['id']}/schedule",
        json={"timezone": "UTC", "days": other_day, "sleep_at": sleep_at, "wake_at": wake_at},
    )
    env.worker._last_schedules = None
    env.run_worker(1)
    assert owner.get(f"/apps/{app['id']}/schedule").json()["paused"] is False
    assert owner.get(f"/apps/{app['id']}").json()["status"] == "running"
    assert owner.delete(f"/apps/{app['id']}/schedule").json()["schedule"] is None


# ---------- metrics, cost ----------


def test_metrics_and_cost(env: Env, owner: ApiClient) -> None:
    draft = owner.post("/apps", json=image_app(env.add_connection(), name="draft-app")).json()
    empty = owner.get(f"/apps/{draft['id']}/metrics").json()
    check(empty, METRICS)
    assert empty["unavailable_reason"] == "The application has not been deployed yet."

    app = deployed_app(env, owner)
    metrics = owner.get(f"/apps/{app['id']}/metrics", params={"range": "6h"}).json()
    check(metrics, METRICS)
    assert metrics["step_seconds"] == 300 and metrics["series"][0]["name"] == "requests"
    assert len(metrics["series"][0]["points"]) == 3
    assert owner.get(f"/apps/{app['id']}/metrics", params={"range": "1y"}).status_code == 422

    cost = owner.get(f"/apps/{app['id']}/cost-estimate").json()
    check(cost, COST)
    assert 0 < cost["monthly_low"] < cost["monthly_high"]
    preview = owner.post(
        "/cost-estimate",
        json={"provider": "kubernetes", "cpu": "0.5", "memory": "1Gi", "min_instances": 1, "max_instances": 3},
    ).json()
    assert preview["monthly_low"] is None and "nodes" in preview["assumptions"][0]
    wrong = owner.post(
        "/cost-estimate",
        json={"provider": "aws_app_runner", "cpu": "3", "memory": "1Gi", "min_instances": 2, "max_instances": 1},
    )
    assert set(field_errors(wrong.json())) == {"cpu", "max_instances"}


# ---------- custom domains ----------


def test_custom_domains(env: Env, owner: ApiClient) -> None:
    draft = owner.post("/apps", json=image_app(env.add_connection(), name="draft-app")).json()
    assert owner.post(f"/apps/{draft['id']}/domains", json={"domain": "api.example.com"}).status_code == 409
    app = deployed_app(env, owner)
    for bad in ("not a domain", "x.awsapprunner.com", "localhost"):
        assert owner.post(f"/apps/{app['id']}/domains", json={"domain": bad}).status_code == 422
    added = owner.post(f"/apps/{app['id']}/domains", json={"domain": "API.Example.com."})
    assert added.status_code == 201, added.text
    domain = added.json()
    check(domain, DOMAIN)
    assert domain["domain"] == "api.example.com" and domain["status"] == "pending_dns"
    assert domain["records"][0]["value"] == "target.example.net"
    assert owner.post(f"/apps/{app['id']}/domains", json={"domain": "api.example.com"}).status_code == 409

    env.aws.domains["api.example.com"] = "active"
    checked = owner.post(f"/apps/{app['id']}/domains/{domain['id']}/check").json()
    assert checked["status"] == "active"
    assert owner.delete(f"/apps/{app['id']}/domains/{domain['id']}").status_code == 202
    env.run_worker(1)
    assert owner.get(f"/apps/{app['id']}/domains").json() == []
    assert "api.example.com" not in env.aws.domains


# ---------- alert channels ----------


class Receiver:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def factory(self) -> httpx.Client:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(204)

        return httpx.Client(transport=httpx.MockTransport(handle))


def test_alert_channels_deliver_signed_alerts(env: Env, owner: ApiClient) -> None:
    receiver = Receiver()
    env.settings.allow_private_alert_urls = True
    env.test_client.app.state.notifier = Notifier(env.settings, env.database, receiver.factory)  # type: ignore[attr-defined]
    env.worker = Worker(
        env.settings,
        env.database,
        env.worker._registry,
        resolver=lambda host: ["140.82.112.3"],
        http_client_factory=http_ok_factory,
        notifier=Notifier(env.settings, env.database, receiver.factory),
    )

    wrong = owner.post(
        "/alert-channels",
        json={"name": "Ops", "kind": "slack", "url": "https://evil.example/hook", "events": ["deployment_failed"]},
    )
    assert "url" in field_errors(wrong.json())
    created = owner.post(
        "/alert-channels",
        json={"name": "Ops", "kind": "webhook", "url": "http://127.0.0.1:9/hook", "events": ["deployment_failed"]},
    )
    assert created.status_code == 201, created.text
    secret = created.json()["signing_secret"]
    assert secret and created.json()["signed"] is True
    listed = owner.get("/alert-channels").json()
    check(listed[0], ALERT_CHANNEL)
    assert listed[0]["host"] == "127.0.0.1" and "url" not in listed[0]

    test = owner.post(f"/alert-channels/{listed[0]['id']}/test").json()
    assert test == {"delivered": True, "status": "Delivered (204)"}
    sent = receiver.requests[-1]
    expected = "sha256=" + hmac.new(secret.encode(), sent.content, hashlib.sha256).hexdigest()
    assert sent.headers["X-CloudBridge-Signature"] == expected
    assert json.loads(sent.content)["event"] == "test"

    env.aws.rollout_result = "failed"
    app = owner.post("/apps", json=image_app(env.add_connection())).json()
    dep = owner.post(f"/apps/{app['id']}/deployments").json()
    assert run_until_done(env, owner, dep["id"])["status"] == "failed"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(
        r.headers.get("X-CloudBridge-Event") == "deployment_failed" for r in receiver.requests
    ):
        time.sleep(0.05)
    alert = next(r for r in receiver.requests if r.headers.get("X-CloudBridge-Event") == "deployment_failed")
    body = json.loads(alert.content)
    assert body["application"] == {"id": app["id"], "name": "orders-api"}

    env.add_user("dev@example.com", "developer")
    dev = env.client()
    dev.login("dev@example.com")
    assert dev.post(f"/alert-channels/{listed[0]['id']}/test").status_code == 403
    assert owner.delete(f"/alert-channels/{listed[0]['id']}").status_code == 204


# ---------- cloudbridge.yaml ----------


def test_config_file_endpoint(env: Env, owner: ApiClient) -> None:
    app = owner.post("/apps", json=image_app(env.add_connection())).json()
    spec = owner.get(f"/apps/{app['id']}/spec").json()
    yaml_text = "port: 9090\ninstances: {min: 1, max: 5}\nenvironment:\n  LOG_LEVEL: debug\n"
    applied = owner.put(f"/apps/{app['id']}/config-file", json={"content": yaml_text, "version": spec["version"]})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["port"] == 9090 and body["max_instances"] == 5 and body["version"] == spec["version"] + 1
    assert body["environment"] == [{"name": "LOG_LEVEL", "value": "debug"}]
    assert body["secret_refs"] == spec["secret_refs"]
    stale = owner.put(f"/apps/{app['id']}/config-file", json={"content": "port: 1", "version": spec["version"]})
    assert stale.status_code == 409
    for text, field in (("port: [1", "config"), ("colour: blue", "config"), ("port: eighty", "port")):
        response = owner.put(f"/apps/{app['id']}/config-file", json={"content": text, "version": body["version"]})
        assert field in field_errors(response.json()), (text, response.text)
    rules = owner.put(f"/apps/{app['id']}/config-file", json={"content": "cpu: '3'", "version": body["version"]})
    assert "cpu" in field_errors(rules.json())


def test_deployments_read_cloudbridge_yaml_from_the_repository(env: Env, owner: ApiClient) -> None:
    app = repo_app(env, owner)
    automation(owner, app["id"], config_from_repo=True)
    files: dict[str, str] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://raw.githubusercontent.com/acme/orders/main/cloudbridge.yaml"):
            return httpx.Response(200, text=files["main"]) if "main" in files else httpx.Response(404)
        return httpx.Response(200, text="ok")

    env.worker = Worker(
        env.settings,
        env.database,
        env.worker._registry,
        resolver=lambda host: ["140.82.112.3"],
        http_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
    )
    first = owner.post(f"/apps/{app['id']}/deployments").json()
    assert run_until_done(env, owner, first["id"])["status"] == "succeeded"
    events = [e["message"] for e in owner.get(f"/deployments/{first['id']}/events").json()]
    assert any("No cloudbridge.yaml" in m for m in events)

    files["main"] = "memory: 4Gi\ninstances: {min: 2, max: 4}\n"
    second = owner.post(f"/apps/{app['id']}/deployments").json()
    assert run_until_done(env, owner, second["id"])["status"] == "succeeded"
    events = [e["message"] for e in owner.get(f"/deployments/{second['id']}/events").json()]
    assert any(m.startswith("Applied cloudbridge.yaml") for m in events)
    spec = owner.get(f"/apps/{app['id']}/spec").json()
    assert spec["memory"] == "4Gi" and spec["min_instances"] == 2 and spec["max_instances"] == 4

    files["main"] = "memory: 5Gi\n"
    third = owner.post(f"/apps/{app['id']}/deployments").json()
    failed = run_until_done(env, owner, third["id"])
    assert failed["status"] == "failed" and failed["failure"]["code"] == "invalid_configuration"


def test_automation_validation(env: Env, owner: ApiClient) -> None:
    image = owner.post("/apps", json=image_app(env.add_connection(), name="image-app")).json()
    assert "auto_deploy" in field_errors(automation(owner, image["id"], auto_deploy=True).json())
    app = repo_app(env, owner)
    assert "auto_deploy_branch" in field_errors(
        automation(owner, app["id"], auto_deploy=True, auto_deploy_branch="a..b").json()
    )
    assert "environment" in field_errors(automation(owner, app["id"], environment="preview").json())
    assert "promote_to_id" in field_errors(automation(owner, app["id"], promote_to_id=app["id"]).json())


@pytest.mark.parametrize("provider", ["aws_ecs_express", "kubernetes"])
def test_capabilities_for_new_providers(owner: ApiClient, provider: str) -> None:
    caps = owner.get(f"/providers/{provider}/capabilities").json()
    assert caps["provider"] == provider
    assert caps["features"]["repository_builds"] is (provider == "aws_ecs_express")


def test_new_connection_kinds(env: Env, owner: ApiClient) -> None:
    ecs = owner.post(
        "/cloud-connections",
        json={
            "name": "ECS",
            "provider": "aws_ecs_express",
            "default_region": "ap-south-1",
            "ecs": {"account_id": "123456789012", "role_arn": "arn:aws:iam::123456789012:role/CloudBridge"},
        },
    )
    assert ecs.status_code == 201, ecs.text
    assert ecs.json()["config"]["ecs_cluster"] == "default" and ecs.json()["setup"]["external_id"].startswith("cb-")

    pem = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIU\n-----END CERTIFICATE-----"
    cluster: dict[str, str] = {
        "api_server": "https://20.40.60.80:443",
        "ca_certificate": pem,
        "namespace": "apps",
        "auth": "aks",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "client_id": "33333333-3333-3333-3333-333333333333",
    }
    k8s = {"name": "AKS", "provider": "kubernetes", "default_region": "in-cluster", "kubernetes": cluster}
    created = owner.post("/cloud-connections", json=k8s)
    assert created.status_code == 201, created.text
    config = created.json()["config"]
    assert config["namespace"] == "apps" and config["kubernetes_auth"] == "aks"
    assert created.json()["setup"]["federation_audience"] == "api://AzureADTokenExchange"
    wrong = {
        "api_server": "https://10.0.0.1",
        "namespace": "kube-system",
        "ca_certificate": "-----BEGIN PRIVATE KEY-----",
        "auth": "oidc",
    }
    bad = dict(k8s, name="Bad", kubernetes={**cluster, **wrong})
    errors = field_errors(owner.post("/cloud-connections", json=bad).json())
    assert set(errors) >= {
        "kubernetes.api_server",
        "kubernetes.namespace",
        "kubernetes.ca_certificate",
        "kubernetes.audience",
    }


def test_preview_and_app_rows_are_consistent(env: Env, owner: ApiClient) -> None:
    app = repo_app(env, owner)
    with env.database.session() as db:
        row = db.get(Application, app["id"])
        assert row is not None and row.environment == "production" and row.webhook_secret is None
