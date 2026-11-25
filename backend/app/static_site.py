"""Serves the built web app from the API process (CLOUDBRIDGE_STATIC_DIR), for single-container deployments.

The same origin serves the pages and /api/, so cookies stay first-party and no CORS is needed. The headers match
deploy/nginx.conf. API paths are never handled here: unknown API paths keep their JSON 404 and 405 answers.
"""

from __future__ import annotations

from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Match, Route
from starlette.types import Scope

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
    "upgrade-insecure-requests"
)
COMMON = {
    "X-Content-Type-Options": "nosniff",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}
PAGE_HEADERS = {
    **COMMON,
    "Cache-Control": "no-cache",
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
# Hashed file names never change.
ASSET_HEADERS = {**COMMON, "Cache-Control": "public, max-age=31536000, immutable"}


class SiteRoute(Route):
    """GET/HEAD for every path outside /api (all API versions)."""

    def __init__(self, directory: Path, api_prefix: str) -> None:
        self.root = directory.resolve()
        self.index = self.root / "index.html"
        if not self.index.is_file():
            raise RuntimeError(f"CLOUDBRIDGE_STATIC_DIR has no index.html: {self.root}")
        self.api_prefix = api_prefix.rstrip("/")
        super().__init__("/{path:path}", self.serve, methods=["GET", "HEAD"], include_in_schema=False)

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        path = str(scope.get("path", ""))
        if path == self.api_prefix or path.startswith(self.api_prefix + "/"):
            return Match.NONE, {}
        return super().matches(scope)

    def _file(self, relative: str) -> Path | None:
        if not relative or "\x00" in relative:
            return None
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root) or not candidate.is_file():
            return None
        return candidate

    async def serve(self, request: Request) -> Response:
        relative = str(request.path_params.get("path", ""))
        # Source maps are for internal debugging only.
        if relative.endswith(".map"):
            raise HTTPException(404)
        found = self._file(relative)
        if relative.startswith("assets/"):
            if found is None:
                raise HTTPException(404)
            return FileResponse(found, headers=ASSET_HEADERS)
        if found is not None and found != self.index:
            return FileResponse(found, headers=PAGE_HEADERS)
        # Single-page app: unknown paths render index.html, which shows its own 404 page.
        return FileResponse(self.index, headers=PAGE_HEADERS)
