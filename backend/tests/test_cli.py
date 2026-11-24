"""The stdlib CLI (cli/cloudbridge.py) against a scripted local HTTP server."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

CLI_PATH = Path(__file__).resolve().parents[2] / "cli" / "cloudbridge.py"

Reply = tuple[int, dict[str, str], bytes]


def _load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cloudbridge_cli", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cloudbridge_cli"] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


class Server:
    """Answers each request with the next scripted reply for its path (the last one repeats)."""

    def __init__(self) -> None:
        self.replies: dict[str, list[Reply]] = {}
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self) -> None:
                outer.requests.append((self.command, self.path, dict(self.headers.items())))
                queue = outer.replies.get(self.path) or [(404, {}, b"")]
                status, headers, body = queue.pop(0) if len(queue) > 1 else queue[0]
                if status == 0:  # drop the connection without an answer
                    self.close_connection = True
                    return
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _reply

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://localhost:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def on(self, path: str, *replies: Reply) -> None:
        self.replies[f"/api/v1{path}"] = list(replies)

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def ok(body: Any) -> Reply:
    return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Server]:
    srv = Server()
    monkeypatch.setenv("CLOUDBRIDGE_URL", srv.url)
    monkeypatch.setenv("CLOUDBRIDGE_TOKEN", "cbt_test")
    monkeypatch.setenv("CLOUDBRIDGE_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    yield srv
    srv.close()


DEP = {"id": "d1", "application_name": "web", "version": 3, "status": "deploying", "service_url": None}


def test_wait_survives_a_brief_outage(server: Server, capsys: pytest.CaptureFixture[str]) -> None:
    server.on(
        "/deployments/d1",
        (502, {}, b"bad gateway"),
        (0, {}, b""),
        ok(DEP),
        ok({**DEP, "status": "succeeded", "service_url": "https://web.example.com"}),
    )
    server.on("/deployments/d1/events", ok([]))
    assert cli.watch(cli.Api(server.url, "cbt_test"), "d1", 5) == 0
    assert "succeeded at https://web.example.com" in capsys.readouterr().out


def test_wait_gives_up_on_a_lasting_outage_and_on_client_errors(server: Server) -> None:
    server.on("/deployments/d1", (503, {}, b""))
    with pytest.raises(cli.CliError):
        cli.watch(cli.Api(server.url, "cbt_test"), "d1", 5)
    assert len([r for r in server.requests if r[1] == "/api/v1/deployments/d1"]) == cli.WATCH_ERRORS

    server.on("/deployments/d2", (404, {"Content-Type": "application/json"}, b'{"error": {"message": "Not found."}}'))
    with pytest.raises(cli.CliError, match="Not found"):
        cli.watch(cli.Api(server.url, "cbt_test"), "d2", 5)


def test_redirects_are_not_followed_with_the_token(server: Server) -> None:
    server.on("/apps?page_size=100&page=1", (302, {"Location": f"{server.url}/collect"}, b""))
    assert cli.main(["apps"]) == 2
    assert [path for _, path, _ in server.requests] == ["/api/v1/apps?page_size=100&page=1"]


def test_non_json_success_is_an_error_not_a_traceback(server: Server, capsys: pytest.CaptureFixture[str]) -> None:
    server.on("/apps?page_size=100&page=1", (200, {"Content-Type": "text/html"}, b"<html>login</html>"))
    assert cli.main(["apps"]) == 2
    assert "not JSON" in capsys.readouterr().err


def test_apps_lists_every_page(server: Server, capsys: pytest.CaptureFixture[str]) -> None:
    def app(i: int) -> dict[str, Any]:
        return {"name": f"app-{i}", "provider": "aws_app_runner", "status": "running", "health": "healthy",
                "current_version": 1}  # fmt: skip

    server.on("/apps?page_size=100&page=1", ok({"items": [app(i) for i in range(100)], "total": 101}))
    server.on("/apps?page_size=100&page=2", ok({"items": [app(100)], "total": 101}))
    assert cli.main(["apps"]) == 0
    assert "app-100" in capsys.readouterr().out


def test_login_writes_the_token_file_private(server: Server, tmp_path: Path) -> None:
    server.on("/apps?page_size=1", ok({"items": [], "total": 0}))
    assert cli.main(["login", "--url", server.url, "--token", "cbt_saved"]) == 0
    config = tmp_path / "config.json"
    assert json.loads(config.read_text(encoding="utf-8")) == {"url": server.url, "token": "cbt_saved"}
    if os.name == "posix":
        assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_a_config_file_that_is_not_an_object_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text("[1, 2]", encoding="utf-8")
    monkeypatch.delenv("CLOUDBRIDGE_URL", raising=False)
    monkeypatch.delenv("CLOUDBRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("CLOUDBRIDGE_CONFIG", str(config))
    with pytest.raises(cli.CliError, match="CLOUDBRIDGE_URL"):
        cli.load_config()
