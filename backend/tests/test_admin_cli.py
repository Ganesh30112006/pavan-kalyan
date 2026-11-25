"""The administration CLI (python -m app.cli) and the standalone worker entry point."""

from __future__ import annotations

import signal
from pathlib import Path

import pytest

from app import cli
from tests.conftest import PASSWORD, ApiClient, Env


@pytest.fixture
def cli_env(env: Env, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setenv("CLOUDBRIDGE_DATABASE_URL", env.settings.database_url)
    monkeypatch.setenv("CLOUDBRIDGE_ENVIRONMENT", "test")
    monkeypatch.setenv("CLOUDBRIDGE_COOKIE_SECURE", "false")
    return env


def test_set_password_signs_out_existing_sessions(
    cli_env: Env, owner: ApiClient, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert owner.get("/auth/session").status_code == 200
    monkeypatch.setenv("CLOUDBRIDGE_BOOTSTRAP_PASSWORD", "a brand new password 42")
    cli.main(["set-password", "--email", "ADA@example.com"])
    assert "1 session(s) signed out" in capsys.readouterr().out
    assert owner.get("/auth/session").status_code == 401
    fresh = cli_env.client()
    fresh.get("/auth/session")
    assert fresh.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD}).status_code == 401
    fresh.login("ada@example.com", "a brand new password 42")


def test_create_user_validates_input(cli_env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDBRIDGE_BOOTSTRAP_PASSWORD", "long enough password")
    base = ["create-user", "--name", "Bo", "--role", "developer", "--new-organization", "Beta"]
    with pytest.raises(SystemExit, match="valid e-mail"):
        cli.main(["create-user", "--email", "not-an-email", *base[1:]])
    cli.main(["create-user", "--email", "bo@example.com", *base[1:]])
    with pytest.raises(SystemExit, match="already exists"):
        cli.main(["create-user", "--email", "BO@example.com", *base[1:]])
    with pytest.raises(SystemExit, match="No organization"):
        cli.main(
            ["create-user", "--email", "cy@example.com", "--name", "Cy", "--role", "viewer", "--organization-id", "x"]
        )
    monkeypatch.setenv("CLOUDBRIDGE_BOOTSTRAP_PASSWORD", "short")
    with pytest.raises(SystemExit, match="at least 12"):
        cli.main(["create-user", "--email", "dee@example.com", *base[1:]])
    with pytest.raises(SystemExit, match="No user"):
        cli.main(["set-password", "--email", "nobody@example.com"])


def test_migrate_creates_the_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "fresh.db"
    monkeypatch.setenv("CLOUDBRIDGE_DATABASE_URL", f"sqlite:///{database.as_posix()}")
    cli.main(["migrate"])
    cli.main(["migrate"])  # running it again is harmless
    assert database.exists() and database.stat().st_size > 0


def test_standalone_worker_stops_on_a_signal(cli_env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.worker import __main__ as worker_main

    handlers: dict[int, object] = {}
    monkeypatch.setattr(signal, "signal", lambda sig, handler: handlers.__setitem__(sig, handler))
    events: list[str] = []

    class FakeWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def start(self) -> None:
            events.append("start")
            handlers[signal.SIGTERM](signal.SIGTERM, None)  # type: ignore[operator]

        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(worker_main, "Worker", FakeWorker)
    worker_main.main()
    assert events == ["start", "stop"]


def test_ensure_owner_creates_the_first_owner_only_once(
    cli_env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["ensure-owner", "--email", "First@Example.com", "--name", "First Owner", "--organization", "Prod"]
    monkeypatch.delenv("CLOUDBRIDGE_BOOTSTRAP_PASSWORD", raising=False)
    with pytest.raises(SystemExit, match="CLOUDBRIDGE_BOOTSTRAP_PASSWORD is required"):
        cli.main(args)
    monkeypatch.setenv("CLOUDBRIDGE_BOOTSTRAP_PASSWORD", "first owner password 42")
    cli.main(args)
    assert "Created owner first@example.com in organization Prod" in capsys.readouterr().out
    client = cli_env.client()
    client.login("first@example.com", "first owner password 42")
    assert client.get("/auth/session").json()["user"]["role"] == "owner"
    # Every later start-up leaves the users alone, even with another password in the environment.
    monkeypatch.setenv("CLOUDBRIDGE_BOOTSTRAP_PASSWORD", "a different password 42")
    cli.main([*args[:2], "someone@example.com", *args[3:]])
    assert "Users already exist; no owner created." in capsys.readouterr().out
    client.login("first@example.com", "first owner password 42")
