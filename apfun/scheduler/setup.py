"""APScheduler `BackgroundScheduler` configuration + lifecycle.

`build_scheduler()` constructs a scheduler with SQLite jobstore + a thread
pool. `start_scheduler()` builds, registers jobs, and starts. Both are pure
functions over a `Settings`-like object so tests can inject an in-memory
jobstore and a smaller pool.

Single-process design — see CLAUDE.md → Concurrency model. Each job is sync
and runs in a worker thread (`BackgroundScheduler` + `ThreadPoolExecutor`).
"""
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from apfun.config import settings

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# heuristic 2026-05-22 — 10 worker threads is comfortable headroom over the
# six concurrent jobs registered today (Reddit/HN/PH/IH ingest + Stage 0
# normalize + Stage 1 cluster + review-miner). SQLite serializes writes via
# busy_timeout so additional concurrency past ~10 just queues, not parallelizes.
_DEFAULT_POOL_SIZE = 10

# All jobs share these execution semantics. coalesce=True collapses missed
# firings (e.g., container restart) into one catch-up run rather than
# replaying every miss; max_instances=1 prevents a slow run from being
# clobbered by the next firing while it's still working.
_JOB_DEFAULTS = {
    "coalesce": True,
    "max_instances": 1,
    "misfire_grace_time": 300,
}


def build_scheduler(
    *,
    db_url: str | None = None,
    pool_size: int = _DEFAULT_POOL_SIZE,
) -> BackgroundScheduler:
    """Construct a scheduler with the project's SQLite jobstore + executor.

    `db_url` defaults to `settings.db_url` (production). Tests pass an
    in-memory or temp-file URL to avoid stomping the real jobstore.
    """
    if db_url is None:
        db_url = settings.db_url

    jobstores = {
        "default": SQLAlchemyJobStore(url=db_url, tablename="apscheduler_jobs"),
    }
    executors = {
        "default": ThreadPoolExecutor(pool_size),
    }
    return BackgroundScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=_JOB_DEFAULTS,
        timezone="UTC",
    )


def start_scheduler(
    *,
    register_jobs: Callable[[BackgroundScheduler], None] | None = None,
    db_url: str | None = None,
    pool_size: int = _DEFAULT_POOL_SIZE,
) -> BackgroundScheduler:
    """Build, register all jobs, and start.

    `register_jobs` defaults to the production registry in `jobs.py`. Tests
    inject a custom registrar (or `lambda _: None`) to avoid wiring real
    ingester triggers.
    """
    scheduler = build_scheduler(db_url=db_url, pool_size=pool_size)

    if register_jobs is None:
        # Local import to keep the test path importable even when DB models
        # aren't available — jobs.py imports the ORM models.
        from apfun.scheduler import jobs

        register_jobs = jobs.register_all

    register_jobs(scheduler)
    scheduler.start()
    logger.info("scheduler started with %d jobs", len(scheduler.get_jobs()))
    return scheduler
