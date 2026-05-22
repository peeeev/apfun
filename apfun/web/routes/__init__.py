"""Web routers — assembled and mounted from `apfun.main`."""

from fastapi import APIRouter

from apfun.web.routes import health, inbox

router = APIRouter()
router.include_router(health.router)
router.include_router(inbox.router)


__all__ = ["router"]
