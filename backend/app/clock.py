"""Time and identifier helpers. All timestamps are timezone-aware UTC."""

from __future__ import annotations

import datetime as dt
import threading
import uuid

_lock = threading.Lock()
_last = dt.datetime.min.replace(tzinfo=dt.UTC)
_TICK = dt.timedelta(microseconds=1)


def utcnow() -> dt.datetime:
    """The current time, strictly increasing within the process.

    Some platforms' clocks advance in coarse steps (about 15 ms on Windows before Python 3.13), so two
    records created back to back could share a timestamp and sort in an arbitrary order.
    """
    global _last
    now = dt.datetime.now(dt.UTC)
    with _lock:
        if now <= _last:
            now = _last + _TICK
        _last = now
    return now


def new_id() -> str:
    return str(uuid.uuid4())


def as_utc(value: dt.datetime) -> dt.datetime:
    """Treats naive datetimes (as SQLite returns them) as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def iso(value: dt.datetime) -> str:
    """ISO 8601 in UTC with a Z suffix, as the frontend expects."""
    return as_utc(value).isoformat().replace("+00:00", "Z")
