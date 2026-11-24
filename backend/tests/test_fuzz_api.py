"""Fuzzing: every API route gets malformed, boundary and random input. Nothing may produce a 500, an unhandled
exception, or an error outside the standard envelope.

The seed is fixed so a failure reproduces; set CLOUDBRIDGE_FUZZ_ROUNDS to run more rounds locally."""

from __future__ import annotations

import os
import random
import re
from typing import Any

from tests.conftest import ApiClient, Env, image_app

ROUNDS = int(os.environ.get("CLOUDBRIDGE_FUZZ_ROUNDS", "6"))

STRINGS = [
    "",
    " ",
    "a",
    "x" * 5000,
    "../../etc/passwd",
    "%00",
    "\x00",
    "‮\u0000퟿",
    "😀" * 50,
    "<script>alert(1)</script>",
    "' OR 1=1 --",
    "${jndi:ldap://x}",
    "AKIAABCDEFGHIJKLMNOP",
    "-1",
    "1e309",
    "null",
    "https://169.254.169.254/latest/meta-data",
    "http://localhost:8000",
    "https://[::1",
    "https://example.com:99999/x",
    "http://[",
    "UTC",
    "Mars/Base",
    "sha256:" + "a" * 64,
]
NUMBERS: list[Any] = [0, -1, 1, 2**31, 2**63, -(2**63), 10**30, 0.5, -0.0, 1e308, float("inf")]


def _value(rng: random.Random, depth: int = 0) -> Any:
    kind = rng.randrange(8 if depth < 3 else 5)
    if kind == 0:
        return rng.choice(STRINGS)
    if kind == 1:
        number = rng.choice(NUMBERS)
        return None if number == float("inf") else number  # JSON has no infinity
    if kind == 2:
        return rng.choice([True, False])
    if kind == 3:
        return None
    if kind == 4:
        return "".join(chr(rng.randrange(1, 0x2FFF)) for _ in range(rng.randrange(0, 40)))
    if kind == 5:
        return [_value(rng, depth + 1) for _ in range(rng.randrange(0, 4))]
    return {
        rng.choice([*STRINGS[:8], "name", "id", "value"]): _value(rng, depth + 1) for _ in range(rng.randrange(0, 4))
    }


def _resolve(schema: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        schema = spec["components"]["schemas"][name]
    return schema


def _from_schema(schema: dict[str, Any], spec: dict[str, Any], rng: random.Random, depth: int = 0) -> Any:
    """Mostly well-typed values with hostile content, sometimes the wrong type entirely."""
    schema = _resolve(schema, spec)
    if rng.random() < 0.15 or depth > 4:
        return _value(rng, depth)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return _from_schema(rng.choice(schema[key]), spec, rng, depth + 1)
    if "enum" in schema:
        return rng.choice(schema["enum"] + [rng.choice(STRINGS)])
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {
            name: _from_schema(sub, spec, rng, depth + 1)
            for name, sub in props.items()
            if name in schema.get("required", []) or rng.random() < 0.7
        }
    if kind == "array":
        return [_from_schema(schema.get("items", {}), spec, rng, depth + 1) for _ in range(rng.randrange(0, 4))]
    if kind == "integer":
        return rng.choice([n for n in NUMBERS if isinstance(n, int)])
    if kind == "number":
        return rng.choice([n for n in NUMBERS if n != float("inf")])
    if kind == "boolean":
        return rng.choice([True, False])
    if kind == "string":
        return rng.choice(STRINGS)
    return _value(rng, depth)


def _check(response: Any, label: str) -> None:
    assert response.status_code < 500 or response.status_code == 503, (
        f"{label} -> {response.status_code}: {response.text[:300]}"
    )
    if response.status_code >= 400:
        body = response.json()
        assert set(body) == {"error"}, f"{label}: not the error envelope: {body}"
        assert {"code", "message", "request_id", "fields"} <= set(body["error"]), f"{label}: {body}"


def _fixtures(env: Env, owner: ApiClient) -> dict[str, str]:
    conn = env.add_connection()
    app = owner.post("/apps", json=image_app(conn)).json()
    dep = owner.post(f"/apps/{app['id']}/deployments").json()
    env.run_worker(12)
    env.settings.allow_private_alert_urls = True  # a local receiver address, so no DNS lookup is needed
    channel = owner.post(
        "/alert-channels",
        json={"name": "ops", "kind": "webhook", "url": "http://localhost:9/hook", "events": ["deployment_failed"]},
    )
    assert channel.status_code == 201, channel.text
    token = owner.post("/me/api-tokens", json={"name": "ci", "expires_in_days": 30})
    assert token.status_code == 201, token.text
    domain = owner.post(f"/apps/{app['id']}/domains", json={"domain": "www.example.com"})
    assert domain.status_code in (200, 201, 202), domain.text
    return {
        "application_id": app["id"],
        "deployment_id": dep["id"],
        "connection_id": conn,
        "channel_id": channel.json()["id"],
        "token_id": token.json()["id"],
        "domain_id": domain.json()["id"],
    }


PATH_VALUES = ["x", "0", "../apps", "%2e%2e", "a" * 300, "00000000-0000-0000-0000-000000000000", "😀", " "]


def test_every_route_survives_hostile_input(env: Env, owner: ApiClient) -> None:
    spec = owner.get("/openapi.json").json()
    known = _fixtures(env, owner)
    rng = random.Random(int(os.environ.get("CLOUDBRIDGE_FUZZ_SEED", "20261124")))
    routes = [(path, method, op) for path, item in spec["paths"].items() for method, op in item.items()]
    assert len(routes) > 40
    for round_no in range(ROUNDS):
        for path, method, op in routes:
            if path.endswith("/auth/logout") or path.endswith("/auth/login"):
                continue  # keep the owner's session (login is fuzzed in its own test)

            def fill(match: re.Match[str], round_no: int = round_no) -> str:
                name = match.group(1)
                if round_no % 4 != 3 and name in known:
                    return known[name]
                return rng.choice(PATH_VALUES)

            url = re.sub(r"\{(\w+)\}", fill, path).removeprefix("/api/v1")
            params = {
                p["name"]: str(_from_schema(p.get("schema", {}), spec, rng))
                for p in op.get("parameters", [])
                if p.get("in") == "query" and rng.random() < 0.8
            }
            body_schema = ((op.get("requestBody") or {}).get("content") or {}).get("application/json", {}).get("schema")
            kwargs: dict[str, Any] = {"params": params}
            if body_schema is not None:
                choice = rng.random()
                if choice < 0.7:
                    kwargs["json"] = _from_schema(body_schema, spec, rng)
                elif choice < 0.85:
                    kwargs["json"] = _value(rng)
                else:
                    kwargs["content"] = rng.choice([b"{", b"\xff\xfe", b"[]" * 1000, b'{"a":' * 200])
                    kwargs["headers"] = {"Content-Type": "application/json"}
            label = f"{method.upper()} {url} {kwargs}"[:400]
            _check(owner.request(method.upper(), url, **kwargs), label)
            if method != "get" and rng.random() < 0.2:
                env.run_worker(1)  # let queued work run against whatever was accepted


def test_login_survives_hostile_input(env: Env) -> None:
    client = env.client()
    client.get("/auth/session")
    rng = random.Random(7)
    for _ in range(60):
        body = rng.choice([{"email": _value(rng), "password": _value(rng)}, _value(rng)])
        response = client.post("/auth/login", json=body)
        assert response.status_code in (400, 401, 422, 429), response.text
        _check(response, f"login {body!r}"[:200])


SPEC_BODY = {
    "version": 1,
    "port": 8080,
    "cpu": "1",
    "memory": "2Gi",
    "min_instances": 1,
    "max_instances": 2,
    "concurrency": None,
    "exposure": "public",
    "health_check_path": "/healthz",
    "environment": [],
    "secret_refs": [],
}
VALID_BODIES: dict[str, Any] = {
    "PUT /apps/{application_id}/spec": SPEC_BODY,
    "PUT /apps/{application_id}/automation": {
        "environment": "production",
        "promote_to_id": None,
        "auto_deploy": False,
        "auto_deploy_branch": None,
        "previews_enabled": False,
        "config_from_repo": False,
        "requires_approval": False,
    },
    "PUT /apps/{application_id}/config-file": {"content": "port: 8080\n", "version": 1},
    "PUT /apps/{application_id}/schedule": {"timezone": "UTC", "days": [0], "sleep_at": "20:00", "wake_at": "08:00"},
    "POST /apps/{application_id}/domains": {"domain": "www.example.com"},
    "PUT /alert-channels/{channel_id}": {"name": "x", "kind": "webhook", "events": ["deployment_failed"]},
}


def test_no_route_reaches_another_organizations_resources(env: Env, owner: ApiClient) -> None:
    """A user of another organization calls every route with this organization's real IDs: nothing may succeed
    or change. (Routes without IDs act on the caller's own organization and are skipped.)"""
    spec = owner.get("/openapi.json").json()
    known = _fixtures(env, owner)
    other_org = env.add_org("Rival")
    env.add_user("eve@rival.example", "owner", "Eve", org_id=other_org)
    eve = env.client()
    eve.login("eve@rival.example")
    before = owner.get(f"/apps/{known['application_id']}").json()
    checked = 0
    for path, item in spec["paths"].items():
        names = re.findall(r"\{(\w+)\}", path)
        if not names or path.startswith("/api/v1/hooks/") or any(n not in known for n in names):
            continue
        url = re.sub(r"\{(\w+)\}", lambda m: known[m.group(1)], path).removeprefix("/api/v1")
        for method, op in item.items():
            body_schema = ((op.get("requestBody") or {}).get("content") or {}).get("application/json", {}).get("schema")
            kwargs: dict[str, Any] = {}
            if body_schema is not None:
                # A valid body, so only the ownership check can refuse the request (not body validation).
                kwargs["json"] = VALID_BODIES.get(f"{method.upper()} {path.removeprefix('/api/v1')}", {})
            response = eve.request(method.upper(), url, **kwargs)
            checked += 1
            assert response.status_code in (404, 403), (
                f"{method.upper()} {url} -> {response.status_code}: {response.text}"
            )
    assert checked > 30
    after = owner.get(f"/apps/{known['application_id']}").json()
    assert after == before


# Write routes every signed-in user may call for themselves, or that only read (dry runs and estimates).
SELF_SERVICE = {
    "PATCH /me",
    "PUT /me/notification-preferences",
    "POST /me/api-tokens",
    "DELETE /me/api-tokens/{token_id}",
    "POST /apps/{application_id}/validate",
    "POST /cost-estimate",
    # Refreshes a status the worker also refreshes on its own; the UI offers "Check now" to everyone.
    "POST /apps/{application_id}/domains/{domain_id}/check",
}


def test_a_viewer_can_change_nothing(env: Env, owner: ApiClient) -> None:
    """With valid bodies, every write route refuses a viewer with 403 (permission before state or validation of
    the resource), except the caller's own profile, tokens and read-only dry runs."""
    spec = owner.get("/openapi.json").json()
    known = _fixtures(env, owner)
    bodies = {
        **VALID_BODIES,
        "POST /apps": image_app(known["connection_id"], name="viewer-made"),
        "PATCH /organization": {"name": "Renamed"},
        "POST /alert-channels": {"name": "v", "kind": "webhook", "url": "http://localhost:9/v", "events": []},
        "POST /cloud-connections": {
            "name": "v",
            "provider": "aws_app_runner",
            "default_region": "eu-west-1",
            "aws": {"account_id": "123456789012", "role_arn": "arn:aws:iam::123456789012:role/CloudBridge"},
        },
    }
    env.add_user("vic@example.com", "viewer", "Vic")
    vic = env.client()
    vic.login("vic@example.com")
    refused = 0
    for path, item in spec["paths"].items():
        route = path.removeprefix("/api/v1")
        if route.startswith(("/hooks/", "/auth/")):
            continue
        url = re.sub(r"\{(\w+)\}", lambda m: known.get(m.group(1), "aws_app_runner"), route)
        for method, op in item.items():
            key = f"{method.upper()} {route}"
            if method == "get" or key in SELF_SERVICE:
                continue
            has_body = bool(((op.get("requestBody") or {}).get("content") or {}).get("application/json"))
            response = vic.request(method.upper(), url, **({"json": bodies.get(key, {})} if has_body else {}))
            assert response.status_code == 403, f"{key} -> {response.status_code}: {response.text[:200]}"
            refused += 1
    assert refused > 25
