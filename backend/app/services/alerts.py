"""Alert channels: Slack, Microsoft Teams and signed webhooks.

Webhook addresses act like passwords, so they are never returned by the API (only their host is). Only
HTTPS addresses on public hosts are accepted, and Slack and Teams addresses must be on their services'
own domains, so an alert cannot be pointed at an internal system.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app import clock

Resolver = Callable[[str], list[str]]
SLACK_HOSTS = ("hooks.slack.com",)
TEAMS_SUFFIXES = (".webhook.office.com", ".logic.azure.com", ".environment.api.powerplatform.com")
SIGNATURE_HEADER = "X-CloudBridge-Signature"
EVENT_HEADER = "X-CloudBridge-Event"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _resolve(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_global


def url_problem(kind: str, url: str, *, allow_private: bool = False, resolver: Resolver | None = None) -> str | None:
    """Why `url` cannot be used for a `kind` channel, or None when it can."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if not host or parts.username or parts.password:
        return "Enter the full webhook address, e.g. https://hooks.slack.com/services/…"
    local = allow_private and (host in ("localhost", "127.0.0.1") or host.endswith(".localhost"))
    if parts.scheme != "https" and not (local and parts.scheme == "http"):
        return "Webhook addresses must use HTTPS."
    if kind == "slack" and host not in SLACK_HOSTS and not local:
        return "Slack webhook addresses start with https://hooks.slack.com/."
    if kind == "teams" and not host.endswith(TEAMS_SUFFIXES) and not local:
        return "Use the address of a Teams incoming webhook or Workflows trigger."
    if local:
        return None
    try:
        addresses = (resolver or _resolve)(host)
    except OSError:
        return "The webhook host could not be resolved."
    if not addresses or not all(_is_public(a) for a in addresses):
        return "The webhook host must be a public internet address."
    return None


def display_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


@dataclass(frozen=True)
class Alert:
    event: str
    subject: str
    message: str
    application_id: str | None = None
    application_name: str | None = None
    link: str | None = None
    occurred_at: dt.datetime | None = None


def payload(kind: str, alert: Alert) -> dict[str, Any]:
    text = f"*{alert.subject}*\n{alert.message}" + (f"\n{alert.link}" if alert.link else "")
    if kind == "slack":
        return {"text": text}
    if kind == "teams":
        return {"text": text.replace("*", "**")}
    return {
        "event": alert.event,
        "subject": alert.subject,
        "message": alert.message,
        "application": ({"id": alert.application_id, "name": alert.application_name} if alert.application_id else None),
        "link": alert.link,
        "occurred_at": clock.iso(alert.occurred_at or clock.utcnow()),
    }


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def deliver(
    kind: str,
    url: str,
    signing_secret: str | None,
    alert: Alert,
    client_factory: Callable[[], httpx.Client] | None = None,
) -> str:
    """Sends one alert. Returns a short status for the channel's "last delivery" (never raises)."""
    body = json.dumps(payload(kind, alert), separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "CloudBridge-Alerts/1", EVENT_HEADER: alert.event}
    if signing_secret:
        headers[SIGNATURE_HEADER] = sign(signing_secret, body)
    factory = client_factory or (lambda: httpx.Client(timeout=TIMEOUT, follow_redirects=False))
    try:
        with factory() as client:
            response = client.post(url, content=body, headers=headers)
    except httpx.TimeoutException:
        return "Timed out"
    except httpx.HTTPError:
        return "Could not connect"
    if 200 <= response.status_code < 300:
        return f"Delivered ({response.status_code})"
    return f"Rejected ({response.status_code})"
