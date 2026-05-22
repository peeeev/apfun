"""`/healthz` — liveness + scheduler health.

Returns `{"ok": True, "scheduler": {"running": ...}}`. The `scheduler` key
lets the operator (and future status panels in task 021) confirm that the
background scheduler started under the FastAPI lifespan handler.

Per `docs/tasks/012-scheduler.md` → Acceptance: `scheduler.running == true`.
"""

from typing import Any

from fastapi import APIRouter
from starlette.requests import Request

router = APIRouter()


@router.get("/healthz")
def healthz(request: Request) -> dict[str, Any]:
    scheduler = getattr(request.app.state, "scheduler", None)
    running = bool(scheduler.running) if scheduler is not None else False
    return {"ok": True, "scheduler": {"running": running}}
