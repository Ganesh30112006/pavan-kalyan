"""The error model. Every error response uses the envelope from docs/api-contract.md:

    {"error": {"code": "...", "message": "...", "request_id": "...", "fields": [{"field": "...", "message": "..."}]}}

Messages are written for end users. They never contain stack traces, SQL, file paths, internal
host names, provider raw responses or secret values.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str


@dataclass(eq=False)
class ApiError(Exception):
    status: int
    code: str
    message: str
    fields: list[FieldError] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.status} {self.code}: {self.message}"


def not_found(what: str = "resource") -> ApiError:
    return ApiError(404, "not_found", f"The {what} was not found.")


def forbidden(message: str = "You do not have permission to do this.") -> ApiError:
    return ApiError(403, "forbidden", message)


def unauthenticated(message: str = "Sign in to continue.") -> ApiError:
    return ApiError(401, "unauthenticated", message)


def conflict(code: str, message: str, fields: list[FieldError] | None = None) -> ApiError:
    return ApiError(409, code, message, fields or [])


def invalid(fields: list[FieldError], message: str = "Some fields need attention.") -> ApiError:
    return ApiError(422, "validation_error", message, fields)


def bad_request(code: str, message: str) -> ApiError:
    return ApiError(400, code, message)
