#!/usr/bin/env bash
# Post-rebuild bootstrap for the apfun dev container. Idempotent.
#
# Run after `docker compose up -d --build` rebuilds the container, since:
#   1. `sqlite3` (CLI) is not in the host Dockerfile yet — installs it.
#   2. `gh auth` state lives outside the container image — checks it.
#
# Per orchestrator feedback 019 Q2: the `sqlite3` apt entry will get bundled
# into the next natural Dockerfile edit (likely a task 011 dep bump). Until
# then this script absorbs the friction. The `gh auth` check never belongs
# in the Dockerfile (interactive + stateful).
#
# Usage (from the host):
#   docker exec -it apfun-funnel /workspace/scripts/post-rebuild-bootstrap.sh

set -euo pipefail

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "Installing sqlite3 CLI..."
    sudo apt-get update -qq
    sudo apt-get install -y --no-install-recommends sqlite3
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "GitHub auth missing. Run:"
    echo "  gh auth login --hostname github.com --git-protocol https --web"
    echo "  gh auth setup-git --hostname github.com"
    exit 1
fi

echo "Post-rebuild bootstrap: OK"
