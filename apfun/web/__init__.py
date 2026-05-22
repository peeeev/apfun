"""Server-rendered HTMX + Jinja + Tailwind admin UI.

No JS framework, no Node toolchain. Tailwind via the standalone CLI binary
(see `scripts/build_css.sh`). HTMX pinned + SRI'd via CDN. Apache strips
basic-auth headers at the edge — this app does not look at `Authorization`.

Per `docs/tasks/013-admin-ui-base.md`.
"""
