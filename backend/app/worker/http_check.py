"""HTTP checks against deployed services. Only provider-issued hosts are contacted (SSRF guard)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx

SLOW_SECONDS = 5.0


@dataclass(frozen=True)
class HttpResult:
    outcome: Literal["passed", "failed", "unreachable"]
    message: str
    status_code: int | None = None
    seconds: float | None = None


def check(
    url: str,
    path: str | None,
    host_allowed: Callable[[str], bool],
    client_factory: Callable[[], httpx.Client] | None = None,
    allow_http: bool = False,
) -> HttpResult:
    parts = urlsplit(url)
    host = parts.hostname or ""
    schemes = ("https", "http") if allow_http else ("https",)
    if parts.scheme not in schemes or not host or not host_allowed(host):
        return HttpResult("failed", "The service address is not one CloudBridge can check.")
    target = f"{parts.scheme}://{parts.netloc}{path or '/'}"
    factory = client_factory or (lambda: httpx.Client(timeout=10.0, follow_redirects=False))
    started = time.monotonic()
    try:
        with factory() as client:
            response = client.get(target, headers={"User-Agent": "CloudBridge-HealthCheck/1"})
    except httpx.TimeoutException:
        return HttpResult("unreachable", "The service did not respond within 10 seconds.")
    except httpx.HTTPError:
        return HttpResult("unreachable", "The service could not be reached.")
    seconds = time.monotonic() - started
    code = response.status_code
    healthy = 200 <= code < 400 if path else code < 500
    where = f"{path} " if path else ""
    if healthy:
        return HttpResult("passed", f"GET {where}returned {code}.", code, seconds)
    if code >= 500:
        return HttpResult("unreachable", f"GET {where}returned {code}.", code, seconds)
    return HttpResult("failed", f"GET {where}returned {code}.", code, seconds)
