"""Data-preservation test for the buildability migration (4e8f1a2b9c3d).

Per the migration data-preservation discipline (CLAUDE.md / feedback 029 Q1): a
batch (table-recreate) migration on a table with cascade/set-null children must
be proven to preserve those children on a SEEDED db, not just an empty one.

`4e8f1a2b9c3d` adds three columns to `candidates` via `op.batch_alter_table`,
which recreates the table. `candidates` is referenced by:
  - `candidate_signals` (ON DELETE CASCADE)
  - `approvals`         (ON DELETE CASCADE)
  - `llm_runs.candidate_id` (ON DELETE SET NULL)

This test seeds a parent + all three child kinds, runs the migration, and
asserts (1) the parent survives, (2) both cascade children survive, and (3) the
set-null FK (`llm_runs.candidate_id`) is preserved — the migration must not
nuke or null any of them. `migrations/env.py`'s `foreign_keys=OFF` is what makes
the recreate safe; this test is the proof.

Shells out to the alembic CLI (env.py executes on import, so it can't run
in-process), hence the `integration` marker — `make test` skips it,
`make test-all` runs it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[2]
# down_revision of the buildability migration — the batch recreate under test.
_PRE_BUILDABILITY = "7f3a9c2e1d04"

# Seed/assert via raw sqlite3, NOT the ORM. A migration test asserts behavior of
# the SQL schema at a past revision; the live ORM model floats ahead of it (it
# already has the buildability columns this migration adds), so an ORM INSERT
# would emit columns the pre-migration schema lacks. Raw SQL pins exactly the
# columns that exist at `_PRE_BUILDABILITY`.
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
cur.execute("INSERT INTO llm_runs"
            " (task,model,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,"
            "  latency_ms,est_cost_usd,ok,attempts,candidate_id,created_at,updated_at)"
            " VALUES ('cluster','claude-opus-4-7',0,0,0,0,0,0.0,1,1,?,?,?)", (cid, now, now))
con.commit(); con.close()
"""

_ASSERT = """
import os, sqlite3
path = os.environ["APFUN_DB_URL"].replace("sqlite:///", "", 1)
con = sqlite3.connect(path)
cand = con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
cs = con.execute("SELECT COUNT(*) FROM candidate_signals").fetchone()[0]
ap = con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
linked = con.execute("SELECT COUNT(*) FROM llm_runs WHERE candidate_id IS NOT NULL").fetchone()[0]
con.close()
assert cand == 1, f"candidates wiped: {cand}"
assert cs == 1, f"candidate_signals wiped: {cs}"
assert ap == 1, f"approvals wiped: {ap}"
assert linked == 1, f"llm_runs.candidate_id nulled by recreate: {linked}"
"""


def test_buildability_migration_preserves_child_rows(tmp_path: Path) -> None:
    db = tmp_path / "buildability_fk_safety.db"
    env = {**os.environ, "APFUN_DB_URL": f"sqlite:///{db}"}

    def run(*args: str) -> None:
        subprocess.run(args, check=True, env=env, cwd=_REPO, capture_output=True)

    # Build schema up to just before the buildability migration, seed
    # parent + children + a candidate-linked llm_run, apply the migration,
    # then assert every row (and the set-null FK) survived the recreate.
    run("uv", "run", "alembic", "upgrade", _PRE_BUILDABILITY)
    run("uv", "run", "python", "-c", _SEED)
    run("uv", "run", "alembic", "upgrade", "head")
    run("uv", "run", "python", "-c", _ASSERT)
