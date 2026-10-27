from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.config import Settings
from app.db import Base, Database
from app.main import create_app
from app.models import Organization, ProviderConnection, User
from app.providers.base import ProviderRegistry
from app.security import CSRF_COOKIE, hash_password
from app.worker import Worker
from tests.fakes import FakeAdapter

PASSWORD = "correct horse battery staple"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "database_url": f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        "cookie_secure": False,
        "run_worker": False,
        "aws_principal_arn": "arn:aws:iam::999999999999:role/cloudbridge-worker",
        "gcp_trusted_principal": "principalSet://iam.googleapis.com/cloudbridge",
        "login_max_attempts": 5,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def http_ok_factory() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="ok")))


class ApiClient:
    """TestClient wrapper that sends the CSRF header like the frontend does."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def _headers(self, method: str, headers: dict[str, str] | None) -> dict[str, str]:
        result = dict(headers or {})
        if method != "GET":
            token = self.client.cookies.get(CSRF_COOKIE)
            if token:
                result.setdefault("X-CSRF-Token", token)
        return result

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = self._headers(method, kwargs.pop("headers", None))
        return self.client.request(method, f"/api/v1{path}", headers=headers, **kwargs)

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    def login(self, email: str, password: str = PASSWORD) -> Any:
        self.get("/auth/session")  # issues the CSRF cookie, as the frontend's bootstrap does
        response = self.post("/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return response


@dataclass
class Env:
    settings: Settings
    database: Database
    aws: FakeAdapter
    gcp: FakeAdapter
    worker: Worker
    test_client: TestClient
    org_id: str

    def client(self) -> ApiClient:
        return ApiClient(TestClient(self.test_client.app, base_url="http://testserver"))

    def add_user(self, email: str, role: str, name: str | None = None, org_id: str | None = None) -> str:
        with self.database.session() as db:
            user = User(
                organization_id=org_id or self.org_id,
                email=email,
                display_name=name or email.split("@")[0].title(),
                role=role,
                password_hash=hash_password(PASSWORD),
                time_format="24h",
                created_at=clock.utcnow(),
            )
            db.add(user)
            db.commit()
            return user.id

    def add_org(self, name: str) -> str:
        with self.database.session() as db:
            org = Organization(name=name, created_at=clock.utcnow())
            db.add(org)
            db.commit()
            return org.id

    def add_connection(
        self, provider: str = "aws_app_runner", status: str = "connected", org_id: str | None = None
    ) -> str:
        with self.database.session() as db:
            aws = provider == "aws_app_runner"
            conn = ProviderConnection(
                organization_id=org_id or self.org_id,
                name=f"{provider}-{clock.new_id()[:6]}",
                provider=provider,
                default_region="eu-west-1" if aws else "europe-west1",
                status=status,
                account_id="123456789012" if aws else None,
                role_arn="arn:aws:iam::123456789012:role/CloudBridge" if aws else None,
                external_id="cb-test" if aws else None,
                project_id=None if aws else "acme-prod-1",
                workload_identity_provider=None
                if aws
                else "projects/123456/locations/global/workloadIdentityPools/cloudbridge/providers/aws-main",
                service_account_email=None if aws else "deployer@acme-prod-1.iam.gserviceaccount.com",
                created_at=clock.utcnow(),
            )
            db.add(conn)
            db.commit()
            return conn.id

    def run_worker(self, times: int = 1) -> None:
        """Runs worker passes, ignoring scheduled delays (time is not simulated)."""
        from sqlalchemy import update

        from app.models import Application, Deployment

        for _ in range(times):
            with self.database.session() as db:
                db.execute(update(Deployment).values(next_attempt_at=None))
                db.execute(update(Application).values(locked_until=None))
                db.commit()
            self.worker.run_once()


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    settings = make_settings(tmp_path)
    database = Database(settings.database_url)
    Base.metadata.create_all(database.engine)
    aws, gcp = FakeAdapter("aws_app_runner"), FakeAdapter("gcp_cloud_run", min_floor=0)
    registry = ProviderRegistry({"aws_app_runner": aws, "gcp_cloud_run": gcp})
    app = create_app(settings, database=database, provider_registry=registry)
    worker = Worker(
        settings, database, registry, resolver=lambda host: ["140.82.112.3"], http_client_factory=http_ok_factory
    )
    with TestClient(app, base_url="http://testserver") as test_client:
        with database.session() as db:
            org = Organization(name="Acme", created_at=clock.utcnow())
            db.add(org)
            db.commit()
            org_id = org.id
        yield Env(settings, database, aws, gcp, worker, test_client, org_id)
    database.dispose()


@pytest.fixture
def owner(env: Env) -> ApiClient:
    env.add_user("ada@example.com", "owner", "Ada Owner")
    client = env.client()
    client.login("ada@example.com")
    return client


def image_app(connection_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "orders-api",
        "description": "Order service",
        "application_type": "api",
        "runtime": "node",
        "connection_id": connection_id,
        "region": "eu-west-1",
        "source": {
            "type": "image",
            "image": "123456789012.dkr.ecr.eu-west-1.amazonaws.com/orders:1.4.0",
            "repository_url": None,
            "ref_type": None,
            "git_ref": None,
            "dockerfile_path": None,
            "build_context": None,
        },
        "port": 8080,
        "cpu": "1",
        "memory": "2Gi",
        "min_instances": 0,
        "max_instances": 3,
        "concurrency": None,
        "exposure": "public",
        "health_check_path": "/healthz",
        "environment": [{"name": "LOG_LEVEL", "value": "info"}],
        "secret_refs": [
            {"name": "DATABASE_URL", "reference": "arn:aws:secretsmanager:eu-west-1:123456789012:secret:orders/db"}
        ],
    }
    body.update(overrides)
    return body


def repo_source(**overrides: Any) -> dict[str, Any]:
    source = {
        "type": "repository",
        "image": None,
        "repository_url": "https://github.com/acme/orders.git",
        "ref_type": "branch",
        "git_ref": "main",
        "dockerfile_path": "Dockerfile",
        "build_context": ".",
    }
    source.update(overrides)
    return source
