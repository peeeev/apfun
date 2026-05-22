"""FastAPI app entrypoint. See CLAUDE.md → Networking: bind to 0.0.0.0:4000.

Apache strips/proxies basic-auth at the edge — this app does NOT look at
`Authorization` headers. Per `docs/tasks/013-admin-ui-base.md`.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from apfun.config import settings
from apfun.web.routes import router as web_router

_STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "web" / "templates"

app = FastAPI(title="apfun")
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
