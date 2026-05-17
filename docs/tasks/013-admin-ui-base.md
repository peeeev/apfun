# 013 — Admin UI scaffolding

**Goal:** Server-rendered HTMX + Jinja + Tailwind base, with shared layout, navigation, and dark-mode-default styling. No app auth.

**Complexity:** M

Depends on: 001.

## Deliverables
- Tailwind via the standalone CLI binary (no Node). `scripts/build_css.sh` watches `apfun/web/static/src.css` → `apfun/web/static/app.css`.
- `apfun/web/templates/_base.html`: page chrome, nav (Inbox / Opportunities / Sources / Projects), HTMX CDN script (pin a version), `app.css`.
- `apfun/web/templates/index.html` redirecting to `/inbox` (placeholder until task 014).
- `apfun/web/routes/__init__.py` mounting routers.
- `apfun/web/routes/health.py` — moves `/healthz` here.
- `apfun/main.py` mounts the web router and serves static files at `/static`.
- Dense info layout: monospace headings, tight line-height, no animations beyond HTMX's built-in opacity swap.
- Document that Apache strips/proxies basic auth; the app should NOT look at `Authorization` headers.

## Acceptance
- `GET /` → 200 with the base layout rendered.
- `GET /static/app.css` returns the built CSS.
- `ruff` / `pyright` clean.

## Notes
- HTMX over websockets is overkill here; default polling for sources health (task 021) is fine.
- Pin HTMX to a specific version on a CDN with SRI; document the upgrade path in CLAUDE.md if updated.
