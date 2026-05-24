#!/usr/bin/env bash
# Snapshot the live SQLite DB before a migration. Per orchestrator feedback 029
# (post-incident backup discipline). Run this BEFORE every `alembic upgrade`.
#
# Snapshots land in data/backups/ (gitignored), named with the current alembic
# revision + a UTC timestamp. The most recent 10 are kept; older ones pruned.
#
# Uses SQLite's online backup API (not `cp`) so the copy is consistent even
# with WAL frames in flight.
#
# Usage:
#   bash scripts/db_snapshot.sh            # snapshots data/apfun.db
#   bash scripts/db_snapshot.sh /path/to/other.db

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="${1:-$REPO_ROOT/data/apfun.db}"
BACKUP_DIR="$REPO_ROOT/data/backups"
KEEP=10

if [[ ! -f "$DB" ]]; then
    echo "No DB at $DB — nothing to snapshot." >&2
    exit 0
fi

mkdir -p "$BACKUP_DIR"

# Current alembic revision for the filename (best-effort; stdlib sqlite3, no venv).
rev="$(python3 - "$DB" <<'PY' 2>/dev/null || echo norev
import sqlite3, sys
try:
    row = sqlite3.connect(sys.argv[1]).execute(
        "SELECT version_num FROM alembic_version"
    ).fetchone()
    print(row[0] if row else "norev")
except Exception:
    print("norev")
PY
)"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
dest="$BACKUP_DIR/apfun-${rev}-${ts}.db"

# Consistent online backup (WAL-safe) — NOT `cp`, which can capture an
# inconsistent file mid-WAL.
python3 - "$DB" "$dest" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
src.close()
dst.close()
PY

echo "snapshot: $dest ($(du -h "$dest" | cut -f1))"

# Prune: keep the most recent $KEEP snapshots, drop the rest.
mapfile -t old < <(ls -1t "$BACKUP_DIR"/apfun-*.db 2>/dev/null | tail -n +$((KEEP + 1)))
if ((${#old[@]})); then
    printf '%s\n' "${old[@]}" | xargs -r rm -f
    echo "pruned ${#old[@]} old snapshot(s), keeping newest $KEEP"
fi
