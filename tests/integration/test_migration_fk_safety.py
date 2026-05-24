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

_SEED = """
from datetime import UTC, datetime
from apfun.db import SessionLocal
from apfun.models import (
    Source, RawSignal, Candidate, CandidateSignal, Approval,
    ApprovalDecision, Decision, PipelineStage,
)
with SessionLocal() as s:
    src = Source(kind="hn", name="hn:x", config_json={}); s.add(src); s.flush()
    raw = RawSignal(source_id=src.id, external_id="e1", url="u",
                    captured_at=datetime.now(UTC), content_hash="h1", payload_json={})
    s.add(raw); s.flush()
    c = Candidate(problem_statement="p", seed_keywords_json=[], dedup_key="k1",
                  decision=Decision.PENDING, pipeline_stage=PipelineStage.NONE)
    s.add(c); s.flush()
    s.add(CandidateSignal(candidate_id=c.id, raw_signal_id=raw.id))
    s.add(Approval(candidate_id=c.id, decision=ApprovalDecision.APPROVE,
                   comment="note", decided_at=datetime.now(UTC)))
    s.commit()
"""

_ASSERT = """
from sqlalchemy import func, select
from apfun.db import SessionLocal
from apfun.models import CandidateSignal, Approval
with SessionLocal() as s:
    cs = s.execute(select(func.count()).select_from(CandidateSignal)).scalar_one()
    ap = s.execute(select(func.count()).select_from(Approval)).scalar_one()
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
