"""The web app served from the API process (CLOUDBRIDGE_STATIC_DIR), as in the single-container AWS deployment."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from app.providers.base import ProviderRegistry
from tests.conftest import make_settings


@pytest.fixture
def site(tmp_path: Path) -> Iterator[TestClient]:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (web / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (web / "assets" / "index-abc123.js.map").write_text("{}", encoding="utf-8")
    (web / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the web root", encoding="utf-8")
    settings = make_settings(tmp_path, static_dir=str(web), database_url=f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    database = Database(settings.database_url)
    app = create_app(settings, database=database, provider_registry=ProviderRegistry({}))
    with TestClient(app) as client:
        yield client
    database.dispose()


def test_client_side_routes_render_the_app_with_security_headers(site: TestClient) -> None:
    for path in ("/", "/applications", "/applications/abc/deployments", "/does-not-exist"):
        response = site.get(path)
        assert response.status_code == 200, path
        assert "id=root" in response.text
        assert response.headers["cache-control"] == "no-cache"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["strict-transport-security"].startswith("max-age=")
    assert site.head("/applications").status_code == 200


def test_assets_are_cached_and_missing_ones_are_not_the_app(site: TestClient) -> None:
    asset = site.get("/assets/index-abc123.js")
    assert asset.status_code == 200 and asset.text == "console.log(1)"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert site.get("/assets/missing.js").status_code == 404
    assert site.get("/assets/index-abc123.js.map").status_code == 404
    assert site.get("/favicon.svg").text == "<svg/>"


def test_files_outside_the_web_root_are_never_served(site: TestClient) -> None:
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt", "/..%2fsecret.txt"):
        response = site.get(path)
        assert "outside the web root" not in response.text, path


def test_api_paths_keep_their_json_answers(site: TestClient) -> None:
    missing = site.get("/api/v1/no-such-route")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    assert site.get("/api").status_code == 404 and site.get("/api").json()["error"]["code"] == "not_found"
    # A known API path with the wrong method still answers 405 with Allow, not the web app.
    wrong = site.post("/api/v1/health")
    assert wrong.status_code in (403, 405)  # CSRF runs first for unsafe methods without a token
    site.get("/api/v1/health")
    token = site.cookies.get("cb_csrf") or ""
    wrong = site.post("/api/v1/health", headers={"X-CSRF-Token": token})
    assert wrong.status_code == 405 and "GET" in wrong.headers["allow"]
    assert site.get("/api/v1/health").json() == {"status": "ok"}


def test_other_methods_on_pages_are_refused(site: TestClient) -> None:
    site.get("/api/v1/health")
    token = site.cookies.get("cb_csrf") or ""
    response = site.post("/applications", headers={"X-CSRF-Token": token})
    assert response.status_code == 405


def test_a_static_dir_without_index_is_a_startup_error(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    settings = make_settings(tmp_path, static_dir=str(tmp_path / "empty"))
    with pytest.raises(RuntimeError, match=r"no index.html"):
        create_app(settings, database=Database(settings.database_url), provider_registry=ProviderRegistry({}))


def test_without_static_dir_pages_are_not_served(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    database = Database(settings.database_url)
    with TestClient(create_app(settings, database=database, provider_registry=ProviderRegistry({}))) as client:
        assert client.get("/applications").status_code == 404
    database.dispose()
