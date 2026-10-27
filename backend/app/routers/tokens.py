"""Personal API tokens for the CLI and CI. A token acts as its user, with that user's role."""

from __future__ import annotations

import datetime as dt
import secrets

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.api.deps import API_TOKEN_PREFIX, Principal, get_db, get_principal
from app.api.responses import respond
from app.errors import FieldError, forbidden, invalid, not_found
from app.models import ApiToken
from app.schemas import ApiTokenCreatedOut, ApiTokenIn, ApiTokenOut
from app.security import token_hash
from app.services import audit

router = APIRouter(prefix="/me/api-tokens", tags=["tokens"])
MAX_TOKENS = 20


def _out(token: ApiToken) -> ApiTokenOut:
    return ApiTokenOut(
        id=token.id,
        name=token.name,
        prefix=token.prefix,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
        expires_at=token.expires_at,
    )


@router.get("", response_model=list[ApiTokenOut])
def list_tokens(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> JSONResponse:
    rows = db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == principal.user.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    ).all()
    return respond([_out(t) for t in rows])


@router.post("", status_code=201, response_model=ApiTokenCreatedOut)
def create_token(
    body: ApiTokenIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    if principal.session is None:
        # A token cannot mint more tokens: creating one needs a signed-in browser session.
        raise forbidden()
    errors: list[FieldError] = []
    name = body.name.strip()
    if not name or len(name) > 100:
        errors.append(FieldError("name", "Name must be 1–100 characters."))
    if body.expires_in_days is not None and not 1 <= body.expires_in_days <= 365:
        errors.append(FieldError("expires_in_days", "Tokens expire after 1–365 days, or never."))
    existing = db.scalars(
        select(ApiToken.id).where(ApiToken.user_id == principal.user.id, ApiToken.revoked_at.is_(None))
    ).all()
    if len(existing) >= MAX_TOKENS:
        errors.append(FieldError("name", f"You can have up to {MAX_TOKENS} tokens. Revoke one first."))
    if errors:
        raise invalid(errors)
    raw = f"{API_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    now = clock.utcnow()
    token = ApiToken(
        organization_id=principal.organization_id,
        user_id=principal.user.id,
        name=name,
        token_hash=token_hash(raw),
        prefix=raw[:12],
        created_at=now,
        expires_at=now + dt.timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    db.add(token)
    db.flush()
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action="api_token.created",
        resource_type="api_token",
        resource_id=token.id,
        resource_name=token.name,
        request_id=principal.request_id,
    )
    db.commit()
    return respond(ApiTokenCreatedOut(**_out(token).model_dump(), token=raw), 201)


@router.delete("/{token_id}", status_code=204)
def revoke_token(
    token_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> Response:
    token = db.scalar(
        select(ApiToken).where(
            ApiToken.id == token_id, ApiToken.user_id == principal.user.id, ApiToken.revoked_at.is_(None)
        )
    )
    if token is None:
        raise not_found("API token")
    token.revoked_at = clock.utcnow()
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action="api_token.revoked",
        resource_type="api_token",
        resource_id=token.id,
        resource_name=token.name,
        request_id=principal.request_id,
    )
    db.commit()
    return Response(status_code=204)
