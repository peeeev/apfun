"""Web routers — assembled and mounted from `apfun.main`."""

from fastapi import APIRouter

from apfun.web.routes import health, inbox, ops, placeholders

router = APIRouter()
router.include_router(health.router)
router.include_router(inbox.router)
router.include_router(ops.router)
router.include_router(placeholders.router)


__all__ = ["router"]
