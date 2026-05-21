"""Integration test for Stage 1 clustering — real Anthropic calls.

Marked @pytest.mark.integration so `make test` skips by default; run via
`make test-all`. Requires APFUN_ANTHROPIC_API_KEY and incurs Opus + Haiku
charges (~$0.30-$1.00 per run depending on bucket count).

Per orchestrator feedback 016 risk profile: this is one of three "judgment"
stages. Spot-check outputs by eye after running, not just assertion counts.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.llm.client import LLMClient
from apfun.models import Candidate, CandidateSignal, RawSignal, SignalText, Source
from apfun.pipeline.cluster import cluster_signals

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("APFUN_ANTHROPIC_API_KEY"),
        reason="APFUN_ANTHROPIC_API_KEY not set",
    ),
]


# Hand-crafted "signals" representing what real ingest output looks like —
# focused enough that clustering should produce 1-2 cards from this batch.
_FIXTURE_SIGNALS: list[tuple[str, float]] = [
    (
        "Stripe billing is fine for cards but proration with mid-cycle changes is a nightmare. "
        "Our customers got double-charged this month and I can't tell why from Stripe's logs.",
        12.0,
    ),
    (
        "Dunning emails through Stripe Customer Portal are bare-bones. We had 30 customers in "
        "failed-payment state and Stripe just sent the same form letter; lost half of them.",
        18.0,
    ),
    (
        "Anyone else find proration confusing? Customer downgraded mid-month and the next "
        "invoice numbers don't match what they expected. Six hours debugging.",
        7.0,
    ),
    (
        "Show HN: tiny observability stack for indie SaaS — one binary, SQLite-backed, "
        "drop-in for metrics + traces + logs. Built it because Datadog is way too much.",
        25.0,
    ),
    (
        "Datadog pricing for a one-person SaaS is wild. I just want process CPU + req latency "
        "for a single VM; their starter tier is $15/host with seven hidden add-ons.",
        14.0,
    ),
]


def test_cluster_on_real_anthropic_yields_candidates(session: Session) -> None:
    """End-to-end: seed 5 fixture signals, run cluster_signals against a real
    LLMClient, assert ≥1 candidate landed with sensible shape."""
    src = Source(kind="reddit", name="r/SaaS-live-smoke", config_json={})
    session.add(src)
    session.flush()

    for i, (text, weight) in enumerate(_FIXTURE_SIGNALS):
        raw = RawSignal(
            source_id=src.id,
            external_id=f"live-{i}",
            url=f"https://example.com/{i}",
            captured_at=datetime.now(UTC),
            content_hash=f"hash-{i}",
            payload_json={"text": text},
        )
        session.add(raw)
        session.flush()
        st = SignalText(
            raw_signal_id=raw.id,
            source_kind="reddit",
            text=text,
            social_proof_weight=weight,
            is_low_signal=False,
            extracted_at=datetime.now(UTC),
        )
        session.add(st)
    session.commit()

    client = LLMClient()
    result = cluster_signals(session, llm_client=client)
    session.commit()

    assert result.processed_signals == len(_FIXTURE_SIGNALS), (
        f"expected all {len(_FIXTURE_SIGNALS)} signals processed; got {result.processed_signals}"
    )
    assert result.candidates_inserted >= 1, (
        f"expected ≥1 candidate from this batch; got {result.candidates_inserted} "
        f"(buckets={result.buckets})"
    )

    candidates = session.execute(select(Candidate)).scalars().all()
    assert len(candidates) >= 1
    for c in candidates:
        assert c.problem_statement
        assert c.dedup_key
        # Every candidate must have at least one linked signal.
        links = (
            session.execute(select(CandidateSignal).where(CandidateSignal.candidate_id == c.id))
            .scalars()
            .all()
        )
        assert len(links) >= 1, f"candidate {c.id} has no linked signals"
