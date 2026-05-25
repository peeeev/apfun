"""Unit tests for `apfun.pipeline.merge.merge_candidates` (task 014-fix-2).

The LLMClient is stubbed. Covers: validation (>=2, exists, not-already-merged),
the single-transaction persist, signal re-linking (incl. shared-signal dedupe),
soft-deletion + decision preservation, and the merged candidate's pending state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apfun.models import (
    Buildability,
    Candidate,
    CandidateSignal,
    Decision,
    PipelineStage,
    RawSignal,
    SignalText,
    Source,
)
from apfun.pipeline.merge import MergedCard, MergeError, merge_candidates


def _make_source(session: Session, kind: str = "hn", name: str = "hn:x") -> Source:
    s = Source(kind=kind, name=name, config_json={})
    session.add(s)
    session.flush()
    return s


def _make_signal(session: Session, source: Source, *, weight: float, ext: str) -> int:
    raw = RawSignal(
        source_id=source.id,
        external_id=ext,
        url="https://example.com",
        captured_at=datetime.now(UTC),
        content_hash=f"h-{ext}",
        payload_json={"text": ext},
    )
    session.add(raw)
    session.flush()
    session.add(
        SignalText(
            raw_signal_id=raw.id,
            source_kind=source.kind,
            text=f"signal {ext}",
            social_proof_weight=weight,
            is_low_signal=False,
            extracted_at=datetime.now(UTC),
        )
    )
    session.flush()
    return raw.id


def _make_candidate(
    session: Session,
    *,
    problem: str,
    dedup_key: str,
    decision: Decision = Decision.PENDING,
    signal_ids: list[int] | None = None,
) -> int:
    c = Candidate(
        problem_statement=problem,
        seed_keywords_json=["k"],
        dedup_key=dedup_key,
        decision=decision,
        pipeline_stage=PipelineStage.NONE,
        buildability=Buildability.HIGH,
        buildability_rationale="orig",
    )
    session.add(c)
    session.flush()
    for sid in signal_ids or []:
        session.add(CandidateSignal(candidate_id=c.id, raw_signal_id=sid))
    session.flush()
    return c.id


class _StubLLM:
    """Returns a fixed MergedCard for the single judge_json("merge") call."""

    def __init__(self, merged: MergedCard) -> None:
        self._merged = merged
        self.calls = 0

    def judge_json(
        self,
        task: str,  # noqa: ARG002
        system: str,  # noqa: ARG002
        messages: list[dict[str, Any]],  # noqa: ARG002
        *,
        schema: type[Any],  # noqa: ARG002
        **kwargs: Any,
    ) -> MergedCard:
        self.calls += 1
        return self._merged


_MERGED = MergedCard(
    problem_statement="Unified problem",
    suspected_user="founders",
    seed_keywords=["a", "b"],
    buildability=Buildability.MEDIUM,
    buildability_rationale="merged rationale",
)


# ───────────────────────────── validation ─────────────────────────────


def test_merge_requires_two_distinct(session: Session) -> None:
    cid = _make_candidate(session, problem="A", dedup_key="a", signal_ids=[])
    session.commit()
    with pytest.raises(MergeError, match="at least 2"):
        merge_candidates(session, llm_client=_StubLLM(_MERGED), candidate_ids=[cid, cid])


def test_merge_rejects_missing_candidate(session: Session) -> None:
    cid = _make_candidate(session, problem="A", dedup_key="a")
    session.commit()
    with pytest.raises(MergeError, match="not found"):
        merge_candidates(session, llm_client=_StubLLM(_MERGED), candidate_ids=[cid, 99999])


def test_merge_rejects_already_merged(session: Session) -> None:
    a = _make_candidate(session, problem="A", dedup_key="a")
    b = _make_candidate(session, problem="B", dedup_key="b")
    # Mark `a` already soft-deleted.
    session.get(Candidate, a).merged_into_id = b  # type: ignore[union-attr]
    session.commit()
    with pytest.raises(MergeError, match="already-merged"):
        merge_candidates(session, llm_client=_StubLLM(_MERGED), candidate_ids=[a, b])


# ───────────────────────── happy path + persistence ───────────────────


def test_merge_creates_pending_candidate_and_soft_deletes_sources(session: Session) -> None:
    src = _make_source(session)
    s1 = _make_signal(session, src, weight=10, ext="s1")
    s2 = _make_signal(session, src, weight=4, ext="s2")
    a = _make_candidate(session, problem="A", dedup_key="a", signal_ids=[s1])
    b = _make_candidate(session, problem="B", dedup_key="b", signal_ids=[s2])
    session.commit()

    stub = _StubLLM(_MERGED)
    new = merge_candidates(session, llm_client=stub, candidate_ids=[a, b])
    session.commit()

    assert stub.calls == 1
    # New candidate: pending, carries the merged fields.
    assert new.decision == Decision.PENDING
    assert new.problem_statement == "Unified problem"
    assert new.buildability == Buildability.MEDIUM
    assert new.merged_into_id is None

    # Sources soft-deleted (point at the new candidate).
    for src_id in (a, b):
        refreshed = session.get(Candidate, src_id)
        assert refreshed is not None
        assert refreshed.merged_into_id == new.id

    # Signals re-linked to the new candidate; none left on the sources.
    new_links = (
        session.execute(
            select(CandidateSignal.raw_signal_id).where(CandidateSignal.candidate_id == new.id)
        )
        .scalars()
        .all()
    )
    assert set(new_links) == {s1, s2}
    old_links = session.execute(
        select(func.count())
        .select_from(CandidateSignal)
        .where(CandidateSignal.candidate_id.in_([a, b]))
    ).scalar_one()
    assert old_links == 0


def test_merge_dedupes_shared_signal(session: Session) -> None:
    """A signal linked to two merged candidates yields exactly one link on the
    new candidate (composite-PK-safe) — weight isn't double-counted."""
    src = _make_source(session)
    shared = _make_signal(session, src, weight=7, ext="shared")
    a = _make_candidate(session, problem="A", dedup_key="a", signal_ids=[shared])
    b = _make_candidate(session, problem="B", dedup_key="b", signal_ids=[shared])
    session.commit()

    new = merge_candidates(session, llm_client=_StubLLM(_MERGED), candidate_ids=[a, b])
    session.commit()

    links = (
        session.execute(
            select(CandidateSignal.raw_signal_id).where(CandidateSignal.candidate_id == new.id)
        )
        .scalars()
        .all()
    )
    assert links == [shared], "shared signal linked exactly once"


def test_merge_weight_is_sum_of_distinct_signal_weights(session: Session) -> None:
    src = _make_source(session)
    s1 = _make_signal(session, src, weight=10, ext="s1")
    s2 = _make_signal(session, src, weight=4, ext="s2")
    a = _make_candidate(session, problem="A", dedup_key="a", signal_ids=[s1])
    b = _make_candidate(session, problem="B", dedup_key="b", signal_ids=[s2])
    session.commit()

    new = merge_candidates(session, llm_client=_StubLLM(_MERGED), candidate_ids=[a, b])
    session.commit()

    weight = session.execute(
        select(func.coalesce(func.sum(SignalText.social_proof_weight), 0.0))
        .join(CandidateSignal, CandidateSignal.raw_signal_id == SignalText.raw_signal_id)
        .where(CandidateSignal.candidate_id == new.id)
    ).scalar_one()
    assert weight == 14.0


def test_merge_preserves_rejected_source_decision(session: Session) -> None:
    """Merging a rejected candidate keeps its rejection on the soft-deleted row;
    the new merged candidate is pending. No silent decision flip."""
    src = _make_source(session)
    s1 = _make_signal(session, src, weight=3, ext="s1")
    rejected = _make_candidate(
        session, problem="R", dedup_key="r", decision=Decision.REJECTED, signal_ids=[s1]
    )
    pending = _make_candidate(session, problem="P", dedup_key="p")
    session.commit()

    new = merge_candidates(session, llm_client=_StubLLM(_MERGED), candidate_ids=[rejected, pending])
    session.commit()

    refreshed_rejected = session.get(Candidate, rejected)
    assert refreshed_rejected is not None
    assert refreshed_rejected.decision == Decision.REJECTED, "soft-deleted source keeps rejection"
    assert refreshed_rejected.merged_into_id == new.id
    assert new.decision == Decision.PENDING
