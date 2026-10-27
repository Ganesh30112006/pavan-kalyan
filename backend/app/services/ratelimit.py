"""Sign-in rate limiting (per client address and per e-mail address).

The limiter is in memory: with several API processes each enforces its own window, so the
effective limit is multiplied by the process count. Put a shared limiter (for example at the
reverse proxy) in front of the API when running more than one process.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self._max = max_attempts
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, *keys: str) -> int | None:
        """Seconds until another attempt is allowed for any of the keys, or None when allowed."""
        now = time.monotonic()
        worst: float | None = None
        with self._lock:
            for key in keys:
                hits = self._prune(key, now)
                if len(hits) >= self._max:
                    wait = hits[0] + self._window - now
                    worst = wait if worst is None else max(worst, wait)
        return None if worst is None else max(1, math.ceil(worst))

    def hit(self, *keys: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in keys:
                self._prune(key, now).append(now)

    def reset(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._hits.pop(key, None)

    def _prune(self, key: str, now: float) -> deque[float]:
        if len(self._hits) > 100_000:
            # Bound memory under a flood of distinct keys: drop keys with no recent attempts.
            cutoff = now - self._window
            for stale in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
                del self._hits[stale]
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        return hits
