"""Notifications: e-mail (when SMTP is configured and the user opted in) and alert channels
(Slack, Microsoft Teams, signed webhooks). Both are sent only after the change they describe is
committed, and dropped if the transaction rolls back."""

from __future__ import annotations

import logging
import smtplib
import ssl
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy import event as sa_event
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import clock
from app.config import Settings
from app.db import Database
from app.domain import DEFAULT_NOTIFICATION_EVENTS
from app.models import AlertChannel, Application, NotificationPreference, User
from app.services import alerts

logger = logging.getLogger(__name__)
_PENDING = "cloudbridge_pending_mail"
_PENDING_ALERTS = "cloudbridge_pending_alerts"
_HOOKED = "cloudbridge_mail_hooks"


class Notifier:
    def __init__(
        self,
        settings: Settings,
        database: Database | None = None,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._client_factory = client_factory
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cloudbridge-notify")

    @property
    def client_factory(self) -> Callable[[], httpx.Client] | None:
        return self._client_factory

    def notify(
        self,
        db: Session,
        organization_id: str,
        event: str,
        subject: str,
        body: str,
        *,
        application: Application | None = None,
    ) -> None:
        link = self._link(application)
        self._queue_alerts(db, organization_id, event, subject, body, application, link)
        if not self._settings.notifications_enabled:
            return
        rows = db.execute(
            select(User, NotificationPreference)
            .outerjoin(NotificationPreference, NotificationPreference.user_id == User.id)
            .where(User.organization_id == organization_id, User.is_active.is_(True))
        ).all()
        recipients = []
        for user, prefs in rows:
            enabled = prefs.email_enabled if prefs else True
            wants = (prefs.events if prefs else {}).get(event, DEFAULT_NOTIFICATION_EVENTS.get(event, False))
            if enabled and wants:
                recipients.append(user.email)
        if recipients:
            self._hook(db)
            db.info.setdefault(_PENDING, []).append((recipients, subject, body + (f"\n\n{link}" if link else "")))

    def _link(self, application: Application | None) -> str | None:
        base = (self._settings.public_url or "").rstrip("/")
        if not base:
            return None
        return f"{base}/applications/{application.id}" if application is not None else base

    def _queue_alerts(
        self,
        db: Session,
        organization_id: str,
        event: str,
        subject: str,
        body: str,
        application: Application | None,
        link: str | None,
    ) -> None:
        channels = db.scalars(
            select(AlertChannel).where(AlertChannel.organization_id == organization_id, AlertChannel.enabled.is_(True))
        ).all()
        targets = [(c.id, c.kind, c.url, c.signing_secret) for c in channels if event in (c.events or [])]
        if not targets:
            return
        alert = alerts.Alert(
            event=event,
            subject=subject,
            message=body,
            application_id=application.id if application else None,
            application_name=application.name if application else None,
            link=link,
            occurred_at=clock.utcnow(),
        )
        self._hook(db)
        db.info.setdefault(_PENDING_ALERTS, []).extend((target, alert) for target in targets)

    def _hook(self, db: Session) -> None:
        if db.info.get(_HOOKED):
            return
        db.info[_HOOKED] = True
        sa_event.listen(db, "after_commit", self._after_commit)
        sa_event.listen(db, "after_rollback", self._after_rollback)

    def _after_commit(self, db: Session) -> None:
        for recipients, subject, body in db.info.pop(_PENDING, []):
            self._pool.submit(self._send, recipients, subject, body)
        for target, alert in db.info.pop(_PENDING_ALERTS, []):
            self._pool.submit(self._deliver, target, alert)

    @staticmethod
    def _after_rollback(db: Session) -> None:
        db.info.pop(_PENDING, None)
        db.info.pop(_PENDING_ALERTS, None)

    def _deliver(self, target: tuple[str, str, str, str | None], alert: alerts.Alert) -> None:
        channel_id, kind, url, secret = target
        problem = alerts.url_problem(kind, url, allow_private=self._settings.allow_private_alert_urls)
        status = f"Not sent: {problem}" if problem else alerts.deliver(kind, url, secret, alert, self._client_factory)
        if status.startswith("Delivered"):
            logger.info("Alert delivered", extra={"channel_id": channel_id, "event": alert.event})
        else:
            logger.warning("Alert not delivered", extra={"channel_id": channel_id, "event": alert.event})
        self.record_delivery(channel_id, status)

    def record_delivery(self, channel_id: str, status: str) -> None:
        if self._database is None:
            return
        try:
            with self._database.session() as db:
                values: dict[str, Any] = {"last_delivery_at": clock.utcnow(), "last_delivery_status": status[:80]}
                db.execute(update(AlertChannel).where(AlertChannel.id == channel_id).values(**values))
                db.commit()
        except Exception:
            logger.exception("Could not record the alert delivery", extra={"channel_id": channel_id})

    def _send(self, recipients: list[str], subject: str, body: str) -> None:
        settings = self._settings
        try:
            with smtplib.SMTP(str(settings.smtp_host), settings.smtp_port, timeout=15) as smtp:
                if settings.smtp_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if settings.smtp_username and settings.smtp_password:
                    smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
                for recipient in recipients:
                    message = EmailMessage()
                    message["From"] = str(settings.smtp_from)
                    message["To"] = recipient
                    message["Subject"] = f"[CloudBridge] {subject}"
                    message.set_content(body)
                    smtp.send_message(message)
        except (OSError, smtplib.SMTPException):
            logger.warning("Could not send notification e-mail")

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
