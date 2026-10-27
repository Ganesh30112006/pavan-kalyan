"""The background worker: runs deployments, connection validation, deletions and health checks.

Work is claimed with a lease (`locked_until`) using a conditional UPDATE, so several worker
processes can run against the same database without doing the same step twice.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections.abc import Callable
from typing import Any

from sqlalchemy import or_, select, update

from app import clock
from app.config import Settings
from app.db import Database
from app.domain import ACTIVE_DEPLOYMENT_STATUSES
from app.models import Application, CustomDomain, Deployment, ProviderConnection
from app.providers.base import ProviderRegistry
from app.worker import tasks
from app.worker.deployments import DeploymentFlow
from app.worker.notify import Notifier

logger = logging.getLogger(__name__)

DEPLOYMENT_LEASE = dt.timedelta(minutes=2)
DELETE_LEASE = dt.timedelta(minutes=2)
HOUSEKEEPING_EVERY = dt.timedelta(minutes=10)
SCHEDULES_EVERY = dt.timedelta(minutes=1)
DOMAIN_CHECK_EVERY = dt.timedelta(minutes=2)
MAX_INTERNAL_ERRORS = 3
BATCH = 20


class Worker:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        registry: ProviderRegistry,
        *,
        resolver: Callable[[str], list[str]] | None = None,
        http_client_factory: Any = None,
        notifier: Notifier | None = None,
    ) -> None:
        self._settings = settings
        self._db = database
        self._registry = registry
        self._http_client_factory = http_client_factory
        self._notifier = notifier or Notifier(settings, database)
        self._flow = DeploymentFlow(settings, registry, self._notifier, resolver, http_client_factory)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_housekeeping: dt.datetime | None = None
        self._last_schedules: dt.datetime | None = None

    # ----- lifecycle -----

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="cloudbridge-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
        self._notifier.close()

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        logger.info("Worker started")
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("Worker iteration failed")
            self._wake.wait(self._settings.worker_poll_seconds)
            self._wake.clear()
        logger.info("Worker stopped")

    def run_once(self) -> int:
        """Processes everything that is due once. Returns the number of items handled."""
        handled = 0
        handled += self._run_pending()
        handled += self._run_deployments()
        handled += self._run_connections()
        handled += self._run_deletions()
        handled += self._run_health_checks()
        handled += self._run_domains()
        now = clock.utcnow()
        if self._last_schedules is None or now - self._last_schedules >= SCHEDULES_EVERY:
            self._last_schedules = now
            handled += self._run_schedules(now)
        if self._last_housekeeping is None or now - self._last_housekeeping > HOUSEKEEPING_EVERY:
            self._last_housekeeping = now
            with self._db.session() as db:
                tasks.housekeeping(db)
                db.commit()
        return handled

    # ----- leases -----

    def _claim(self, model: Any, row_id: str, lease: dt.timedelta) -> bool:
        now = clock.utcnow()
        with self._db.session() as db:
            result = db.execute(
                update(model)
                .where(model.id == row_id, or_(model.locked_until.is_(None), model.locked_until < now))
                .values(locked_until=now + lease)
                .execution_options(synchronize_session=False)
            )
            db.commit()
            return getattr(result, "rowcount", 0) == 1

    # ----- deployments -----

    def _run_pending(self) -> int:
        now = clock.utcnow()
        with self._db.session() as db:
            ids = db.scalars(
                select(Application.id).where(
                    Application.pending_auto_deploy.is_not(None),
                    Application.active_deployment_id.is_(None),
                    Application.delete_requested_at.is_(None),
                    Application.deleted_at.is_(None),
                    or_(Application.locked_until.is_(None), Application.locked_until < now),
                )
            ).all()
        handled = 0
        for app_id in ids:
            if not self._claim(Application, app_id, dt.timedelta(minutes=1)):
                continue
            handled += 1
            try:
                with self._db.session() as db:
                    app = db.get(Application, app_id)
                    if app is not None and app.active_deployment_id is None:
                        tasks.run_pending(db, app)
                        app.locked_until = None
                    db.commit()
            except Exception:
                logger.exception("Deferred deployment failed", extra={"application_id": app_id})
                self._unlock(app_id)
        return handled

    def _run_deployments(self) -> int:
        now = clock.utcnow()
        with self._db.session() as db:
            ids = db.scalars(
                select(Deployment.id)
                .where(
                    Deployment.status.in_(ACTIVE_DEPLOYMENT_STATUSES),
                    or_(Deployment.locked_until.is_(None), Deployment.locked_until < now),
                    or_(Deployment.next_attempt_at.is_(None), Deployment.next_attempt_at <= now),
                )
                .order_by(Deployment.created_at)
                .limit(BATCH)
            ).all()
        handled = 0
        for dep_id in ids:
            if not self._claim(Deployment, dep_id, DEPLOYMENT_LEASE):
                continue
            handled += 1
            self._step_deployment(dep_id)
        return handled

    def _step_deployment(self, dep_id: str) -> None:
        try:
            with self._db.session() as db:
                dep = db.get(Deployment, dep_id)
                if dep is None:
                    return
                delay = self._flow.step(db, dep)
                self._release(dep, delay)
                db.commit()
        except Exception:
            logger.exception("Deployment step failed", extra={"deployment_id": dep_id})
            self._record_internal_error(dep_id)

    @staticmethod
    def _release(dep: Deployment, delay: float | None) -> None:
        dep.locked_until = None
        dep.next_attempt_at = None if delay is None or delay <= 0 else clock.utcnow() + dt.timedelta(seconds=delay)

    def _record_internal_error(self, dep_id: str) -> None:
        """An unexpected error (a bug, or the database being unavailable): retry a few times, then fail."""
        try:
            with self._db.session() as db:
                dep = db.get(Deployment, dep_id)
                if dep is None:
                    return
                state = dict(dep.state or {})
                count = int(state.get("internal_errors", 0)) + 1
                state["internal_errors"] = count
                dep.state = state
                if count >= MAX_INTERNAL_ERRORS and dep.status in ACTIVE_DEPLOYMENT_STATUSES:
                    app = db.get(Application, dep.application_id)
                    self._flow._fail(
                        db,
                        dep,
                        app,
                        "internal_error",
                        "CloudBridge hit an unexpected error while deploying. Try again.",
                    )
                    self._release(dep, None)
                else:
                    self._release(dep, 30.0)
                db.commit()
        except Exception:
            logger.exception("Could not record the deployment error", extra={"deployment_id": dep_id})

    # ----- connections -----

    def _run_connections(self) -> int:
        now = clock.utcnow()
        with self._db.session() as db:
            ids = db.scalars(
                select(ProviderConnection.id)
                .where(
                    ProviderConnection.status == "validating",
                    or_(ProviderConnection.locked_until.is_(None), ProviderConnection.locked_until < now),
                )
                .limit(BATCH)
            ).all()
        lease = dt.timedelta(seconds=self._settings.connection_validation_timeout_seconds + 60)
        handled = 0
        for conn_id in ids:
            if not self._claim(ProviderConnection, conn_id, lease):
                continue
            handled += 1
            try:
                with self._db.session() as db:
                    conn = db.get(ProviderConnection, conn_id)
                    if conn is None or conn.status != "validating":
                        continue
                    tasks.validate_connection(db, conn, self._settings, self._registry, self._notifier)
                    conn.locked_until = None
                    db.commit()
            except Exception:
                logger.exception("Connection validation failed", extra={"connection_id": conn_id})
                self._fail_connection(conn_id)
        return handled

    def _fail_connection(self, conn_id: str) -> None:
        try:
            with self._db.session() as db:
                conn = db.get(ProviderConnection, conn_id)
                if conn is None:
                    return
                validation = dict(conn.validation or {})
                validation.update(
                    {"stage": None, "finished_at": clock.iso(clock.utcnow()), "error_code": "internal_error"}
                )
                conn.validation = validation
                conn.status = "error"
                conn.locked_until = None
                db.commit()
        except Exception:
            logger.exception("Could not record the connection error", extra={"connection_id": conn_id})

    # ----- deletions -----

    def _run_deletions(self) -> int:
        now = clock.utcnow()
        with self._db.session() as db:
            rows = db.execute(
                select(Application.id, Application.active_deployment_id).where(
                    Application.delete_requested_at.is_not(None),
                    Application.deleted_at.is_(None),
                    or_(Application.locked_until.is_(None), Application.locked_until < now),
                )
            ).all()
        handled = 0
        for app_id, active in rows:
            if active is not None or not self._claim(Application, app_id, DELETE_LEASE):
                continue
            handled += 1
            try:
                with self._db.session() as db:
                    app = db.get(Application, app_id)
                    if app is None or app.delete_requested_at is None:
                        continue
                    delay = tasks.delete_application(db, app, self._registry, self._notifier)
                    # The lease doubles as the wait before the next poll.
                    app.locked_until = (
                        None if delay is None or delay <= 0 else clock.utcnow() + dt.timedelta(seconds=delay)
                    )
                    db.commit()
            except Exception:
                logger.exception("Application deletion step failed", extra={"application_id": app_id})
        return handled

    # ----- health -----

    def _run_health_checks(self) -> int:
        now = clock.utcnow()
        due = now - dt.timedelta(seconds=self._settings.health_check_interval_seconds)
        with self._db.session() as db:
            ids = db.scalars(
                select(Application.id)
                .where(
                    Application.deleted_at.is_(None),
                    Application.delete_requested_at.is_(None),
                    Application.active_deployment_id.is_(None),
                    Application.current_deployment_id.is_not(None),
                    Application.status.in_(("running", "failed")),
                    or_(Application.health_checked_at.is_(None), Application.health_checked_at < due),
                    or_(Application.locked_until.is_(None), Application.locked_until < now),
                )
                .limit(BATCH)
            ).all()
        handled = 0
        for app_id in ids:
            if not self._claim(Application, app_id, dt.timedelta(minutes=1)):
                continue
            handled += 1
            try:
                with self._db.session() as db:
                    app = db.get(Application, app_id)
                    if app is None or app.active_deployment_id is not None:
                        continue
                    tasks.check_health(db, app, self._registry, self._http_client_factory, self._notifier)
                    app.locked_until = None
                    db.commit()
            except Exception:
                logger.exception("Health check failed", extra={"application_id": app_id})
                self._unlock(app_id)
        return handled

    # ----- sleep schedules -----

    def _run_schedules(self, now: dt.datetime) -> int:
        with self._db.session() as db:
            ids = db.scalars(
                select(Application.id).where(
                    Application.deleted_at.is_(None),
                    Application.delete_requested_at.is_(None),
                    Application.active_deployment_id.is_(None),
                    Application.current_deployment_id.is_not(None),
                    Application.sleep_schedule.is_not(None),
                    or_(Application.locked_until.is_(None), Application.locked_until < now),
                )
            ).all()
        handled = 0
        for app_id in ids:
            if not self._claim(Application, app_id, dt.timedelta(minutes=1)):
                continue
            handled += 1
            try:
                with self._db.session() as db:
                    app = db.get(Application, app_id)
                    if app is not None and app.active_deployment_id is None:
                        tasks.apply_schedule(db, app, self._registry, now)
                        app.locked_until = None
                    db.commit()
            except Exception:
                logger.exception("Sleep schedule failed", extra={"application_id": app_id})
                self._unlock(app_id)
        return handled

    # ----- custom domains -----

    def _run_domains(self) -> int:
        now = clock.utcnow()
        due = now - DOMAIN_CHECK_EVERY
        with self._db.session() as db:
            ids = db.scalars(
                select(CustomDomain.id)
                .where(
                    CustomDomain.status.in_(("pending_dns", "verifying", "removing")),
                    or_(
                        CustomDomain.checked_at.is_(None),
                        CustomDomain.checked_at < due,
                        CustomDomain.status == "removing",
                    ),
                    or_(CustomDomain.locked_until.is_(None), CustomDomain.locked_until < now),
                )
                .limit(BATCH)
            ).all()
        handled = 0
        for domain_id in ids:
            if not self._claim(CustomDomain, domain_id, dt.timedelta(minutes=1)):
                continue
            handled += 1
            try:
                with self._db.session() as db:
                    domain = db.get(CustomDomain, domain_id)
                    if domain is not None and not tasks.refresh_domain(db, domain, self._registry):
                        domain.locked_until = None
                    db.commit()
            except Exception:
                logger.exception("Custom domain check failed", extra={"domain_id": domain_id})
        return handled

    def _unlock(self, app_id: str) -> None:
        with self._db.session() as db:
            db.execute(update(Application).where(Application.id == app_id).values(locked_until=None))
            db.commit()
