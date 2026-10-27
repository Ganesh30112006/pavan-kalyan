"""Session endpoints: /auth/session, /auth/login, /auth/logout."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import clock
from app.api.deps import Principal, client_ip, get_db, get_settings, load_session, request_id
from app.api.responses import respond
from app.api.serializers import session_out
from app.config import Settings
from app.domain import permissions_for
from app.errors import ApiError, unauthenticated
from app.models import Organization, User, UserSession
from app.schemas import LoginIn, SessionOut
from app.security import CSRF_COOKIE, SESSION_COOKIE, dummy_verify, new_token, token_hash, verify_password
from app.services import audit

router = APIRouter(prefix="/auth", tags=["session"])

INVALID_LOGIN = "The e-mail address or password is incorrect."


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_absolute_hours * 3600,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def set_csrf_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        token,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=False,
        samesite="lax",
    )


def clear_cookies(response: Response, settings: Settings) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name, path="/", domain=settings.cookie_domain, secure=settings.cookie_secure, samesite="lax"
        )


@router.get("/session", response_model=SessionOut)
def get_session(
    request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> JSONResponse:
    loaded = load_session(db, request.cookies.get(SESSION_COOKIE), settings)
    if loaded is None:
        raise unauthenticated("You are not signed in.")
    session, user, organization = loaded
    principal = Principal(user, organization, session, frozenset(permissions_for(user.role)), request_id(request))
    return respond(session_out(principal, settings))


@router.post("/login", response_model=SessionOut)
def login(
    body: LoginIn, request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> JSONResponse:
    limiter = request.app.state.login_limiter
    email = body.email.strip().lower()
    ip = client_ip(request)
    keys = (f"ip:{ip}", f"email:{email}")
    wait = limiter.retry_after(*keys)
    if wait is not None:
        raise ApiError(
            429, "rate_limited", "Too many sign-in attempts. Try again later.", headers={"Retry-After": str(wait)}
        )

    user = db.scalar(select(User).where(func.lower(User.email) == email)) if email else None
    if user is None:
        dummy_verify(body.password)
        ok = False
    else:
        ok = verify_password(body.password, user.password_hash) and user.is_active
    if not ok or user is None:
        limiter.hit(*keys)
        if user is not None:
            audit.record(
                db,
                organization_id=user.organization_id,
                actor=user,
                action="auth.login",
                resource_type="user",
                resource_id=user.id,
                resource_name=user.display_name,
                outcome="failure",
                request_id=request_id(request),
            )
            db.commit()
        # Same response whether or not the e-mail exists.
        raise ApiError(401, "invalid_credentials", INVALID_LOGIN)

    limiter.reset(f"email:{email}")
    organization = db.get(Organization, user.organization_id)
    if organization is None:
        raise ApiError(401, "invalid_credentials", INVALID_LOGIN)

    # Session rotation: any session presented with this request is replaced.
    old = request.cookies.get(SESSION_COOKIE)
    if old:
        db.execute(delete(UserSession).where(UserSession.token_hash == token_hash(old)))
    now = clock.utcnow()
    token = new_token()
    session = UserSession(
        user_id=user.id,
        token_hash=token_hash(token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + dt.timedelta(hours=settings.session_absolute_hours),
        ip_address=ip,
        user_agent=(request.headers.get("user-agent") or "")[:300] or None,
    )
    db.add(session)
    audit.record(
        db,
        organization_id=user.organization_id,
        actor=user,
        action="auth.login",
        resource_type="user",
        resource_id=user.id,
        resource_name=user.display_name,
        request_id=request_id(request),
    )
    db.commit()
    principal = Principal(user, organization, session, frozenset(permissions_for(user.role)), request_id(request))
    response = respond(session_out(principal, settings))
    set_session_cookie(response, token, settings)
    set_csrf_cookie(response, new_token(), settings)
    request.state.csrf_cookie_set = True
    return response


@router.post("/logout", status_code=204)
def logout(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    loaded = load_session(db, token, settings)
    if loaded is not None:
        session, user, _ = loaded
        audit.record(
            db,
            organization_id=user.organization_id,
            actor=user,
            action="auth.logout",
            resource_type="user",
            resource_id=user.id,
            resource_name=user.display_name,
            request_id=request_id(request),
        )
        db.delete(session)
        db.commit()
    response = Response(status_code=204)
    clear_cookies(response, settings)
    request.state.csrf_cookie_set = True
    return response
