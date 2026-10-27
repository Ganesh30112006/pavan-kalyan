"""Profile, notification preferences, organization and members."""

from __future__ import annotations

import zoneinfo

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.api.deps import Principal, authorize, get_db, get_principal, get_settings
from app.api.responses import respond
from app.api.serializers import member_out, organization_out, user_out
from app.config import Settings
from app.domain import DEFAULT_NOTIFICATION_EVENTS, NOTIFICATION_EVENTS
from app.errors import FieldError, invalid, not_found
from app.models import NotificationPreference, User
from app.schemas import (
    MemberOut,
    NotificationPreferencesIO,
    OrganizationOut,
    UpdateOrganizationIn,
    UpdateProfileIn,
    UserOut,
)
from app.services import audit

router = APIRouter(tags=["settings"])


def _valid_timezone(name: str) -> bool:
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False
    return True


@router.patch("/me", response_model=UserOut)
def update_profile(
    body: UpdateProfileIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    errors: list[FieldError] = []
    display_name = body.display_name.strip()
    if not display_name:
        errors.append(FieldError("display_name", "Display name is required."))
    elif len(display_name) > 100:
        errors.append(FieldError("display_name", "Display name must be 100 characters or fewer."))
    timezone = (body.preferences.timezone or "").strip() or None
    if timezone and not _valid_timezone(timezone):
        errors.append(FieldError("preferences.timezone", "Choose a valid time zone."))
    if errors:
        raise invalid(errors)
    user = principal.user
    user.display_name = display_name
    user.timezone = timezone
    user.time_format = body.preferences.time_format
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=user,
        action="user.profile_updated",
        resource_type="user",
        resource_id=user.id,
        resource_name=display_name,
        request_id=principal.request_id,
    )
    db.commit()
    return respond(user_out(user))


def _preferences(db: Session, user: User) -> NotificationPreference:
    prefs = db.get(NotificationPreference, user.id)
    if prefs is None:
        prefs = NotificationPreference(
            user_id=user.id, email_enabled=True, events=dict(DEFAULT_NOTIFICATION_EVENTS), updated_at=clock.utcnow()
        )
        db.add(prefs)
        db.flush()
    return prefs


def _prefs_out(prefs: NotificationPreference) -> NotificationPreferencesIO:
    events = {event: bool(prefs.events.get(event, DEFAULT_NOTIFICATION_EVENTS[event])) for event in NOTIFICATION_EVENTS}
    return NotificationPreferencesIO(email_enabled=prefs.email_enabled, events=events)


@router.get("/me/notification-preferences", response_model=NotificationPreferencesIO)
def get_notification_preferences(
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    if not settings.notifications_enabled:
        raise not_found("notification settings")
    prefs = _preferences(db, principal.user)
    db.commit()
    return respond(_prefs_out(prefs))


@router.put("/me/notification-preferences", response_model=NotificationPreferencesIO)
def update_notification_preferences(
    body: NotificationPreferencesIO,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    if not settings.notifications_enabled:
        raise not_found("notification settings")
    unknown = sorted(set(body.events) - set(NOTIFICATION_EVENTS))
    if unknown:
        raise invalid([FieldError("events", f"Unknown notification event: {', '.join(unknown[:5])}.")])
    prefs = _preferences(db, principal.user)
    prefs.email_enabled = body.email_enabled
    prefs.events = {
        event: bool(body.events.get(event, prefs.events.get(event, False))) for event in NOTIFICATION_EVENTS
    }
    prefs.updated_at = clock.utcnow()
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action="user.notification_preferences_updated",
        resource_type="user",
        resource_id=principal.user.id,
        resource_name=principal.user.display_name,
        request_id=principal.request_id,
    )
    db.commit()
    return respond(_prefs_out(prefs))


@router.get("/organization", response_model=OrganizationOut)
def get_organization(principal: Principal = Depends(get_principal)) -> JSONResponse:
    return respond(organization_out(principal.organization))


@router.patch("/organization", response_model=OrganizationOut)
def update_organization(
    body: UpdateOrganizationIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> JSONResponse:
    org = principal.organization
    authorize(
        db,
        principal,
        "organization:manage",
        action="organization.updated",
        resource_type="organization",
        resource_id=org.id,
        resource_name=org.name,
    )
    name = body.name.strip()
    if not name:
        raise invalid([FieldError("name", "Organization name is required.")])
    if len(name) > 100:
        raise invalid([FieldError("name", "Organization name must be 100 characters or fewer.")])
    org.name = name
    audit.record(
        db,
        organization_id=org.id,
        actor=principal.user,
        action="organization.updated",
        resource_type="organization",
        resource_id=org.id,
        resource_name=name,
        request_id=principal.request_id,
    )
    db.commit()
    return respond(organization_out(org))


@router.get("/organization/members", response_model=list[MemberOut])
def list_members(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> JSONResponse:
    users = db.scalars(
        select(User)
        .where(User.organization_id == principal.organization_id, User.is_active.is_(True))
        .order_by(User.display_name, User.email)
    ).all()
    return respond([member_out(u) for u in users])
