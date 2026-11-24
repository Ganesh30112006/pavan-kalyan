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

import httpcore
import httpx

from app import clock
from app.urls import split_url

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
    parts = split_url(url.strip())
    host = ((parts.hostname if parts else None) or "").lower()
    if parts is None or not host or parts.username or parts.password:
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


class _PublicOnlyBackend(httpcore.NetworkBackend):
    """Connects only to public addresses, and to exactly the address it checked.

    `url_problem` checks the host before delivery, but a host name can resolve differently a moment later (DNS
    rebinding: a short-lived record that points at a public address for the check and at an internal one for the
    connection). Resolving here and connecting to that IP closes the gap. TLS still verifies the host name: httpcore
    starts TLS with the URL's host as the server name, not the IP."""

    def __init__(self, resolver: Resolver) -> None:
        self._inner: httpcore.NetworkBackend = httpcore.SyncBackend()
        self._resolver = resolver

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        try:
            addresses = self._resolver(host)
        except OSError:
            raise httpcore.ConnectError("The webhook host could not be resolved.") from None
        if not addresses or not all(_is_public(a) for a in addresses):
            raise httpcore.ConnectError("The webhook host is not a public internet address.")
        return self._inner.connect_tcp(
            addresses[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Any = None
    ) -> httpcore.NetworkStream:
        raise httpcore.ConnectError("Alerts are not sent over Unix sockets.")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class PublicOnlyTransport(httpx.HTTPTransport):
    """An httpx transport that reaches only public addresses (see `_PublicOnlyBackend`)."""

    def __init__(self, resolver: Resolver | None = None) -> None:
        super().__init__()
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(), network_backend=_PublicOnlyBackend(resolver or _resolve)
        )


def display_host(url: str) -> str:
    parts = split_url(url)
    return ((parts.hostname if parts else None) or "").lower()


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
    *,
    allow_private: bool = False,
) -> str:
    """Sends one alert. Returns a short status for the channel's "last delivery" (never raises)."""
    body = json.dumps(payload(kind, alert), separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "CloudBridge-Alerts/1", EVENT_HEADER: alert.event}
    if signing_secret:
        headers[SIGNATURE_HEADER] = sign(signing_secret, body)
    factory = client_factory or (
        lambda: httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=False,
            # Development only (refused in production): a local test receiver.
            transport=None if allow_private else PublicOnlyTransport(),
        )
    )
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
