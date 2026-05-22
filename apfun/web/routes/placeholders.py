"""Nav-link placeholder routes.

The base nav (in `_base.html`) links to four sections; only `/inbox` is real
in v1. The other three are wired here as friendly placeholders so the UI
doesn't 404 when an operator clicks around. Each renders `_placeholder.html`
with a heading + a "Coming in task NNN" pointer.

Per orchestrator feedback 019 Q5: bundled as a chore PR alongside task 012;
not pre-committing tasks 020/021's actual designs.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("/opportunities", response_class=HTMLResponse)
def opportunities(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_placeholder.html",
        {
            "active": "opportunities",
            "heading": "Opportunities",
            "tagline": "Scored + synthesized opportunities surface here after Stage 5.",
            "task_id": "020",
        },
    )


@router.get("/sources", response_class=HTMLResponse)
def sources(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_placeholder.html",
        {
            "active": "sources",
            "heading": "Sources",
            "tagline": "Per-source health, last-fetch timestamps, consecutive-failure counts.",
            "task_id": "021",
        },
    )


@router.get("/projects", response_class=HTMLResponse)
def projects(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_placeholder.html",
        {
            "active": "projects",
            "heading": "Projects",
            "tagline": "Approved opportunities promoted to build projects appear here.",
            "task_id": "021",
        },
    )
