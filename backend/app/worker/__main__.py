"""Runs the worker as its own process: `python -m app.worker` (set CLOUDBRIDGE_RUN_WORKER=false on the API)."""

from __future__ import annotations

import logging
import signal
import sys
import threading

from app.config import Settings
from app.db import Database
from app.logs import configure_logging
from app.main import default_providers
from app.worker import Worker

logger = logging.getLogger("cloudbridge.worker")


def main() -> None:
    settings = Settings()
    configure_logging(settings)
    database = Database(settings.database_url)
    worker = Worker(settings, database, default_providers(settings))
    stopped = threading.Event()

    def shutdown(*_: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    worker.start()
    crashed = False
    while not stopped.wait(1.0):
        if not worker.alive:
            # Exit so the process supervisor (Docker, systemd, Kubernetes) restarts the worker.
            logger.error("The worker thread stopped unexpectedly; exiting")
            crashed = True
            break
    worker.stop()
    database.dispose()
    if crashed:
        sys.exit(1)


if __name__ == "__main__":
    main()
