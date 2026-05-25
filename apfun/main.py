"""FastAPI app entrypoint. See CLAUDE.md → Networking: bind to 0.0.0.0:4000.

Apache strips/proxies basic-auth at the edge — this app does NOT look at
`Authorization` headers. Per `docs/tasks/013-admin-ui-base.md`.
"""
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from apfun.config import settings
from apfun.scheduler.setup import start_scheduler
from apfun.web.routes import router as web_router

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "web" / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start the APScheduler on app startup; stop on shutdown.

    `app.state.scheduler` lets `/healthz` report `running`.
    `app.state.started_at` lets `/ops` surface "service started Xh ago" so
    the operator can confirm a restart picked up the latest code.

    If the operator paused the scheduler from /ops before a restart, re-apply
    the pause (APScheduler's pause() is in-memory only; the intent is persisted
    in `runtime_state`). Per orchestrator request 031 §1.

    The pause re-apply is wrapped defensively: with `--reload`, new code boots
    *before* the operator runs the migration, so the `runtime_state` table may
    not exist yet. A missing table (or any DB hiccup) must NOT crash startup —
    degrade to "not paused" and log. Otherwise the whole service is down for the
    code-before-migration window, not just the routes that read the new schema.
    (Tuition: shipping 014-fix-2 took /inbox + /ops down until `make migrate`.)
    """
    scheduler: BackgroundScheduler = start_scheduler()
    app.state.scheduler = scheduler
    app.state.started_at = datetime.now(UTC)

    from apfun.db import SessionLocal
    from apfun.scheduler.pause_state import is_scheduler_paused

    try:
        with SessionLocal() as session:
            paused = is_scheduler_paused(session)
    except Exception:  # noqa: BLE001 — startup must survive a not-yet-migrated DB
        logger.warning(
            "runtime_state unavailable at startup (migration pending?); starting un-paused",
            exc_info=True,
        )
        paused = False
    if paused:
        scheduler.pause()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="apfun", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
app.include_router(web_router)

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Root — meta-redirects to /inbox (the live admin landing page)."""
    return templates.TemplateResponse(request, "index.html", {"active": None})


def main() -> None:
    """Run a production-shaped server. Dev uses `uvicorn ... --reload` directly."""
    import uvicorn

    uvicorn.run("apfun.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
