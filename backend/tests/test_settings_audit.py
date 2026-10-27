from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.db import Base, Database
from app.main import create_app
from app.providers.base import ProviderRegistry
from tests.conftest import ApiClient, Env, image_app, make_settings
from tests.contract import AUDIT_EVENT, check, field_errors, page
from tests.fakes import FakeAdapter


def test_profile_update(owner: ApiClient) -> None:
    response = owner.patch(
        "/me", json={"display_name": "Ada L.", "preferences": {"timezone": "Europe/London", "time_format": "12h"}}
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": response.json()["id"],
        "email": "ada@example.com",
        "display_name": "Ada L.",
        "role": "owner",
        "preferences": {"timezone": "Europe/London", "time_format": "12h"},
    }
    assert owner.get("/auth/session").json()["user"]["display_name"] == "Ada L."
    bad = owner.patch("/me", json={"display_name": " ", "preferences": {"timezone": "Mars/Base", "time_format": "24h"}})
    assert set(field_errors(bad.json())) == {"display_name", "preferences.timezone"}


def test_organization_and_members(env: Env, owner: ApiClient) -> None:
    org = owner.get("/organization").json()
    assert set(org) == {"id", "name", "created_at"} and org["name"] == "Acme"
    assert owner.patch("/organization", json={"name": "Acme Corp"}).json()["name"] == "Acme Corp"
    assert owner.get("/auth/session").json()["organization"]["name"] == "Acme Corp"
    env.add_user("dev@example.com", "developer", "Dev")
    members = owner.get("/organization/members").json()
    assert [m["email"] for m in members] == ["ada@example.com", "dev@example.com"]
    assert set(members[0]) == {"id", "display_name", "email", "role", "joined_at"}
    dev = env.client()
    dev.login("dev@example.com")
    assert dev.patch("/organization", json={"name": "Hijack"}).status_code == 403


def test_notification_preferences_need_smtp(owner: ApiClient) -> None:
    assert owner.get("/auth/session").json()["features"]["notification_preferences"] is False
    assert owner.get("/me/notification-preferences").status_code == 404


def test_notification_preferences_when_configured(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, smtp_host="smtp.example.com", smtp_from="cloudbridge@example.com")
    database = Database(settings.database_url)
    Base.metadata.create_all(database.engine)
    registry = ProviderRegistry(
        {"aws_app_runner": FakeAdapter("aws_app_runner"), "gcp_cloud_run": FakeAdapter("gcp_cloud_run")}
    )
    app = create_app(settings, database=database, provider_registry=registry)
    with TestClient(app) as test_client:
        from app import clock
        from app.models import Organization, User
        from app.security import hash_password
        from tests.conftest import PASSWORD

        with database.session() as db:
            org = Organization(name="Acme", created_at=clock.utcnow())
            db.add(org)
            db.flush()
            db.add(User(organization_id=org.id, email="a@example.com", display_name="A", role="viewer",
                        password_hash=hash_password(PASSWORD), time_format="24h", created_at=clock.utcnow()))  # fmt: skip
            db.commit()
        client = ApiClient(test_client)
        assert client.login("a@example.com").json()["features"]["notification_preferences"] is True
        prefs = client.get("/me/notification-preferences").json()
        assert prefs["email_enabled"] is True
        assert set(prefs["events"]) == {
            "deployment_succeeded",
            "deployment_failed",
            "rollback_completed",
            "provider_connection_failed",
            "application_deleted",
            "application_unhealthy",
            "approval_requested",
        }
        updated = client.put(
            "/me/notification-preferences",
            json={"email_enabled": False, "events": {**prefs["events"], "deployment_succeeded": True}},
        ).json()
        assert updated["email_enabled"] is False and updated["events"]["deployment_succeeded"] is True
        assert (
            client.put(
                "/me/notification-preferences", json={"email_enabled": True, "events": {"spam": True}}
            ).status_code
            == 422
        )
    database.dispose()


def test_audit_log_filters(env: Env, owner: ApiClient) -> None:
    conn = env.add_connection()
    owner.post("/apps", json=image_app(conn))
    owner.post("/apps", json=image_app(conn, name="billing-api"))
    listed = owner.get("/audit-events").json()
    check(listed, page(AUDIT_EVENT))
    actions = [e["action"] for e in listed["items"]]
    assert actions[:2] == ["application.created", "application.created"]
    assert "auth.login" in actions
    assert owner.get("/audit-events", params={"action": "application.created"}).json()["total"] == 2
    assert owner.get("/audit-events", params={"search": "billing"}).json()["total"] == 1
    assert owner.get("/audit-events", params={"actor": "ada@"}).json()["total"] == listed["total"]
    assert owner.get("/audit-events", params={"actor": "nobody"}).json()["total"] == 0
    assert (
        owner.get("/audit-events", params={"resource_type": "application", "outcome": "success"}).json()["total"] == 2
    )
    assert owner.get("/audit-events", params={"from": "2999-01-01T00:00:00.000Z"}).json()["total"] == 0
    assert owner.get("/audit-events", params={"to": "2999-01-01T00:00:00.000Z"}).json()["total"] == listed["total"]
    assert owner.get("/audit-events", params={"from": "yesterday"}).status_code == 422
    event = listed["items"][0]
    assert event["request_id"] and event["actor"]["email"] == "ada@example.com"
