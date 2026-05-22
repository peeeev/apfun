"""APScheduler-based job runner. See `docs/tasks/012-scheduler.md`.

`setup.start_scheduler()` returns a configured `BackgroundScheduler`. Started
from the FastAPI lifespan handler in `apfun.main`; jobs run as sync functions
in worker threads so they share the existing sync DB + sync HTTP stack
(per CLAUDE.md → Concurrency model).
"""
