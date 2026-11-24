"""A tiny web server for testing CloudBridge deployments. Standard library only.

GET /         an HTML page showing which instance answered
GET /healthz  {"status": "ok"} for the platform's health check
GET /api/info JSON with the version, host name and start time
"""

from __future__ import annotations

import html
import json
import os
import signal
import socket
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))
VERSION = os.environ.get("APP_VERSION", "1.0.0")
STARTED = datetime.now(timezone.utc).isoformat(timespec="seconds")
HOST = socket.gethostname()


def info() -> dict[str, str]:
    return {"app": "hello-web", "version": VERSION, "host": HOST, "started": STARTED}


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hello from CloudBridge</title>
<style>
  :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: Canvas; color: CanvasText; }}
  main {{ max-width: 32rem; padding: 2rem 1rem; text-align: center; }}
  h1 {{ font-size: 1.75rem; margin: 0 0 .5rem; }}
  dl {{ display: grid; grid-template-columns: auto 1fr; gap: .25rem 1rem; text-align: left;
        margin: 1.5rem auto 0; width: fit-content; }}
  dt {{ opacity: .7; }}
  dd {{ margin: 0; font-family: ui-monospace, monospace; overflow-wrap: anywhere; }}
</style>
</head>
<body>
<main>
  <h1>Hello from CloudBridge</h1>
  <p>This test web server was deployed through CloudBridge.</p>
  <dl>
    <dt>Version</dt><dd>{version}</dd>
    <dt>Instance</dt><dd>{host}</dd>
    <dt>Started</dt><dd>{started}</dd>
  </dl>
</main>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "hello-web"
    sys_version = ""

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: dict[str, str]) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._json(200, {"status": "ok"})
        elif path == "/api/info":
            self._json(200, info())
        elif path == "/":
            page = PAGE.format(**{k: html.escape(v) for k, v in info().items()})
            self._send(200, page.encode(), "text/html; charset=utf-8")
        else:
            self._json(404, {"error": "not found"})

    do_HEAD = do_GET

    def log_message(self, format: str, *args: object) -> None:
        sys.stdout.write(f"{self.address_string()} {format % args}\n")
        sys.stdout.flush()


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)  # noqa: S104 (containers listen on all interfaces)
    # Platforms stop containers with SIGTERM. Exit the serve loop from the signal handler (calling
    # server.shutdown() here would deadlock: it waits for serve_forever, which runs on this thread).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"hello-web {VERSION} listening on port {PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("hello-web stopped", flush=True)


if __name__ == "__main__":
    main()
