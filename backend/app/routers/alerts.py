"""Alert channels: Slack, Microsoft Teams and signed webhooks for the organization."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.api.deps import Principal, authorize, get_db, get_notifier, get_principal, get_settings
from app.api.responses import respond
from app.config import Settings
from app.domain import NOTIFICATION_EVENTS
from app.errors import FieldError, invalid, not_found
from app.models import AlertChannel
from app.schemas import AlertChannelCreatedOut, AlertChannelIn, AlertChannelOut, AlertTestOut
from app.services import alerts, audit
from app.worker.notify import Notifier

router = APIRouter(prefix="/alert-channels", tags=["alerts"])
MAX_CHANNELS = 20


def _out(channel: AlertChannel) -> AlertChannelOut:
    return AlertChannelOut(
        id=channel.id,
        name=channel.name,
        kind=channel.kind,
        host=alerts.display_host(channel.url),
        events=list(channel.events or []),
        enabled=channel.enabled,
        signed=bool(channel.signing_secret),
        created_at=channel.created_at,
        last_delivery_at=channel.last_delivery_at,
        last_delivery_status=channel.last_delivery_status,
    )


def _find(db: Session, principal: Principal, channel_id: str) -> AlertChannel:
    channel = db.scalar(
        select(AlertChannel).where(
            AlertChannel.id == channel_id, AlertChannel.organization_id == principal.organization_id
        )
    )
    if channel is None:
        raise not_found("alert channel")
    return channel


def _check(body: AlertChannelIn, settings: Settings, require_url: bool) -> list[FieldError]:
    errors: list[FieldError] = []
    if not body.name.strip() or len(body.name.strip()) > 100:
        errors.append(FieldError("name", "Name must be 1–100 characters."))
    unknown = [e for e in body.events if e not in NOTIFICATION_EVENTS]
    if unknown or not body.events:
        errors.append(FieldError("events", "Choose at least one event."))
    url = (body.url or "").strip()
    if require_url and not url:
        errors.append(FieldError("url", "Enter the webhook address."))
    elif url and (problem := alerts.url_problem(body.kind, url, allow_private=settings.allow_private_alert_urls)):
        errors.append(FieldError("url", problem))
    return errors


def _record(db: Session, principal: Principal, channel: AlertChannel, action: str) -> None:
    audit.record(
        db,
        organization_id=principal.organization_id,
        actor=principal.user,
        action=action,
        resource_type="alert_channel",
        resource_id=channel.id,
        resource_name=channel.name,
        request_id=principal.request_id,
    )


@router.get("", response_model=list[AlertChannelOut])
def list_channels(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> JSONResponse:
    rows = db.scalars(
        select(AlertChannel)
        .where(AlertChannel.organization_id == principal.organization_id)
        .order_by(AlertChannel.created_at)
    ).all()
    return respond([_out(c) for c in rows])


@router.post("", status_code=201, response_model=AlertChannelCreatedOut)
def create_channel(
    body: AlertChannelIn,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    authorize(db, principal, "providers:manage", action="alert_channel.created", resource_type="alert_channel")
    errors = _check(body, settings, require_url=True)
    count = len(
        db.scalars(select(AlertChannel.id).where(AlertChannel.organization_id == principal.organization_id)).all()
    )
    if count >= MAX_CHANNELS:
        errors.append(FieldError("name", f"An organization can have up to {MAX_CHANNELS} alert channels."))
    if errors:
        raise invalid(errors)
    channel = AlertChannel(
        organization_id=principal.organization_id,
        name=body.name.strip(),
        kind=body.kind,
        url=(body.url or "").strip(),
        signing_secret=secrets.token_hex(32) if body.kind == "webhook" else None,
        events=list(dict.fromkeys(body.events)),
        enabled=body.enabled,
        created_at=clock.utcnow(),
    )
    db.add(channel)
    db.flush()
    _record(db, principal, channel, "alert_channel.created")
    db.commit()
    return respond(AlertChannelCreatedOut(**_out(channel).model_dump(), signing_secret=channel.signing_secret), 201)


@router.put("/{channel_id}", response_model=AlertChannelOut)
def update_channel(
    channel_id: str,
    body: AlertChannelIn,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    channel = _find(db, principal, channel_id)
    authorize(
        db,
        principal,
        "providers:manage",
        action="alert_channel.updated",
        resource_type="alert_channel",
        resource_id=channel.id,
        resource_name=channel.name,
    )
    errors = _check(body, settings, require_url=False)
    if body.kind != channel.kind:
        errors.append(FieldError("kind", "Create a new channel to change its type."))
    if errors:
        raise invalid(errors)
    channel.name = body.name.strip()
    if body.url and body.url.strip():
        channel.url = body.url.strip()
    channel.events = list(dict.fromkeys(body.events))
    channel.enabled = body.enabled
    _record(db, principal, channel, "alert_channel.updated")
    db.commit()
    return respond(_out(channel))


@router.delete("/{channel_id}", status_code=204)
def delete_channel(
    channel_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> Response:
    channel = _find(db, principal, channel_id)
    authorize(
        db,
        principal,
        "providers:manage",
        action="alert_channel.deleted",
        resource_type="alert_channel",
        resource_id=channel.id,
        resource_name=channel.name,
    )
    _record(db, principal, channel, "alert_channel.deleted")
    db.delete(channel)
    db.commit()
    return Response(status_code=204)


@router.post("/{channel_id}/test", response_model=AlertTestOut)
def test_channel(
    channel_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    notifier: Notifier = Depends(get_notifier),
) -> JSONResponse:
    """Sends a test alert now and reports what the receiver answered."""
    channel = _find(db, principal, channel_id)
    authorize(
        db,
        principal,
        "providers:manage",
        action="alert_channel.tested",
        resource_type="alert_channel",
        resource_id=channel.id,
        resource_name=channel.name,
    )
    problem = alerts.url_problem(channel.kind, channel.url, allow_private=settings.allow_private_alert_urls)
    if problem:
        status = f"Not sent: {problem}"
    else:
        status = alerts.deliver(
            channel.kind,
            channel.url,
            channel.signing_secret,
            alerts.Alert(
                event="test",
                subject="CloudBridge test alert",
                message=f"{principal.user.display_name} sent a test alert to {channel.name}.",
                occurred_at=clock.utcnow(),
            ),
            notifier.client_factory,
        )
    channel.last_delivery_at = clock.utcnow()
    channel.last_delivery_status = status[:80]
    _record(db, principal, channel, "alert_channel.tested")
    db.commit()
    return respond(AlertTestOut(delivered=status.startswith("Delivered"), status=status))
