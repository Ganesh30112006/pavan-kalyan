"""Log setup shared by the API and the worker process."""

from __future__ import annotations

import datetime as dt
import json
import logging

from app.config import Settings

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
# Extra fields passed with `logger.info(..., extra={...})` that are copied into JSON lines.
EXTRA_FIELDS = ("request_id", "method", "path", "status", "duration_ms")


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, known extra fields and the exception."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in EXTRA_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                entry[name] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging(settings: Settings) -> None:
    """Configures the root logger once (a no-op when something, e.g. a test runner, already did)."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else logging.Formatter(TEXT_FORMAT))
    logging.basicConfig(level=settings.log_level.upper(), handlers=[handler])
    if settings.log_format == "json":
        # Uvicorn installs its own plain-text handlers before it loads the app; send its lines (startup,
        # shutdown, errors) through the JSON handler too, so a log collector sees one format.
        # uvicorn.access stays as uvicorn left it: the app logs requests itself (--no-access-log turns it off).
        for name in ("uvicorn", "uvicorn.error"):
            server_logger = logging.getLogger(name)
            server_logger.handlers.clear()
            server_logger.propagate = True
