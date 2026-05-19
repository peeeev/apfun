"""Shared skeleton for per-source ingesters.

Extracted from `reddit.py`, `hn.py`, and `producthunt.py` after three concrete
implementations made the right abstraction shape clear (per orchestrator
feedback 008 → 013). Behavior-preserving: each ingester keeps its own
`ingest()`, terminal-status set, rate-limit bucket, and source-specific
health-update logic — this module owns only what's genuinely identical across
all three.

What's here:
- `IngestResult` — the per-source outcome reported to the batch wrapper.
- `run_with_retry` — three-attempt retry with terminal-status short-circuit
  + exponential backoff. Each ingester wraps its source-specific request in
  a closure and hands it to this helper.
- `run_ingest_batch` — the outer batch loop (client lifecycle, per-source
  try/except, last_fetched_at update, `scheduler_runs` row). Source-specific
  batch-level logic (UA-block detection for Reddit, missing-token skip for
  ProductHunt) lives in each ingester's own `apply_health_updates` callback.
- `apply_default_health_update` — the per-source counter/auto-disable rule
  that all three ingesters share (success resets, terminal increments,
  transient/empty doesn't move the needle).

What's intentionally NOT here:
- The per-source `ingest()` body — each source iterates differently (subreddits,
  queries, topic fan-out) and reads different config fields. Forcing a shared
  shape there would replace clarity with indirection.
- `content_hash` inputs, payload tagging — these are genuinely source-specific.
- The TokenBucket — each ingester instantiates its own with source-specific
  params; `run_with_retry` takes `bucket_acquire` as a callable.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from apfun.models import SchedulerRun, Source

# heuristic 2026-05-19 — three retries with exponential backoff covers the
# realistic transient-error window (DNS blip, brief 5xx, ephemeral 429). Higher
# retry counts increase blast radius on actual upstream incidents.
MAX_RETRIES = 3
RETRY_BASE_DELAY_S = 1.0


@dataclass
class IngestResult:
    """Per-source outcome reported by `ingest()` to the batch wrapper.

    `status_codes` is a list because one ingest call may issue multiple
    requests (multi-subreddit configs, topic fan-out, pagination). The batch
    wrapper flattens across results when it needs batch-level signals (e.g.,
    Reddit's UA-block fraction).
    """

    source_id: int
    items_captured: int
    status_codes: list[int] = field(default_factory=lambda: list[int]())
    error_class: str | None = None
    latency_ms: int = 0


def run_with_retry(
    request_fn: Callable[[], httpx.Response],
    *,
    terminal_statuses: frozenset[int],
    bucket_acquire: Callable[[], None],
) -> tuple[int, dict[str, Any] | None, str | None]:
    """Three-attempt retry loop with terminal-status short-circuit.

    Returns `(final_status, parsed_body_or_None, error_class_or_None)`:
    - On a terminal status (anywhere in `terminal_statuses`): returns
      immediately with `(status, None, None)`.
    - On a 2xx response that parses cleanly: returns `(status, body, None)`.
    - On 5xx/429/timeout/connect-error: retries with exponential backoff up to
      `MAX_RETRIES`. Returns the final attempt's outcome.

    Each attempt calls `bucket_acquire()` first so per-source rate limits are
    honored even across retries.
    """
    last_status = 0
    last_error: str | None = None
    for attempt in range(MAX_RETRIES):
        bucket_acquire()
        try:
            resp = request_fn()
            last_status = resp.status_code
            if resp.status_code in terminal_statuses:
                return resp.status_code, None, None
            if 500 <= resp.status_code < 600 or resp.status_code == 429:
                last_error = f"HTTP {resp.status_code}"
            else:
                resp.raise_for_status()
                return resp.status_code, resp.json(), None
        except httpx.HTTPError as exc:
            last_error = type(exc).__name__
        if attempt < MAX_RETRIES - 1:
            delay = RETRY_BASE_DELAY_S * (2**attempt) + random.uniform(0, 0.1)
            time.sleep(delay)
    return last_status, None, last_error


HealthUpdateFn = Callable[[list[Source], list[IngestResult]], None]
IngestFn = Callable[[Session, Source, httpx.Client], IngestResult]


def run_ingest_batch(
    session: Session,
    sources: list[Source],
    *,
    job_id: str,
    client: httpx.Client | None,
    ingest_fn: IngestFn,
    apply_health_updates: HealthUpdateFn,
    logger: logging.Logger,
) -> list[IngestResult]:
    """Generic batch runner.

    Iterates `sources` calling `ingest_fn(session, source, client)` per source,
    captures unhandled exceptions into result rows so the batch keeps going,
    then hands `(sources, results)` to `apply_health_updates` which decides
    per-source counter/is_active mutations. Writes one `scheduler_runs` row at
    the end summarizing the batch.

    `apply_health_updates` runs *after* all per-source ingest calls so it can
    compute batch-level signals (Reddit's UA-block fraction) before deciding
    increments.
    """
    started_at = datetime.now(UTC)
    owned_client = client is None
    if client is None:
        client = httpx.Client()

    results: list[IngestResult] = []
    batch_error: str | None = None
    try:
        for source in sources:
            try:
                result = ingest_fn(session, source, client)
                results.append(result)
                source.last_fetched_at = datetime.now(UTC)
            except Exception as exc:  # noqa: BLE001 — keep the batch going
                logger.exception("ingest failed for source_id=%s", source.id)
                results.append(
                    IngestResult(
                        source_id=source.id,
                        items_captured=0,
                        status_codes=[],
                        error_class=type(exc).__name__,
                    )
                )
                if batch_error is None:
                    batch_error = type(exc).__name__

        apply_health_updates(sources, results)
        session.commit()
    finally:
        if owned_client:
            client.close()

    finished_at = datetime.now(UTC)
    session.add(
        SchedulerRun(
            job_id=job_id,
            started_at=started_at,
            finished_at=finished_at,
            ok=batch_error is None,
            error=batch_error,
            items_processed=sum(r.items_captured for r in results),
        )
    )
    session.commit()
    return results


def apply_default_health_update(
    source: Source,
    result: IngestResult,
    *,
    terminal_statuses: frozenset[int],
    threshold: int,
    logger: logging.Logger,
    source_kind: str,
) -> None:
    """Per-source counter rule shared across all three ingesters.

    - Any 2xx in `result.status_codes`: reset `consecutive_failures` to 0.
    - Any status in `terminal_statuses` without a same-source 2xx: increment.
      When the counter hits `threshold`, set `is_active=False` and log WARNING.
    - Transient errors (5xx, 429, timeout) and empty `status_codes`: no-op.

    Per-source batch-level guards (Reddit's UA-block, ProductHunt's
    missing-token) wrap *around* this call inside each module's own
    `apply_health_updates` function.
    """
    if not result.status_codes:
        return
    saw_success = any(200 <= s < 300 for s in result.status_codes)
    saw_terminal = any(s in terminal_statuses for s in result.status_codes)

    if saw_success:
        source.consecutive_failures = 0
        return
    if saw_terminal:
        source.consecutive_failures += 1
        if source.consecutive_failures >= threshold:
            source.is_active = False
            logger.warning(
                f"{source_kind}.source_auto_disabled",
                extra={
                    f"{source_kind}_auto_disable": {
                        "source_id": source.id,
                        "source_name": source.name,
                        "consecutive_failures": source.consecutive_failures,
                        "status_codes": result.status_codes,
                    }
                },
            )


__all__ = [
    "IngestResult",
    "MAX_RETRIES",
    "RETRY_BASE_DELAY_S",
    "apply_default_health_update",
    "run_ingest_batch",
    "run_with_retry",
]
