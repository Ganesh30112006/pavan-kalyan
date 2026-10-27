from __future__ import annotations

from tests.conftest import Env
from tests.contract import ERROR, SESSION, check


def test_session_requires_sign_in_and_issues_csrf_cookie(env: Env) -> None:
    client = env.client()
    response = client.get("/auth/session")
    assert response.status_code == 401
    check(response.json(), ERROR)
    assert response.json()["error"]["code"] == "unauthenticated"
    assert response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    assert "cb_csrf" in client.client.cookies


def test_request_id_is_echoed_when_valid(env: Env) -> None:
    client = env.client()
    response = client.get("/auth/session", headers={"X-Request-ID": "req-12345678"})
    assert response.headers["x-request-id"] == "req-12345678"
    assert response.json()["error"]["request_id"] == "req-12345678"
    bad = client.get("/auth/session", headers={"X-Request-ID": "<script>"})
    assert bad.headers["x-request-id"] != "<script>"


def test_login_returns_session_and_sets_http_only_cookie(env: Env) -> None:
    env.add_user("ada@example.com", "owner", "Ada Owner")
    client = env.client()
    response = client.login("ADA@example.com")
    body = response.json()
    check(body, SESSION)
    assert body["user"]["email"] == "ada@example.com"
    assert body["organization"]["name"] == "Acme"
    assert set(body["permissions"]) == {
        "applications:create",
        "applications:update",
        "applications:delete",
        "deployments:create",
        "deployments:cancel",
        "deployments:rollback",
        "deployments:approve",
        "providers:manage",
        "audit:read",
        "organization:manage",
    }
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("cb_session="))
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie
    csrf_cookie = next(c for c in cookies if c.startswith("cb_csrf="))
    assert "HttpOnly" not in csrf_cookie
    assert client.get("/auth/session").status_code == 200


def test_wrong_password_and_unknown_email_get_the_same_answer(env: Env) -> None:
    env.add_user("ada@example.com", "owner")
    client = env.client()
    client.get("/auth/session")
    wrong = client.post("/auth/login", json={"email": "ada@example.com", "password": "nope-nope-nope"})
    unknown = client.post("/auth/login", json={"email": "who@example.com", "password": "nope-nope-nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert wrong.json()["error"]["code"] == "invalid_credentials"


def test_login_is_rate_limited(env: Env) -> None:
    env.add_user("ada@example.com", "owner")
    client = env.client()
    client.get("/auth/session")
    for _ in range(5):
        assert client.post("/auth/login", json={"email": "ada@example.com", "password": "x" * 12}).status_code == 401
    limited = client.post("/auth/login", json={"email": "ada@example.com", "password": "x" * 12})
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0
    assert limited.json()["error"]["code"] == "rate_limited"


def test_unsafe_requests_need_the_csrf_header(env: Env) -> None:
    env.add_user("ada@example.com", "owner")
    client = env.client()
    client.get("/auth/session")
    response = client.client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": "x" * 12})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"
    forged = client.client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "x" * 12},
        headers={"X-CSRF-Token": "not-the-cookie"},
    )
    assert forged.status_code == 403


def test_logout_ends_the_session(owner) -> None:
    response = owner.post("/auth/logout")
    assert response.status_code == 204
    assert owner.get("/auth/session").status_code == 401
    assert owner.get("/apps").status_code == 401


def test_session_rotation_on_login(env: Env) -> None:
    env.add_user("ada@example.com", "owner")
    client = env.client()
    client.login("ada@example.com")
    first = client.client.cookies.get("cb_session")
    client.login("ada@example.com")
    second = client.client.cookies.get("cb_session")
    assert first != second
    stale = env.client()
    stale.client.cookies.set("cb_session", first or "")
    assert stale.get("/auth/session").status_code == 401


def test_viewer_has_no_write_permissions(env: Env) -> None:
    env.add_user("vic@example.com", "viewer")
    client = env.client()
    assert client.login("vic@example.com").json()["permissions"] == []


def test_malformed_json_and_missing_fields(env: Env) -> None:
    client = env.client()
    client.get("/auth/session")
    bad = client.post("/auth/login", content=b"{nope", headers={"Content-Type": "application/json"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_json"
    missing = client.post("/auth/login", json={"email": "a@example.com"})
    assert missing.status_code == 422
    assert missing.json()["error"]["fields"] == [{"field": "password", "message": "This field is required."}]


def test_unknown_route_uses_the_error_envelope(env: Env) -> None:
    response = env.client().get("/nope")
    assert response.status_code == 404
    check(response.json(), ERROR)


def test_method_not_allowed_lists_every_method_of_the_path(owner) -> None:
    # Each method is its own route; the Allow header must still name all of them.
    response = owner.put("/apps", json={})
    assert response.status_code == 405
    check(response.json(), ERROR)
    assert response.headers["allow"] == "GET, POST"
    assert owner.patch("/cloud-connections/abc", json={}).headers["allow"] == "DELETE, GET"


def test_api_responses_forbid_loading_anything(env: Env) -> None:
    csp = env.client().get("/auth/session").headers["content-security-policy"]
    assert csp == "default-src 'none'; frame-ancestors 'none'"


def test_schema_publishes_error_responses_and_paging_limits(env: Env) -> None:
    schema = env.client().get("/openapi.json").json()
    list_apps = schema["paths"]["/api/v1/apps"]["get"]
    assert {"401", "403", "404", "422", "429"} <= set(list_apps["responses"])
    assert list_apps["responses"]["404"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorEnvelope")
    params = {p["name"]: p["schema"] for p in list_apps["parameters"]}
    assert params["page"]["minimum"] == 1 and params["page_size"]["maximum"] == 100
