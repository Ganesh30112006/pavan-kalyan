"""Application factory: middleware, error handling and routes."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.deps import bearer_token
from app.config import Settings
from app.db import Database
from app.errors import ApiError, FieldError
from app.providers.aws import AwsAppRunnerAdapter
from app.providers.azure import AzureContainerAppsAdapter
from app.providers.base import ProviderRegistry
from app.providers.ecs import AwsEcsExpressAdapter
from app.providers.gcp import GcpCloudRunAdapter
from app.providers.kubernetes import KubernetesAdapter
from app.routers import alerts, applications, audit, auth, deployments, hooks, platform, providers, tokens
from app.routers import settings as settings_router
from app.schemas import ErrorEnvelope
from app.security import CSRF_COOKIE, CSRF_HEADER, new_token, tokens_match
from app.services.ratelimit import SlidingWindowLimiter
from app.worker import Worker
from app.worker.notify import Notifier

logger = logging.getLogger("cloudbridge")

API_PREFIX = "/api/v1"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_REQUEST_ID = re.compile(r"(?a)^[A-Za-z0-9._:-]{8,128}$")
STATUS_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
}


_ERROR_DESCRIPTIONS = {
    400: "Invalid request",
    401: "Not signed in, or the session or API token is no longer valid",
    403: "Not allowed (role, or a failed CSRF check)",
    404: "Not found (including resources of another organization)",
    409: "Conflict with the current state",
    413: "Request body too large",
    422: "Field validation errors",
    429: "Rate limited; see Retry-After",
}
# Every API route can answer with the standard error envelope; publishing it keeps the schema honest.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope, "description": description} for status, description in _ERROR_DESCRIPTIONS.items()
}


def _allowed_methods(app: FastAPI, request: Request) -> list[str]:
    """The methods some route fully matches for this path. Asking the router per method also works through
    included routers, which do not expose their routes' methods directly."""
    allowed = []
    for method in ("DELETE", "GET", "PATCH", "POST", "PUT"):
        scope = {**request.scope, "method": method}
        if any(route.matches(scope)[0] == Match.FULL for route in app.router.routes):
            allowed.append(method)
    return allowed


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    fields: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "fields": [{"field": f.field, "message": f.message} for f in (fields or [])],
        }
    }
    return JSONResponse(status_code=status, content=body, headers=headers)


def _field_message(error: dict[str, Any]) -> str:
    kind = str(error.get("type", ""))
    message = str(error.get("msg", "Invalid value."))
    if kind == "missing":
        return "This field is required."
    if kind in ("int_type", "int_parsing", "int_from_float"):
        return "Must be a whole number."
    if kind in ("string_type",):
        return "Must be text."
    if kind in ("bool_type", "bool_parsing"):
        return "Must be true or false."
    if kind in ("list_type",):
        return "Must be a list."
    if kind in ("model_type", "dict_type", "model_attributes_type"):
        return "Must be an object."
    if kind == "value_error":
        return message.removeprefix("Value error, ")
    if message.startswith("Input should be"):
        return "Must be" + message.removeprefix("Input should be") + "."
    if message.startswith("String should have"):
        return "Must have" + message.removeprefix("String should have") + "."
    if message.startswith("List should have"):
        return "Must have" + message.removeprefix("List should have") + "."
    return message


def _field_path(loc: Any) -> str:
    parts = [str(p) for p in loc if isinstance(p, str | int)]
    if parts and parts[0] in ("body", "query", "path", "header", "cookie"):
        parts = parts[1:]
    return ".".join(parts)


class BodySizeLimit:
    """Rejects request bodies larger than the limit, including chunked bodies without Content-Length."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length" and value.isdigit() and int(value) > self.max_bytes:
                await self._reject(scope, send)
                return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise ApiError(413, "payload_too_large", "The request body is too large.")
            return message

        await self.app(scope, limited_receive, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "payload_too_large",
                    "message": "The request body is too large.",
                    "request_id": None,
                    "fields": [],
                }
            },
        )
        await response(scope, _noop_receive, send)


async def _noop_receive() -> Message:
    return {"type": "http.disconnect"}


def default_providers(settings: Settings) -> ProviderRegistry:
    return ProviderRegistry(
        {
            "aws_app_runner": AwsAppRunnerAdapter(settings),
            "gcp_cloud_run": GcpCloudRunAdapter(settings),
            "azure_container_apps": AzureContainerAppsAdapter(settings),
            "aws_ecs_express": AwsEcsExpressAdapter(settings),
            "kubernetes": KubernetesAdapter(settings),
        }
    )


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    provider_registry: ProviderRegistry | None = None,
) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db = database or Database(settings.database_url)
    registry = provider_registry or default_providers(settings)
    notifier = Notifier(settings, db)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        worker: Worker | None = None
        if settings.run_worker:
            worker = Worker(settings, db, registry, notifier=notifier)
            worker.start()
        app.state.worker = worker
        try:
            yield
        finally:
            if worker is not None:
                worker.stop()
            notifier.close()
            if database is None:
                db.dispose()

    docs = settings.api_docs_enabled
    app = FastAPI(
        title="CloudBridge API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs" if docs else None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.providers = registry
    app.state.notifier = notifier
    app.state.worker = None
    app.state.login_limiter = SlidingWindowLimiter(settings.login_max_attempts, settings.login_window_seconds)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status, exc.code, exc.message, exc.fields, exc.headers or None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = list(exc.errors())
        if any(e.get("type") == "json_invalid" for e in errors):
            return error_response(request, 400, "invalid_json", "The request body is not valid JSON.")
        fields: list[FieldError] = []
        for error in errors:
            path = _field_path(error.get("loc", ()))
            if not path:
                return error_response(
                    request, 400, "invalid_body", "The request body is missing or has the wrong shape."
                )
            fields.append(FieldError(path, _field_message(error)))
        return error_response(request, 422, "validation_error", "Some fields need attention.", fields)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = STATUS_CODES.get(exc.status_code, "error")
        message = {
            404: "The requested resource was not found.",
            405: "This method is not allowed here.",
        }.get(exc.status_code, "The request could not be processed.")
        headers = dict(getattr(exc, "headers", None) or {})
        if exc.status_code == 405:
            # Starlette lists only the first route matching the path, but each method is its own route.
            headers["Allow"] = ", ".join(_allowed_methods(app, request))
        return error_response(request, exc.status_code, code, message, headers=headers or None)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", extra={"request_id": getattr(request.state, "request_id", None)})
        return error_response(request, 500, "internal_error", "Something went wrong on our side. Try again later.")

    @app.middleware("http")
    async def security(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if _REQUEST_ID.match(incoming) else str(uuid.uuid4())
        request.state.csrf_cookie_set = False
        path = request.url.path
        is_api = path.startswith(API_PREFIX)

        response: Response
        if is_api and settings.maintenance_mode and path != f"{API_PREFIX}/health":
            response = error_response(
                request, 503, "maintenance", "CloudBridge is undergoing maintenance.", headers={"Retry-After": "300"}
            )
        elif (
            is_api
            and request.method in UNSAFE_METHODS
            # API-token requests and signed webhooks never rely on cookies, so they cannot be forged
            # cross-site: a browser does not attach an Authorization header or a webhook signature.
            and bearer_token(request) is None
            and not path.startswith(f"{API_PREFIX}/hooks/")
            and not tokens_match(request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER))
        ):
            response = error_response(
                request, 403, "csrf_failed", "Your session could not be verified. Reload the page and try again."
            )
        else:
            try:
                response = await call_next(request)
            except ApiError as exc:  # raised by the body size limit while the body is read
                response = error_response(request, exc.status, exc.code, exc.message)

        response.headers["X-Request-ID"] = request.state.request_id
        if is_api:
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            # JSON never needs to load anything. The development-only docs page loads Swagger UI's scripts.
            if path != f"{API_PREFIX}/docs":
                response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
            if CSRF_COOKIE not in request.cookies and not request.state.csrf_cookie_set:
                response.set_cookie(
                    CSRF_COOKIE,
                    new_token(),
                    path="/",
                    domain=settings.cookie_domain,
                    secure=settings.cookie_secure,
                    httponly=False,
                    samesite="lax",
                )
        return response

    @app.get(f"{API_PREFIX}/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    for router in (
        auth.router,
        applications.router,
        deployments.router,
        providers.router,
        audit.router,
        settings_router.router,
        platform.router,
        hooks.router,
        alerts.router,
        tokens.router,
    ):
        app.include_router(router, prefix=API_PREFIX, responses=ERROR_RESPONSES)

    app.add_middleware(BodySizeLimit, max_bytes=settings.max_body_bytes)
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Accept", "Content-Type", "X-Request-ID", "X-CSRF-Token", "Idempotency-Key"],
            expose_headers=["X-Request-ID", "Retry-After"],
            max_age=600,
        )
    return app
