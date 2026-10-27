"""Sleep schedules: pause an application during quiet hours to save money, and wake it afterwards.

A schedule is a daily window in the team's time zone, e.g. 20:00 to 08:00 on weekdays. A window that
ends earlier than it starts runs overnight, and belongs to the day it starts on.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.errors import FieldError

_TIME = re.compile(r"(?a)^([01]\d|2[0-3]):([0-5]\d)$")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class Schedule:
    timezone: str
    days: tuple[int, ...]
    """Days the window starts on, 0 = Monday."""
    sleep_at: dt.time
    wake_at: dt.time

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> Schedule | None:
        if not data:
            return None
        try:
            return cls(
                timezone=str(data["timezone"]),
                days=tuple(int(d) for d in data["days"]),
                sleep_at=dt.time.fromisoformat(str(data["sleep_at"])),
                wake_at=dt.time.fromisoformat(str(data["wake_at"])),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def to_json(self) -> dict[str, Any]:
        return {
            "timezone": self.timezone,
            "days": list(self.days),
            "sleep_at": self.sleep_at.strftime("%H:%M"),
            "wake_at": self.wake_at.strftime("%H:%M"),
        }

    def asleep_at(self, moment: dt.datetime) -> bool:
        local = moment.astimezone(ZoneInfo(self.timezone))
        now = local.time().replace(second=0, microsecond=0)
        today = local.weekday()
        if self.sleep_at < self.wake_at:
            return today in self.days and self.sleep_at <= now < self.wake_at
        # Overnight window: from sleep_at today until wake_at tomorrow.
        yesterday = (today - 1) % 7
        return (today in self.days and now >= self.sleep_at) or (yesterday in self.days and now < self.wake_at)


def parse(timezone: str, days: list[int], sleep_at: str, wake_at: str) -> tuple[Schedule | None, list[FieldError]]:
    errors: list[FieldError] = []
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(FieldError("timezone", "Choose a time zone such as Europe/London or Asia/Kolkata."))
    if not days or any(d not in range(7) for d in days) or len(set(days)) != len(days):
        errors.append(FieldError("days", "Choose at least one day."))
    for field, value in (("sleep_at", sleep_at), ("wake_at", wake_at)):
        if not _TIME.match(value):
            errors.append(FieldError(field, "Use a 24-hour time such as 20:00."))
    if not errors and sleep_at == wake_at:
        errors.append(FieldError("wake_at", "The wake-up time must differ from the sleep time."))
    if errors:
        return None, errors
    return (
        Schedule(
            timezone=timezone,
            days=tuple(sorted(days)),
            sleep_at=dt.time.fromisoformat(sleep_at),
            wake_at=dt.time.fromisoformat(wake_at),
        ),
        [],
    )
