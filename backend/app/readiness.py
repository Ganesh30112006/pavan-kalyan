"""Readiness: can this process serve requests right now (database reachable, schema current, worker running)?"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import Database

logger = logging.getLogger(__name__)
MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
# Values that still let the process take traffic.
PASSING = frozenset({"ok", "newer"})


@dataclass(frozen=True)
class SchemaVersions:
    heads: frozenset[str]
    """The revision(s) `python -m app.cli migrate` of this release brings the database to."""
    known: frozenset[str]
    """Every revision this release has a migration for."""


def schema_versions() -> SchemaVersions:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    scripts = ScriptDirectory.from_config(config)
    return SchemaVersions(
        heads=frozenset(scripts.get_heads()),
        known=frozenset(revision.revision for revision in scripts.walk_revisions()),
    )


def _schema_state(current: frozenset[str], expected: SchemaVersions) -> str:
    if current == expected.heads:
        return "ok"
    if not current:
        return "not_migrated"
    if current <= expected.known:
        # Migrate has not run for this release yet: its code would use columns that do not exist.
        return "behind"
    # A newer release already migrated the database. Migrations only add to the schema while both releases
    # run, so this (older) process keeps serving until the rollout replaces it.
    return "newer"


def check(database: Database, expected: SchemaVersions, worker_alive: bool | None) -> dict[str, str]:
    """Named checks, each "ok", "newer" (passing) or a short reason. `worker_alive` is None when the worker runs
    in another process."""
    checks: dict[str, str] = {}
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            try:
                current = frozenset(str(v) for v in connection.scalars(text("SELECT version_num FROM alembic_version")))
            except SQLAlchemyError:
                current = frozenset()
        checks["database"] = "ok"
        checks["schema"] = _schema_state(current, expected)
    except SQLAlchemyError as exc:
        # One line, no traceback: a load balancer probes every few seconds during an outage.
        detail = str(getattr(exc, "orig", None) or exc).strip()
        logger.warning("Readiness: the database is unreachable (%s)", detail.splitlines()[0][:200] if detail else "")
        checks["database"] = "unreachable"
        checks["schema"] = "unknown"
    if worker_alive is not None:
        checks["worker"] = "ok" if worker_alive else "stopped"
    return checks


def passing(checks: dict[str, str]) -> bool:
    return all(value in PASSING for value in checks.values())
