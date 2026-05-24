"""Regression: batch (table-recreate) migrations must NOT cascade-wipe children.

Tuition for this test: migration `7f3a9c2e1d04` (a `render_as_batch` CHECK-
constraint rewrite on `candidates`) ran against the live DB with foreign keys
enforced. SQLite's batch recreate does CREATE-new → copy → DROP-old → rename,
and the implicit DELETE-before-DROP fired `ON DELETE CASCADE` on `candidate_signals`
and `approvals`, silently destroying every row. `migrations/env.py` now runs
migrations through a dedicated engine with `PRAGMA foreign_keys=OFF`.

This test seeds a parent (`candidates`) + children (`candidate_signals`,
`approvals`), runs the unsure migration, and asserts the children survive.
Marked integration: it shells out to the alembic CLI against a temp DB (env.py
executes on import, so it can't be imported in-process), so `make test` skips
it; run via `make test-all`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[2]
# down_revision of the unsure migration — the batch recreate under test.
_PRE_UNSURE = "16b3688378b5"

# Seed/assert via raw sqlite3, NOT the ORM. The live ORM model floats ahead of
# this past revision (it has columns later migrations add, e.g. buildability), so
# an ORM INSERT would emit columns the pre-`_PRE_UNSURE` schema lacks. Raw SQL
# pins exactly the columns that exist at the seed revision.
_SEED = """
import os, sqlite3
from datetime import UTC, datetime
path = os.environ["APFUN_DB_URL"].replace("sqlite:///", "", 1)
now = datetime.now(UTC).isoformat()
con = sqlite3.connect(path); cur = con.cursor()
cur.execute("INSERT INTO sources (kind,name,config_json,is_active,created_at,updated_at)"
            " VALUES ('hn','hn:x','{}',1,?,?)", (now, now))
sid = cur.lastrowid
cur.execute(
"INSERT INTO raw_signals"
" (source_id,external_id,url,captured_at,content_hash,payload_json,created_at,updated_at)"
" VALUES (?,?,?,?,?,?,?,?)", (sid, "e1", "u", now, "h1", "{}", now, now))
rid = cur.lastrowid
cur.execute(
"INSERT INTO candidates"
" (problem_statement,seed_keywords_json,dedup_key,decision,pipeline_stage,created_at,updated_at)"
" VALUES ('p','[]','k1','pending','none',?,?)", (now, now))
cid = cur.lastrowid
cur.execute("INSERT INTO candidate_signals (candidate_id,raw_signal_id,created_at)"
            " VALUES (?,?,?)", (cid, rid, now))
cur.execute("INSERT INTO approvals (candidate_id,decision,decided_at,created_at,updated_at)"
            " VALUES (?,'approve',?,?,?)", (cid, now, now, now))
con.commit(); con.close()
"""

_ASSERT = """
import os, sqlite3
path = os.environ["APFUN_DB_URL"].replace("sqlite:///", "", 1)
con = sqlite3.connect(path)
cs = con.execute("SELECT COUNT(*) FROM candidate_signals").fetchone()[0]
ap = con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
con.close()
assert cs == 1, f"candidate_signals wiped: {cs}"
assert ap == 1, f"approvals wiped: {ap}"
"""


def test_batch_migration_preserves_child_rows(tmp_path: Path) -> None:
    db = tmp_path / "fk_safety.db"
    env = {**os.environ, "APFUN_DB_URL": f"sqlite:///{db}"}

    def run(*args: str) -> None:
        subprocess.run(args, check=True, env=env, cwd=_REPO, capture_output=True)

    # Build schema up to just before the unsure migration, seed parent+children,
    # apply the batch migration, then assert the children survived the recreate.
    run("uv", "run", "alembic", "upgrade", _PRE_UNSURE)
    run("uv", "run", "python", "-c", _SEED)
    run("uv", "run", "alembic", "upgrade", "head")
    run("uv", "run", "python", "-c", _ASSERT)
