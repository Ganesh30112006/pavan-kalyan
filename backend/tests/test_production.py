"""Production operation: probes, request size limits, logs, production-only settings and the worker process."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from app import readiness
from app.config import Settings
from app.db import Database
from app.logs import JsonFormatter
from app.main import create_app
from app.providers.base import ProviderRegistry
from tests.conftest import ApiClient, Env, make_settings

SCHEMA = readiness.schema_versions()
HEAD = sorted(SCHEMA.heads)


def _stamp(env: Env, version: str) -> None:
    with env.database.engine.begin() as connection:
        connection.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": version})


def test_expected_schema_is_the_latest_migration() -> None:
    files = sorted(p.name for p in (readiness.MIGRATIONS / "versions").glob("[0-9]*.py"))
    assert len(HEAD) == 1 and files[-1].startswith(HEAD[0])


def test_ready_only_when_the_schema_matches_this_release(env: Env) -> None:
    client = env.test_client
    # The fixture creates tables without Alembic, as an unmigrated database would look.
    response = client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"database": "ok", "schema": "not_migrated"}}

    # Older than this release: migrate has not run yet.
    older = sorted(SCHEMA.known - SCHEMA.heads)[0]
    _stamp(env, older)
    response = client.get("/api/v1/ready")
    assert response.status_code == 503 and response.json()["checks"]["schema"] == "behind"

    # Newer than this release (a rolling upgrade migrated it): this process keeps serving.
    _stamp(env, "9999_from_a_newer_release")
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "schema": "newer"}}

    _stamp(env, HEAD[0])
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "schema": "ok"}}
    assert response.headers["cache-control"] == "no-store"


def test_ready_reports_a_stopped_in_process_worker(env: Env) -> None:
    _stamp(env, HEAD[0])

    class Stopped:
        alive = False

    env.test_client.app.state.worker = Stopped()  # type: ignore[attr-defined]
    response = env.test_client.get("/api/v1/ready")
    assert response.status_code == 503 and response.json()["checks"]["worker"] == "stopped"

    Stopped.alive = True
    assert env.test_client.get("/api/v1/ready").json()["checks"]["worker"] == "ok"


def test_ready_reports_an_unreachable_database(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-dir" / "db.sqlite"
    settings = make_settings(tmp_path, database_url=f"sqlite:///{missing.as_posix()}")
    database = Database(settings.database_url)
    app = create_app(settings, database=database, provider_registry=ProviderRegistry({}))
    with TestClient(app) as client:
        response = client.get("/api/v1/ready")
        assert response.status_code == 503
        assert response.json()["checks"] == {"database": "unreachable", "schema": "unknown"}
        # Liveness does not depend on the database.
        assert client.get("/api/v1/health").status_code == 200
    database.dispose()


def test_probes_answer_during_maintenance(tmp_path: Path) -> None:
    # Its own empty database (with CLOUDBRIDGE_TEST_DATABASE_URL the shared one may hold a migrated schema).
    empty = f"sqlite:///{(tmp_path / 'empty.db').as_posix()}"
    settings = make_settings(tmp_path, maintenance_mode=True, database_url=empty)
    database = Database(settings.database_url)
    app = create_app(settings, database=database, provider_registry=ProviderRegistry({}))
    with TestClient(app) as client:
        assert client.get("/api/v1/apps").status_code == 503
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/ready").json()["status"] == "not_ready"  # no schema in this database
    database.dispose()


def test_webhooks_get_a_larger_body_limit_than_other_requests(env: Env, tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_body_bytes=1000, max_webhook_body_bytes=5000)
    app = create_app(settings, database=env.database, provider_registry=ProviderRegistry({}))
    hook = "/api/v1/hooks/github/00000000-0000-0000-0000-000000000000"
    headers = {"Content-Type": "application/json", "X-GitHub-Event": "ping"}
    with TestClient(app) as client:
        assert client.post("/api/v1/auth/login", content=b"x" * 2000).status_code == 413
        # Larger than the general limit but within the webhook limit: reaches the webhook handler.
        assert client.post(hook, content=b"x" * 2000, headers=headers).status_code != 413
        assert client.post(hook, content=b"x" * 6000, headers=headers).status_code == 413

        def chunks(size: int) -> Any:
            for _ in range(size // 500):
                yield b"x" * 500

        # Without Content-Length (chunked), the limit applies while the body is read.
        assert client.post(hook, content=chunks(6000), headers=headers).status_code == 413
        assert client.post(hook, content=chunks(2000), headers=headers).status_code != 413
        # Once an endpoint is reading the body: a 413 in the standard envelope, not a 500.
        client.get("/api/v1/health")  # sets the CSRF cookie
        csrf = {"Content-Type": "application/json", "X-CSRF-Token": str(client.cookies.get("cb_csrf"))}
        response = client.post("/api/v1/auth/login", content=chunks(2000), headers=csrf)
        assert response.status_code == 413 and response.json()["error"]["code"] == "payload_too_large"
        assert response.headers["x-content-type-options"] == "nosniff"


def test_default_webhook_limit_matches_github() -> None:
    settings = Settings(_env_file=None)
    assert settings.max_body_bytes == 1024 * 1024
    assert settings.max_webhook_body_bytes == 25 * 1024 * 1024


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"database_url": "sqlite:///./cloudbridge.db"}, "PostgreSQL in production"),
        ({"public_url": "http://cloudbridge.example.com"}, "CLOUDBRIDGE_PUBLIC_URL must start with https://"),
        ({"cookie_secure": False}, "CLOUDBRIDGE_COOKIE_SECURE"),
    ],
)
def test_production_refuses_unsafe_settings(overrides: dict[str, Any], message: str) -> None:
    values: dict[str, Any] = {
        "environment": "production",
        "database_url": "postgresql+psycopg://cloudbridge@db:5432/cloudbridge",
        "public_url": "https://cloudbridge.example.com",
        **overrides,
    }
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **values)


def test_production_accepts_a_safe_configuration() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        database_url="postgresql+psycopg://cloudbridge@db:5432/cloudbridge",
        public_url="https://cloudbridge.example.com",
    )
    assert settings.environment == "production"
    # Outside production, SQLite and plain-HTTP links are fine.
    assert Settings(_env_file=None, environment="development", public_url="http://localhost:5173").public_url


def _record(message: str, **extra: Any) -> logging.LogRecord:
    record = logging.LogRecord("cloudbridge.access", logging.INFO, __file__, 1, message, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_log_lines() -> None:
    formatter = JsonFormatter()
    line = json.loads(formatter.format(_record("GET /api/v1/apps 200", request_id="req-12345678", status=200)))
    assert line["message"] == "GET /api/v1/apps 200" and line["level"] == "INFO"
    assert line["logger"] == "cloudbridge.access" and line["request_id"] == "req-12345678" and line["status"] == 200
    assert line["time"].endswith("+00:00") and "exception" not in line
    assert "method" not in line  # absent extras are left out, not written as null

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record = _record("failed")
        record.exc_info = sys.exc_info()
    line = json.loads(formatter.format(record))
    assert "RuntimeError: boom" in line["exception"]


def test_requests_are_logged_with_their_id_and_without_query_strings(
    env: Env, owner: ApiClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="cloudbridge.access"):
        response = owner.get("/apps?search=secret-looking-value", headers={"X-Request-ID": "trace-abcdef12"})
        env.test_client.get("/api/v1/health")
        env.test_client.get("/api/v1/ready")
    records = [r for r in caplog.records if r.name == "cloudbridge.access"]
    assert response.status_code == 200
    assert len(records) == 1  # probes are not logged
    record = records[0]
    assert record.__dict__["request_id"] == "trace-abcdef12" and record.__dict__["path"] == "/api/v1/apps"
    assert record.__dict__["status"] == 200 and record.__dict__["method"] == "GET"
    assert "secret-looking-value" not in record.getMessage()
    assert isinstance(record.__dict__["duration_ms"], float)


def test_worker_process_exits_when_its_thread_dies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.worker import __main__ as worker_main

    monkeypatch.setenv("CLOUDBRIDGE_DATABASE_URL", f"sqlite:///{(tmp_path / 'w.db').as_posix()}")
    monkeypatch.setattr("signal.signal", lambda *_: None)
    calls: list[str] = []

    class DeadWorker:
        def __init__(self, *_: object) -> None:
            pass

        alive = False

        def start(self) -> None:
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")

    monkeypatch.setattr(worker_main, "Worker", DeadWorker)
    with pytest.raises(SystemExit) as exited:
        worker_main.main()
    assert exited.value.code == 1 and calls == ["start", "stop"]


def test_worker_reports_alive_while_running(env: Env) -> None:
    assert env.worker.alive is False
    started = threading.Event()
    original = env.worker.run_once

    def run_once() -> int:
        started.set()
        return original()

    env.worker.run_once = run_once  # type: ignore[method-assign]
    env.worker.start()
    try:
        assert started.wait(10)
        assert env.worker.alive is True
    finally:
        env.worker.stop()
    assert env.worker.alive is False


def test_json_logging_takes_over_uvicorn_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.logs import configure_logging

    server_logger = logging.getLogger("uvicorn.error")
    monkeypatch.setattr(server_logger, "handlers", [logging.StreamHandler()])
    monkeypatch.setattr(server_logger, "propagate", False)
    configure_logging(Settings(_env_file=None, log_format="text"))
    assert len(server_logger.handlers) == 1 and server_logger.propagate is False  # text: left alone
    configure_logging(Settings(_env_file=None, log_format="json"))
    assert server_logger.handlers == [] and server_logger.propagate is True


def test_json_logging_keeps_uvicorns_access_log_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.logs import configure_logging

    access = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access, "handlers", [])
    monkeypatch.setattr(access, "propagate", False)  # what --no-access-log leaves behind
    configure_logging(Settings(_env_file=None, log_format="json"))
    assert access.propagate is False and not access.hasHandlers()


def test_worker_logs_a_database_outage_once(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    from sqlalchemy.exc import OperationalError

    outage = OperationalError("SELECT 1", {}, Exception("failed to resolve host 'db'"))
    results: list[BaseException | None] = [outage, outage, outage, None, RuntimeError("bug"), None]

    def run_once() -> int:
        result = results.pop(0)
        if result is not None:
            raise result
        return 0

    env.worker.run_once = run_once  # type: ignore[method-assign]
    with caplog.at_level(logging.INFO, logger="app.worker"):
        for _ in range(6):
            env.worker.iterate()
    messages = [(r.levelname, r.getMessage(), r.exc_info is not None) for r in caplog.records if r.name == "app.worker"]
    assert messages == [
        ("WARNING", "The database is unavailable; the worker keeps retrying (failed to resolve host 'db')", False),
        ("INFO", "The database is reachable again", False),
        ("ERROR", "Worker iteration failed", True),  # other errors keep their traceback
    ]
