"""Deterministic tests for `apfun.sourcing._rate_limit.TokenBucket`.

Patches `time.monotonic` and `time.sleep` on the rate-limit module to keep the
test free of real clock drift and instantaneous regardless of CI load. The
"virtual clock" advances only when the production code calls `time.sleep`,
which is exactly the behavior we want to assert against.
"""

from __future__ import annotations

import pytest

from apfun.sourcing import _rate_limit
from apfun.sourcing._rate_limit import TokenBucket


@pytest.fixture
def virtual_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A mutable [now] cell. `time.sleep(x)` advances it by x seconds."""
    now: list[float] = [0.0]

    def fake_monotonic() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        assert seconds >= 0
        now[0] += seconds

    monkeypatch.setattr(_rate_limit.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(_rate_limit.time, "sleep", fake_sleep)
    return now


def test_construction_rejects_invalid_params() -> None:
    with pytest.raises(ValueError, match="rate_per_sec"):
        TokenBucket(rate_per_sec=0, burst=1)
    with pytest.raises(ValueError, match="rate_per_sec"):
        TokenBucket(rate_per_sec=-1.0, burst=1)
    with pytest.raises(ValueError, match="burst"):
        TokenBucket(rate_per_sec=1.0, burst=0)


def test_burst_drains_without_blocking(virtual_clock: list[float]) -> None:
    """A fresh bucket starts full — the first `burst` calls don't sleep."""
    bucket = TokenBucket(rate_per_sec=1.0, burst=3)
    start = virtual_clock[0]
    for _ in range(3):
        bucket.acquire()
    assert virtual_clock[0] == start, "burst acquires should not advance the clock"


def test_acquire_blocks_when_empty(virtual_clock: list[float]) -> None:
    """Once the burst is drained, the next acquire waits for refill."""
    bucket = TokenBucket(rate_per_sec=2.0, burst=1)
    bucket.acquire()  # drains the bucket
    start = virtual_clock[0]
    bucket.acquire()
    # rate_per_sec=2.0 means 0.5s per token.
    assert virtual_clock[0] - start == pytest.approx(0.5)


def test_refill_caps_at_burst(virtual_clock: list[float]) -> None:
    """Long idle periods don't accumulate tokens past `burst`."""
    bucket = TokenBucket(rate_per_sec=1.0, burst=2)
    virtual_clock[0] += 100.0  # idle for 100s — would refill to 100 tokens uncapped
    # The bucket should still only let us through `burst` calls without blocking.
    for _ in range(2):
        bucket.acquire()
    start = virtual_clock[0]
    bucket.acquire()
    # Third call has to wait 1 token / 1 per sec = 1.0s.
    assert virtual_clock[0] - start == pytest.approx(1.0)


def test_sustained_rate_matches_configured(virtual_clock: list[float]) -> None:
    """Over many calls, throughput converges to `rate_per_sec`."""
    bucket = TokenBucket(rate_per_sec=4.0, burst=1)
    bucket.acquire()  # drain the initial burst
    start = virtual_clock[0]
    n = 20
    for _ in range(n):
        bucket.acquire()
    elapsed = virtual_clock[0] - start
    # n tokens at 4 per sec = n/4 seconds.
    assert elapsed == pytest.approx(n / 4.0)
