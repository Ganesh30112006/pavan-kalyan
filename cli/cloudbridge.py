#!/usr/bin/env python3
"""CloudBridge command-line client.

Uses only the Python standard library, so it runs anywhere Python 3.10+ does (including CI runners).

    export CLOUDBRIDGE_URL=https://cloudbridge.example.com
    export CLOUDBRIDGE_TOKEN=cbt_...          # Settings → API tokens
    cloudbridge apps
    cloudbridge deploy orders-api --wait
    cloudbridge apply orders-api cloudbridge.yaml
    cloudbridge promote orders-staging --wait
    cloudbridge approve <deployment-id>
    cloudbridge logs orders-api
    cloudbridge status orders-api

`cloudbridge login` saves the URL and token to ~/.config/cloudbridge/config.json (readable only by you);
environment variables take precedence. Exit status: 0 on success, 1 when a deployment fails, 2 on usage
or API errors.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

TERMINAL = {"succeeded", "failed", "rolled_back", "failed_rollback", "cancelled"}
SUCCESS = {"succeeded"}


class CliError(Exception):
    pass


def config_path() -> Path:
    # Resolved lazily: a CI runner that passes the token in the environment may have no home directory.
    return Path(os.environ.get("CLOUDBRIDGE_CONFIG") or Path.home() / ".config" / "cloudbridge" / "config.json")


def load_config() -> tuple[str, str]:
    url = os.environ.get("CLOUDBRIDGE_URL") or ""
    token = os.environ.get("CLOUDBRIDGE_TOKEN") or ""
    if not url or not token:
        saved: dict[str, str] = {}
        config = config_path()
        if config.exists():
            try:
                saved = json.loads(config.read_text(encoding="utf-8"))
            except ValueError:
                saved = {}
        url = url or saved.get("url") or ""
        token = token or saved.get("token") or ""
    if not url or not token:
        raise CliError("Set CLOUDBRIDGE_URL and CLOUDBRIDGE_TOKEN, or run `cloudbridge login`.")
    return url.rstrip("/"), token


class Api:
    def __init__(self, url: str, token: str) -> None:
        parts = urllib.parse.urlsplit(url)
        local = parts.hostname in ("localhost", "127.0.0.1")
        # The token travels with every request, so only HTTPS is used (plain HTTP just for a local server).
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise CliError("The CloudBridge URL must start with https:// (http:// only for localhost).")
        self.base = f"{url}/api/v1"
        self.token = token

    def call(self, method: str, path: str, body: Any = None, *, idempotent: bool = False) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)  # noqa: S310 - scheme checked
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", "cloudbridge-cli/1")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if idempotent:
            request.add_header("Idempotency-Key", str(uuid.uuid4()))
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - scheme checked in __init__
                raw = response.read()
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                problem = json.loads(raw)["error"]
                fields = "; ".join(f"{f['field']}: {f['message']}" for f in problem.get("fields", []))
                message = problem["message"] + (f" ({fields})" if fields else "")
            except (ValueError, KeyError, TypeError):
                message = f"HTTP {error.code}"
            raise CliError(message) from None
        except urllib.error.URLError as error:
            raise CliError(f"Could not reach CloudBridge: {error.reason}") from None
        return json.loads(raw) if raw else None

    def find_app(self, name_or_id: str) -> dict[str, Any]:
        query = urllib.parse.urlencode({"search": name_or_id, "page_size": 100})
        for app in self.call("GET", f"/apps?{query}")["items"]:
            if name_or_id in (app["name"], app["id"]):
                return dict(app)
        try:
            return dict(self.call("GET", f"/apps/{urllib.parse.quote(name_or_id)}"))
        except CliError:
            raise CliError(f"No application named {name_or_id}.") from None


def watch(api: Api, deployment_id: str, timeout_minutes: int) -> int:
    seen = 0
    deadline = time.monotonic() + timeout_minutes * 60
    while time.monotonic() < deadline:
        dep = api.call("GET", f"/deployments/{deployment_id}")
        events = api.call("GET", f"/deployments/{deployment_id}/events")
        for event in events[seen:]:
            print(f"  {event['timestamp'][11:19]}  {event['level']:<7} {event['message']}", flush=True)
        seen = len(events)
        if dep["status"] in TERMINAL:
            url = f" at {dep['service_url']}" if dep.get("service_url") else ""
            print(f"{dep['application_name']} {dep['version']}: {dep['status']}{url}")
            if dep.get("failure"):
                print(f"  {dep['failure']['message']}")
            return 0 if dep["status"] in SUCCESS else 1
        time.sleep(5)
    print("Timed out waiting for the deployment.", file=sys.stderr)
    return 1


def started(dep: dict[str, Any]) -> None:
    note = " (waiting for approval)" if dep["status"] == "awaiting_approval" else ""
    print(f"Deployment {dep['id']} of {dep['application_name']} {dep['version']}: {dep['status']}{note}")


def cmd_login(args: argparse.Namespace) -> int:
    url = args.url.rstrip("/")
    Api(url, args.token).call("GET", "/apps?page_size=1")
    config = config_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"url": url, "token": args.token}), encoding="utf-8")
    config.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"Saved credentials for {url} to {config}.")
    return 0


def cmd_apps(api: Api, args: argparse.Namespace) -> int:
    apps = api.call("GET", "/apps?page_size=100")["items"]
    width = max([len(a["name"]) for a in apps] + [4])
    for app in apps:
        version = app["current_version"] or "-"
        print(f"{app['name']:<{width}}  {app['provider']:<22} {app['status']:<9} {app['health']:<9} {version}")
    return 0


def cmd_status(api: Api, args: argparse.Namespace) -> int:
    app = api.call("GET", f"/apps/{api.find_app(args.app)['id']}")
    print(json.dumps(
        {k: app[k] for k in ("name", "provider", "region", "status", "health", "current_version", "service_url",
                             "environment", "paused")},
        indent=2,
    ))  # fmt: skip
    return 0


def cmd_deploy(api: Api, args: argparse.Namespace) -> int:
    app = api.find_app(args.app)
    dep = api.call("POST", f"/apps/{app['id']}/deployments", idempotent=True)
    started(dep)
    return watch(api, dep["id"], args.timeout) if args.wait else 0


def cmd_promote(api: Api, args: argparse.Namespace) -> int:
    app = api.find_app(args.app)
    dep = api.call("POST", f"/apps/{app['id']}/promote", idempotent=True)
    started(dep)
    return watch(api, dep["id"], args.timeout) if args.wait else 0


def cmd_approve(api: Api, args: argparse.Namespace) -> int:
    action = "reject" if args.reject else "approve"
    dep = api.call("POST", f"/deployments/{args.deployment}/{action}")
    print(f"Deployment {dep['id']}: {dep['status']}")
    return 0


def cmd_apply(api: Api, args: argparse.Namespace) -> int:
    app = api.find_app(args.app)
    content = Path(args.file).read_text(encoding="utf-8")
    version = api.call("GET", f"/apps/{app['id']}/spec")["version"]
    spec = api.call("PUT", f"/apps/{app['id']}/config-file", {"content": content, "version": version})
    print(f"Applied {args.file} to {app['name']} (configuration version {spec['version']}).")
    return 0


def cmd_logs(api: Api, args: argparse.Namespace) -> int:
    app = api.find_app(args.app)
    page = api.call("GET", f"/apps/{app['id']}/logs")
    for entry in page["entries"]:
        print(f"{entry['timestamp']}  {entry['stream']:<6} {entry['message']}")
    if page["truncated"]:
        print("(older lines omitted)", file=sys.stderr)
    return 0


def cmd_metrics(api: Api, args: argparse.Namespace) -> int:
    app = api.find_app(args.app)
    data = api.call("GET", f"/apps/{app['id']}/metrics?range={args.range}")
    if data["unavailable_reason"]:
        print(data["unavailable_reason"])
    for series in data["series"]:
        values = [p["value"] for p in series["points"]]
        latest = f"{values[-1]:g}" if values else "-"
        peak = f"{max(values):g}" if values else "-"
        print(f"{series['name']:<15} latest {latest:>10}  peak {peak:>10}  ({series['unit']})")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="cloudbridge", description="Deploy and manage CloudBridge applications.")
    sub = root.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="save the CloudBridge URL and an API token")
    login.add_argument("--url", required=True)
    login.add_argument("--token", required=True)
    sub.add_parser("apps", help="list applications")
    for name, help_text in (("status", "show an application"), ("logs", "show recent runtime logs")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("app")
    for name, help_text in (
        ("deploy", "deploy the saved configuration"),
        ("promote", "promote to the next environment"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("app")
        command.add_argument("--wait", action="store_true", help="follow the deployment until it finishes")
        command.add_argument("--timeout", type=int, default=60, help="minutes to wait (default 60)")
    approve = sub.add_parser("approve", help="approve (or --reject) a deployment waiting for approval")
    approve.add_argument("deployment")
    approve.add_argument("--reject", action="store_true")
    apply = sub.add_parser("apply", help="apply a cloudbridge.yaml to an application's configuration")
    apply.add_argument("app")
    apply.add_argument("file", nargs="?", default="cloudbridge.yaml")
    metrics = sub.add_parser("metrics", help="show request, latency and resource metrics")
    metrics.add_argument("app")
    metrics.add_argument("--range", choices=("1h", "6h", "24h", "7d"), default="1h")
    return root


COMMANDS = {
    "apps": cmd_apps,
    "status": cmd_status,
    "deploy": cmd_deploy,
    "promote": cmd_promote,
    "approve": cmd_approve,
    "apply": cmd_apply,
    "logs": cmd_logs,
    "metrics": cmd_metrics,
}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "login":
            return cmd_login(args)
        url, token = load_config()
        return COMMANDS[args.command](Api(url, token), args)
    except CliError as error:
        print(f"cloudbridge: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"cloudbridge: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
