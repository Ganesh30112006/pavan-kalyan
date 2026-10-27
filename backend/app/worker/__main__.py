"""Runs the worker as its own process: `python -m app.worker` (set CLOUDBRIDGE_RUN_WORKER=false on the API)."""

from __future__ import annotations

import logging
import signal
import threading

from app.config import Settings
from app.db import Database
from app.main import default_providers
from app.worker import Worker


def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    database = Database(settings.database_url)
    worker = Worker(settings, database, default_providers(settings))
    stopped = threading.Event()

    def shutdown(*_: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    worker.start()
    while not stopped.wait(1.0):
        pass
    worker.stop()
    database.dispose()


if __name__ == "__main__":
    main()
