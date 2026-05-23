"""Job functions + APScheduler registration.

Each job is a sync function with signature `() -> None`. The shared
`_wrap` decorator catches unhandled exceptions and writes a fallback
`scheduler_runs` row tagged with the scheduler job_id — the *inner* batch
functions (`run_ingest_batch`, `normalize_raw_signals`, `cluster_signals`)
already write their own `scheduler_runs` rows on the happy path, so the
wrapper deliberately doesn't double-write on success.

The Stage 2 demand-check job slot is empty per orchestrator feedback 019
Q1 — Stage 2 (task 011) has nothing to filter yet; rewire after the
scheduler-driven routing review (3-5 cycles, ~30-50 candidates in the
inbox).

Cadence intervals are unchanged from the task 012 spec per feedback 019
Q3 — trust the prior, validate via `scheduler_runs.items_returned`
distribution after a few days of running.
"""
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from apfun.db import SessionLocal
from apfun.models import SchedulerRun, Source

logger = logging.getLogger(__name__)


def _wrap(job_id: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    """Wrap a job function to log unhandled exceptions to `scheduler_runs`.

    Inner batch functions write their own scheduler_runs row on the happy
    path; this wrapper only writes a row when the inner call raises (and
    therefore never reached its own SchedulerRun write). The two paths
    don't overlap, so the table stays free of double-counted firings.
    """

    def decorator(fn: Callable[[], None]) -> Callable[[], None]:
        @wraps(fn)
        def wrapped() -> None:
            started = datetime.now(UTC)
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 — capture for observability
                logger.exception("scheduler job %s failed", job_id)
                with SessionLocal() as session:
                    session.add(
                        SchedulerRun(
                            job_id=job_id,
                            started_at=started,
                            finished_at=datetime.now(UTC),
                            ok=False,
                            error=f"{type(exc).__name__}: {exc}",
                            items_processed=None,
                        )
                    )
                    session.commit()
                # Don't re-raise — APScheduler would log a second time, and
                # there's nothing useful for it to do beyond what we already
                # recorded.

        return wrapped

    return decorator


def _active_sources(kind: str) -> list[Source]:
    """Load active sources of a given kind from the DB.

    Jobs that operate on N sources at once load them at firing time so newly
    added sources start ingesting on the next tick without restart.
    """
    with SessionLocal() as session:
        return list(
            session.execute(
                select(Source).where(Source.kind == kind, Source.is_active.is_(True))
            ).scalars()
        )


@_wrap("reddit.ingest_batch")
def reddit_ingest_job() -> None:
    from apfun.sourcing import reddit

    sources = _active_sources("reddit")
    if not sources:
        logger.info("reddit: no active sources, skipping")
        return
    with SessionLocal() as session:
        reddit.ingest_batch(session, sources)


@_wrap("hn.ingest_batch")
def hn_ingest_job() -> None:
    from apfun.sourcing import hn

    sources = _active_sources("hn")
    if not sources:
        logger.info("hn: no active sources, skipping")
        return
    with SessionLocal() as session:
        hn.ingest_batch(session, sources)


@_wrap("producthunt.ingest_batch")
def producthunt_ingest_job() -> None:
    from apfun.sourcing import producthunt

    sources = _active_sources("producthunt")
    if not sources:
        logger.info("producthunt: no active sources, skipping")
        return
    with SessionLocal() as session:
        producthunt.ingest_batch(session, sources)


@_wrap("indiehackers.ingest_batch")
def indiehackers_ingest_job() -> None:
    from apfun.sourcing import indiehackers

    sources = _active_sources("indiehackers")
    if not sources:
        logger.info("indiehackers: no active sources, skipping")
        return
    with SessionLocal() as session:
        indiehackers.ingest_batch(session, sources)


@_wrap("review_sites.ingest_batch")
def review_sites_ingest_job() -> None:
    from apfun.sourcing.review_sites import ingest_batch as review_ingest_batch

    sources = _active_sources("review_sites")
    if not sources:
        logger.info("review_sites: no active sources, skipping")
        return
    with SessionLocal() as session:
        review_ingest_batch(session, sources)


@_wrap("pipeline.normalize")
def normalize_job() -> None:
    from apfun.pipeline.normalize import normalize_raw_signals

    with SessionLocal() as session:
        normalize_raw_signals(session, only_new=True)


@_wrap("pipeline.cluster")
def cluster_job() -> None:
    from apfun.llm.client import LLMClient
    from apfun.pipeline.cluster import cluster_signals

    llm_client = LLMClient()
    with SessionLocal() as session:
        cluster_signals(session, llm_client=llm_client)


def register_all(scheduler: BackgroundScheduler) -> None:
    """Register every job on the production cadence.

    Intervals are the task 012 spec defaults; held unchanged per feedback
    019 Q3. The Stage 2 slot is deliberately empty — wired in when task 011
    ships and its own runbook has been run.
    """
    # All interval jobs carry an explicit `start_date` so their cadence anchors
    # to a fixed UTC grid, NOT to scheduler-start time. A bare IntervalTrigger
    # anchors its first fire to "now", so two bare-or-mixed triggers drift
    # relative to each other depending on when the process booted — which broke
    # the intended "HN 1h after Reddit" and "cluster 10min after normalize"
    # offsets (cluster was firing *before* normalize). Fixed anchors make the
    # relative ordering deterministic across restarts. (Residual: booting inside
    # the 10-min normalize→cluster gap can delay one cluster cycle; coalesce=True
    # + the next window self-correct it.)

    # Ingest jobs — cadences chosen to "wake when there's plausibly new content"
    # rather than to hammer upstream. Empty results just no-op.
    scheduler.add_job(
        reddit_ingest_job,
        # 6h grid anchored to 00:00 UTC → 00/06/12/18.
        trigger=IntervalTrigger(hours=6, start_date="2026-01-01 00:00:00+00:00"),
        id="reddit.ingest_batch",
        replace_existing=True,
    )
    scheduler.add_job(
        hn_ingest_job,
        # 1h offset from Reddit so the two heaviest ingesters don't fire together
        # → 01/07/13/19 UTC.
        trigger=IntervalTrigger(hours=6, start_date="2026-01-01 01:00:00+00:00"),
        id="hn.ingest_batch",
        replace_existing=True,
    )
    scheduler.add_job(
        producthunt_ingest_job,
        trigger=CronTrigger(hour=7, minute=0, timezone="UTC"),
        id="producthunt.ingest_batch",
        replace_existing=True,
    )
    scheduler.add_job(
        indiehackers_ingest_job,
        trigger=CronTrigger(hour=9, minute=0, timezone="UTC"),
        id="indiehackers.ingest_batch",
        replace_existing=True,
    )
    scheduler.add_job(
        review_sites_ingest_job,
        trigger=CronTrigger(day_of_week="mon", hour=3, minute=0, timezone="UTC"),
        id="review_sites.ingest_batch",
        replace_existing=True,
    )

    # Pipeline stages — Stage 0 normalize runs ahead of Stage 1 each tick.
    scheduler.add_job(
        normalize_job,
        # 2h grid anchored to :00 → even hours UTC (00:00, 02:00, ...).
        trigger=IntervalTrigger(hours=2, start_date="2026-01-01 00:00:00+00:00"),
        id="pipeline.normalize",
        replace_existing=True,
    )
    scheduler.add_job(
        cluster_job,
        # 10-minute offset from normalize so signal_text rows are ready by the
        # time Stage 1 looks for them → :10 past each even hour UTC.
        trigger=IntervalTrigger(hours=2, start_date="2026-01-01 00:10:00+00:00"),
        id="pipeline.cluster",
        replace_existing=True,
    )

    # Stage 2 (demand check) slot deliberately empty. Wired in task 011 PR
    # after the routing-review orchestrator turn (per feedback 019 Q1).
