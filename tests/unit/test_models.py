"""Happy-path object graph: source → raw_signal → candidate → candidate_signals."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import Candidate, CandidateSignal, Decision, PipelineStage, RawSignal, Source


def test_object_graph(session: Session) -> None:
    src = Source(kind="reddit", name="r/SaaS", config_json={"subreddits": ["SaaS"]})
    session.add(src)
    session.flush()

    sig = RawSignal(
        source_id=src.id,
        external_id="t3_abc123",
        url="https://reddit.com/r/SaaS/comments/abc123",
        captured_at=datetime.now(UTC),
        content_hash="hash-abc123",
        payload_json={"title": "what tool do you wish existed"},
    )
    session.add(sig)
    session.flush()

    cand = Candidate(
        problem_statement="founders waste time wiring up Stripe billing",
        suspected_user="solo SaaS founders",
        seed_keywords_json=["stripe billing setup", "saas billing"],
        vertical="dev_tools",
        dedup_key="stripe-billing-setup-saas",
    )
    session.add(cand)
    session.flush()

    session.add(CandidateSignal(candidate_id=cand.id, raw_signal_id=sig.id))
    session.commit()

    # Defaults — see CLAUDE.md → Lessons learned: decision and pipeline_stage are independent.
    assert cand.decision == Decision.PENDING
    assert cand.pipeline_stage == PipelineStage.NONE

    # Read back via fresh query.
    fetched = session.execute(select(Candidate).where(Candidate.id == cand.id)).scalar_one()
    assert fetched.problem_statement.startswith("founders waste time")
    assert fetched.seed_keywords_json == ["stripe billing setup", "saas billing"]

    links = (
        session.execute(select(CandidateSignal).where(CandidateSignal.candidate_id == cand.id))
        .scalars()
        .all()
    )
    assert len(links) == 1
    assert links[0].raw_signal_id == sig.id
