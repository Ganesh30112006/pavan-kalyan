"""Request-scoped dependencies: database session, authenticated principal, permission checks."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.config import Settings
from app.db import Database
from app.domain import Permission, permissions_for
from app.errors import forbidden, unauthenticated
from app.models import ApiToken, Organization, User, UserSession
from app.providers.base import ProviderRegistry
from app.security import SESSION_COOKIE, token_hash
from app.services import audit
from app.worker.notify import Notifier

LAST_SEEN_RESOLUTION = dt.timedelta(minutes=5)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_providers(request: Request) -> ProviderRegistry:
    registry: ProviderRegistry = request.app.state.providers
    return registry


def get_notifier(request: Request) -> Notifier:
    notifier: Notifier = request.app.state.notifier
    return notifier


def wake_worker(request: Request) -> None:
    """Asks the in-process worker (if any) to pick up new work now instead of at its next poll."""
    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        worker.wake()


def get_db(request: Request) -> Iterator[Session]:
    database: Database = request.app.state.db
    with database.session() as db:
        yield db


def request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def client_ip(request: Request) -> str:
    settings = get_settings(request)
    if settings.trust_proxy_headers:
        # The trusted proxy appends the address it saw; earlier entries are supplied by the client.
        forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
        if forwarded:
            return forwarded[-1][:64]
    return (request.client.host if request.client else "unknown")[:64]


@dataclass
class Principal:
    user: User
    organization: Organization
    session: UserSession | None
    """None when the request authenticated with an API token."""
    permissions: frozenset[str]
    request_id: str

    @property
    def organization_id(self) -> str:
        return self.organization.id

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions


def load_session(db: Session, token: str | None, settings: Settings) -> tuple[UserSession, User, Organization] | None:
    if not token or len(token) > 200:
        return None
    row = db.execute(
        select(UserSession, User, Organization)
        .join(User, User.id == UserSession.user_id)
        .join(Organization, Organization.id == User.organization_id)
        .where(UserSession.token_hash == token_hash(token))
    ).first()
    if row is None:
        return None
    session, user, organization = row
    now = clock.utcnow()
    idle_limit = dt.timedelta(minutes=settings.session_idle_minutes)
    if not user.is_active or session.expires_at <= now or now - session.last_seen_at > idle_limit:
        db.delete(session)
        db.commit()
        return None
    if now - session.last_seen_at > LAST_SEEN_RESOLUTION:
        session.last_seen_at = now
        db.commit()
    return session, user, organization


API_TOKEN_PREFIX = "cbt_"


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.startswith(API_TOKEN_PREFIX):
        return None
    return value.strip()


def load_api_token(db: Session, token: str) -> tuple[User, Organization] | None:
    if len(token) > 200:
        return None
    row = db.execute(
        select(ApiToken, User, Organization)
        .join(User, User.id == ApiToken.user_id)
        .join(Organization, Organization.id == User.organization_id)
        .where(ApiToken.token_hash == token_hash(token))
    ).first()
    if row is None:
        return None
    record, user, organization = row
    now = clock.utcnow()
    if not user.is_active or record.revoked_at is not None or (record.expires_at and record.expires_at <= now):
        return None
    if record.last_used_at is None or now - record.last_used_at > LAST_SEEN_RESOLUTION:
        record.last_used_at = now
        db.commit()
    return user, organization


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    token = bearer_token(request)
    if token is not None:
        authenticated = load_api_token(db, token)
        if authenticated is None:
            raise unauthenticated("The API token is invalid, expired or revoked.")
        user, organization = authenticated
        return Principal(
            user=user,
            organization=organization,
            session=None,
            permissions=frozenset(permissions_for(user.role)),
            request_id=request_id(request),
        )
    loaded = load_session(db, request.cookies.get(SESSION_COOKIE), get_settings(request))
    if loaded is None:
        raise unauthenticated("Your session has ended. Sign in again.")
    session, user, organization = loaded
    return Principal(
        user=user,
        organization=organization,
        session=session,
        permissions=frozenset(permissions_for(user.role)),
        request_id=request_id(request),
    )


def authorize(
    db: Session,
    principal: Principal,
    permission: Permission,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    resource_name: str | None = None,
) -> None:
    """Raises 403 and records a 'denied' activity entry when the principal lacks the permission."""
    if principal.can(permission):
        return
    db.rollback()
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        outcome="denied",
        request_id=principal.request_id,
    )
    db.commit()
    raise forbidden()
