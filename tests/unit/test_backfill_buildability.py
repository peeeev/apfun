"""Unit tests for `scripts.backfill_buildability.backfill`.

The LLMClient is stubbed (and optionally logs an `llm_runs` row per call so the
cost-accounting + budget-abort paths are exercised without the network).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import Buildability, Candidate, Decision, LLMRun, PipelineStage
from apfun.pipeline.cluster import BuildabilityAssessment
from scripts.backfill_buildability import backfill


def _make_candidate(session: Session, *, dedup_key: str, buildability: Buildability | None) -> int:
    c = Candidate(
        problem_statement=f"problem {dedup_key}",
        seed_keywords_json=["k"],
        dedup_key=dedup_key,
        decision=Decision.PENDING,
        pipeline_stage=PipelineStage.NONE,
        buildability=buildability,
        buildability_rationale="" if buildability is None else "preexisting",
    )
    session.add(c)
    session.flush()
    return c.id


class _StubLLM:
    """Returns scripted assessments; optionally logs an llm_runs row per call so
    the backfill's cost query sees a real (test-controlled) cost."""

    def __init__(
        self,
        session: Session,
        responses: list[BuildabilityAssessment],
        *,
        cost_per_call: float = 0.0,
    ) -> None:
        self._session = session
        self._responses = list(responses)
        self._cost = cost_per_call
        self.calls = 0

    def judge_json(
        self,
        task: str,
        system: str,  # noqa: ARG002
        messages: list[dict[str, Any]],  # noqa: ARG002
        *,
        schema: type[Any],  # noqa: ARG002
        candidate_id: int | None = None,
    ) -> BuildabilityAssessment:
        self.calls += 1
        if self._cost:
            self._session.add(
                LLMRun(
                    task=task,
                    model="claude-opus-4-7",
                    ok=True,
                    est_cost_usd=self._cost,
                    candidate_id=candidate_id,
                )
            )
            self._session.flush()
        if not self._responses:
            raise RuntimeError("stub: out of responses")
        return self._responses.pop(0)


def test_backfill_assesses_only_unassessed(session: Session) -> None:
    done = _make_candidate(session, dedup_key="done", buildability=Buildability.HIGH)
    todo = _make_candidate(session, dedup_key="todo", buildability=None)
    session.commit()

    stub = _StubLLM(
        session,
        [BuildabilityAssessment(buildability=Buildability.LOW, buildability_rationale="r")],
    )
    report = backfill(session, stub)

    assert stub.calls == 1, "only the unassessed candidate is sent to Opus"
    assert report.assessed == 1
    assert report.counts["low"] == 1

    refreshed_todo = session.get(Candidate, todo)
    assert refreshed_todo is not None
    assert refreshed_todo.buildability == Buildability.LOW
    assert refreshed_todo.buildability_assessed_at is not None
    # The already-assessed candidate is untouched.
    refreshed_done = session.get(Candidate, done)
    assert refreshed_done is not None
    assert refreshed_done.buildability == Buildability.HIGH
    assert refreshed_done.buildability_rationale == "preexisting"


def test_backfill_is_idempotent(session: Session) -> None:
    _make_candidate(session, dedup_key="c1", buildability=None)
    session.commit()

    stub = _StubLLM(
        session,
        [BuildabilityAssessment(buildability=Buildability.MEDIUM, buildability_rationale="r")],
    )
    first = backfill(session, stub)
    assert first.assessed == 1

    # Second run: nothing left to assess → no LLM calls.
    stub2 = _StubLLM(session, [])
    second = backfill(session, stub2)
    assert second.assessed == 0
    assert stub2.calls == 0


def test_backfill_reports_cost(session: Session) -> None:
    _make_candidate(session, dedup_key="a", buildability=None)
    _make_candidate(session, dedup_key="b", buildability=None)
    session.commit()

    stub = _StubLLM(
        session,
        [
            BuildabilityAssessment(buildability=Buildability.HIGH, buildability_rationale="r"),
            BuildabilityAssessment(buildability=Buildability.HIGH, buildability_rationale="r"),
        ],
        cost_per_call=0.1,
    )
    report = backfill(session, stub)
    assert report.assessed == 2
    assert report.cost_usd == 0.2  # two logged buildability runs at $0.10


def test_backfill_aborts_over_budget(session: Session) -> None:
    _make_candidate(session, dedup_key="a", buildability=None)
    _make_candidate(session, dedup_key="b", buildability=None)
    session.commit()

    stub = _StubLLM(
        session,
        [
            BuildabilityAssessment(buildability=Buildability.HIGH, buildability_rationale="r"),
            BuildabilityAssessment(buildability=Buildability.HIGH, buildability_rationale="r"),
        ],
        cost_per_call=0.1,
    )
    # Budget below one call's cost → abort after the first candidate.
    report = backfill(session, stub, budget=0.05)
    assert report.aborted is True
    assert report.assessed == 1
    assert stub.calls == 1, "should stop before assessing the second candidate"

    # The unassessed candidate remains NULL — a resume run picks it up.
    remaining = (
        session.execute(select(Candidate).where(Candidate.buildability.is_(None))).scalars().all()
    )
    assert len(remaining) == 1


def test_backfill_respects_limit(session: Session) -> None:
    for i in range(3):
        _make_candidate(session, dedup_key=f"c{i}", buildability=None)
    session.commit()

    stub = _StubLLM(
        session,
        [BuildabilityAssessment(buildability=Buildability.HIGH, buildability_rationale="r")],
    )
    report = backfill(session, stub, limit=1)
    assert report.assessed == 1
    assert stub.calls == 1
