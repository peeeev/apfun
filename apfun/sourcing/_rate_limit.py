"""Source-agnostic token-bucket rate limiter.

Each ingester instantiates its own bucket with source-specific params; the
bucket itself knows nothing about Reddit, HN, or anyone else. See
`docs/tasks/005-reddit-ingester.md` and orchestrator feedback 008.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class TokenBucket:
    """Classic token bucket: capacity `burst`, refill `rate_per_sec` tokens/sec.

    `acquire()` blocks until at least one token is available, then consumes it.
    Thread-safe — APScheduler may invoke ingest from a worker thread while
    another ingest is mid-flight on the same source kind.
    """

    rate_per_sec: float
    burst: int

    def __post_init__(self) -> None:
        if self.rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        if self.burst < 1:
            raise ValueError("burst must be >= 1")
        self._tokens: float = float(self.burst)
        self._last_refill: float = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                if elapsed > 0:
                    self._tokens = min(
                        float(self.burst),
                        self._tokens + elapsed * self.rate_per_sec,
                    )
                    self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                wait = deficit / self.rate_per_sec
            time.sleep(wait)
