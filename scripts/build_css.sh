#!/usr/bin/env bash
# Build apfun/web/static/app.css from src.css using the Tailwind standalone CLI.
#
# Documented build path per task 013. Today `app.css` is committed by hand
# (subset of utilities used by the templates) — this script is the planned
# workflow for when the CSS surface grows enough that hand-curation stops
# scaling.
#
# Usage:
#   scripts/build_css.sh           # one-shot build
#   scripts/build_css.sh --watch   # watch mode for dev
#
# Per `docs/tasks/013-admin-ui-base.md`. The standalone Tailwind CLI binary
# (no Node) is downloaded on first run if missing.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAILWIND_BIN="${REPO_ROOT}/.tailwindcss"
TAILWIND_URL="${TAILWIND_URL:-https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64}"

if [[ ! -x "${TAILWIND_BIN}" ]]; then
    echo "Downloading Tailwind standalone CLI..." >&2
    curl -sSLo "${TAILWIND_BIN}" "${TAILWIND_URL}"
    chmod +x "${TAILWIND_BIN}"
fi

WATCH_ARG=""
if [[ "${1:-}" == "--watch" ]]; then
    WATCH_ARG="--watch"
fi

cd "${REPO_ROOT}"
"${TAILWIND_BIN}" \
    -i apfun/web/static/src.css \
    -o apfun/web/static/app.css \
    --minify \
    ${WATCH_ARG}
