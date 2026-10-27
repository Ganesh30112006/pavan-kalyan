"""Idempotency-Key handling for creates, deployments, rollbacks and connections.

A repeated key with the same payload returns the original result (the resource it created, in its
current state, with the original status code). A repeated key with a different payload is a 409.
The record is written in the same transaction as the resource, so a crash cannot leave one without
the other.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import clock
from app.errors import ApiError, bad_request, conflict
from app.models import IdempotencyRecord

HEADER = "idempotency-key"
_KEY = re.compile(r"(?a)^[\x21-\x7e]{1,200}$")
RETENTION = dt.timedelta(hours=24)


@dataclass(frozen=True)
class IdempotencyContext:
    key: str
    scope: str
    fingerprint: str
    organization_id: str
    user_id: str


def context(
    request: Request, *, scope: str, payload: Any, organization_id: str, user_id: str
) -> IdempotencyContext | None:
    key = request.headers.get(HEADER)
    if key is None:
        return None
    if not _KEY.match(key):
        raise bad_request("invalid_idempotency_key", "The Idempotency-Key header must be 1 to 200 visible characters.")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    fingerprint = hashlib.sha256(f"{scope}\n{canonical}".encode()).hexdigest()
    return IdempotencyContext(key, scope, fingerprint, organization_id, user_id)


def lookup(db: Session, ctx: IdempotencyContext | None) -> IdempotencyRecord | None:
    if ctx is None:
        return None
    record = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == ctx.user_id,
            IdempotencyRecord.scope == ctx.scope,
            IdempotencyRecord.key == ctx.key,
            IdempotencyRecord.created_at > clock.utcnow() - RETENTION,
        )
    )
    if record is not None and record.request_hash != ctx.fingerprint:
        raise conflict(
            "idempotency_key_reused",
            "This request reuses an idempotency key with different content. Start a new submission.",
        )
    return record


def remember(
    db: Session, ctx: IdempotencyContext | None, *, status_code: int, resource_type: str, resource_id: str
) -> None:
    if ctx is None:
        return
    # A key that expired is replaced rather than colliding with the unique constraint.
    db.execute(
        delete(IdempotencyRecord).where(
            IdempotencyRecord.user_id == ctx.user_id,
            IdempotencyRecord.scope == ctx.scope,
            IdempotencyRecord.key == ctx.key,
        )
    )
    db.add(
        IdempotencyRecord(
            organization_id=ctx.organization_id,
            user_id=ctx.user_id,
            scope=ctx.scope,
            key=ctx.key,
            request_hash=ctx.fingerprint,
            status_code=status_code,
            resource_type=resource_type,
            resource_id=resource_id,
            created_at=clock.utcnow(),
        )
    )


def purge_expired(db: Session) -> int:
    result = db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.created_at <= clock.utcnow() - RETENTION))
    return int(getattr(result, "rowcount", 0) or 0)


def in_flight_conflict() -> ApiError:
    return conflict("request_in_progress", "An identical request is already being processed. Check the result shortly.")
