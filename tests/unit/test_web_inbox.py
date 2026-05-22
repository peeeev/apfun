"""Tests for the inbox endpoint (task 014):

- GET /inbox lists pending candidates ordered by composite signal weight
- POST /inbox/<id>/approve writes an Approval + flips decision to APPROVED
- POST /inbox/<id>/reject writes an Approval + flips decision to REJECTED
- signals_since_rejection surfaces in the UI for rejected candidates that
  have accumulated new candidate_signals.created_at > approvals.decided_at
- HITL durability: re-approving a rejected candidate writes a new Approval
  row but does NOT auto-flip (operator's explicit action does)
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from apfun.models import (
    Approval,
    ApprovalDecision,
    Candidate,
    CandidateSignal,
    Decision,
    PipelineStage,
    RawSignal,
    SignalText,
    Source,
)


@pytest.fixture
def client_with_session(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, sessionmaker]]:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("apfun.db.SessionLocal", factory)
    monkeypatch.setattr("apfun.web.routes.inbox.SessionLocal", factory)

    from apfun.main import app

    with TestClient(app) as c:
        yield c, factory


def _seed_candidate(
    session: Session,
    *,
    problem: str = "Something is broken",
    weight: float = 5.0,
    decision: Decision = Decision.PENDING,
    n_signals: int = 1,
) -> Candidate:
    """Create a Candidate with `n_signals` linked signal_text rows."""
    src = Source(kind="hn", name=f"hn:{problem[:20]}", config_json={})
    session.add(src)
    session.flush()
    cand = Candidate(
        problem_statement=problem,
        suspected_user="founders",
        seed_keywords_json=["alpha", "beta"],
        vertical="dev-tools",
        dedup_key=f"slug-{problem[:30].lower().replace(' ', '-')}-{id(problem)}",
        decision=decision,
        pipeline_stage=PipelineStage.NONE,
    )
    session.add(cand)
    session.flush()
    for i in range(n_signals):
        raw = RawSignal(
            source_id=src.id,
            external_id=f"ext-{problem[:10]}-{i}",
            url=f"https://example.com/{i}",
            captured_at=datetime.now(UTC),
            content_hash=f"h-{problem}-{i}-{weight}",
            payload_json={"text": f"signal {i} for {problem}"},
        )
        session.add(raw)
        session.flush()
        st = SignalText(
            raw_signal_id=raw.id,
            source_kind="hn",
            text=f"signal {i} for {problem}",
            social_proof_weight=weight,
            is_low_signal=False,
            extracted_at=datetime.now(UTC),
        )
        session.add(st)
        session.flush()
        link = CandidateSignal(candidate_id=cand.id, raw_signal_id=raw.id)
        session.add(link)
    session.commit()
    return cand


def test_inbox_lists_pending_candidates(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        _seed_candidate(s, problem="Stripe proration is broken", weight=20)
        _seed_candidate(s, problem="HN search results are stale", weight=3)
        _seed_candidate(s, problem="An auto-killed thing", weight=10, decision=Decision.AUTO_KILLED)

    r = client.get("/inbox")
    assert r.status_code == 200
    # Both pending candidates render
    assert "Stripe proration is broken" in r.text
    assert "HN search results are stale" in r.text
    # Auto-killed should not appear in the inbox
    assert "An auto-killed thing" not in r.text
    # Pending header count
    assert "pending=2" in r.text


def test_inbox_orders_pending_by_composite_weight_desc(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        _seed_candidate(s, problem="LOW-weight item", weight=2)
        _seed_candidate(s, problem="HIGH-weight item", weight=50)
        _seed_candidate(s, problem="MED-weight item", weight=15)

    body = client.get("/inbox").text
    high_pos = body.find("HIGH-weight item")
    med_pos = body.find("MED-weight item")
    low_pos = body.find("LOW-weight item")
    assert high_pos < med_pos < low_pos


def test_approve_writes_approval_and_flips_decision(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        cand = _seed_candidate(s, problem="Approve me")
        cand_id = cand.id

    r = client.post(f"/inbox/{cand_id}/approve", data={"comment": "looks legit"})
    assert r.status_code == 200
    # Returned partial reflects approved decision
    assert 'data-decision="approved"' in r.text
    assert "approve</button>" not in r.text  # actions removed

    with factory() as s:
        refreshed = s.execute(select(Candidate).where(Candidate.id == cand_id)).scalar_one()
        assert refreshed.decision == Decision.APPROVED
        approvals = (
            s.execute(select(Approval).where(Approval.candidate_id == cand_id)).scalars().all()
        )
        assert len(approvals) == 1
        assert approvals[0].decision == ApprovalDecision.APPROVE
        assert approvals[0].comment == "looks legit"
        assert approvals[0].decided_at is not None


def test_reject_writes_approval_and_flips_decision(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        cand = _seed_candidate(s, problem="Reject me")
        cand_id = cand.id

    r = client.post(f"/inbox/{cand_id}/reject")
    assert r.status_code == 200
    assert 'data-decision="rejected"' in r.text

    with factory() as s:
        refreshed = s.execute(select(Candidate).where(Candidate.id == cand_id)).scalar_one()
        assert refreshed.decision == Decision.REJECTED
        approvals = (
            s.execute(select(Approval).where(Approval.candidate_id == cand_id)).scalars().all()
        )
        assert len(approvals) == 1
        assert approvals[0].decision == ApprovalDecision.REJECT


def test_approve_unknown_candidate_404(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, _ = client_with_session
    r = client.post("/inbox/9999/approve")
    assert r.status_code == 404


def test_signals_since_rejection_surfaces_in_rejected_section(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """A rejected candidate with new signal links *after* the approval timestamp
    appears in the re-review section. Per feedback 016 Q5 HITL-durability."""
    client, factory = client_with_session
    with factory() as s:
        # Seed a rejected candidate with 1 signal linked BEFORE rejection
        cand = _seed_candidate(
            s, problem="Rejected but signal-resurrected", decision=Decision.PENDING
        )
        cand_id = cand.id

    # Reject it (writes Approval with decided_at=now)
    r = client.post(f"/inbox/{cand_id}/reject")
    assert r.status_code == 200

    # Now Stage 1 (simulated) links a NEW signal to the rejected candidate
    # with a created_at timestamp AFTER the rejection.
    with factory() as s:
        src = s.execute(select(Source)).scalars().first()
        assert src is not None
        new_raw = RawSignal(
            source_id=src.id,
            external_id="ext-new-after-reject",
            url="https://example.com/new",
            captured_at=datetime.now(UTC),
            content_hash="h-new-after-reject",
            payload_json={"text": "new signal text"},
        )
        s.add(new_raw)
        s.flush()
        new_st = SignalText(
            raw_signal_id=new_raw.id,
            source_kind="hn",
            text="new signal text complaining about the rejected thing",
            social_proof_weight=8.0,
            is_low_signal=False,
            extracted_at=datetime.now(UTC),
        )
        s.add(new_st)
        s.flush()
        # Force created_at to be after the rejection's decided_at
        new_link = CandidateSignal(
            candidate_id=cand_id,
            raw_signal_id=new_raw.id,
            created_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        s.add(new_link)
        s.commit()

    body = client.get("/inbox").text
    assert "Re-review?" in body
    assert "Rejected but signal-resurrected" in body
    assert "+1 since rejection" in body
    # Operator-pointing copy
    assert "Decisions are durable" in body


def test_rejected_without_new_signals_does_not_appear(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """Rejected candidates with no new signals should NOT clutter the inbox."""
    client, factory = client_with_session
    with factory() as s:
        cand = _seed_candidate(s, problem="Rejected and stays quiet")
        cand_id = cand.id

    client.post(f"/inbox/{cand_id}/reject")

    body = client.get("/inbox").text
    assert "Rejected and stays quiet" not in body
    assert "rejected-with-new-signals=0" in body


def test_decision_is_durable_no_auto_resurrection(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """Per CLAUDE.md → HITL decisions are durable: a rejected candidate does
    NOT flip to pending when new signals arrive. The UI surfaces the prompt;
    the decision stays whatever the operator set."""
    client, factory = client_with_session
    with factory() as s:
        cand = _seed_candidate(s, problem="Durable rejection")
        cand_id = cand.id

    client.post(f"/inbox/{cand_id}/reject")

    # New signal arrives (simulating Stage 1 re-clustering)
    with factory() as s:
        src = s.execute(select(Source)).scalars().first()
        assert src is not None
        new_raw = RawSignal(
            source_id=src.id,
            external_id="ext-new-durable",
            url="https://example.com/durable",
            captured_at=datetime.now(UTC),
            content_hash="h-new-durable",
            payload_json={"text": "more evidence"},
        )
        s.add(new_raw)
        s.flush()
        s.add(
            SignalText(
                raw_signal_id=new_raw.id,
                source_kind="hn",
                text="more evidence",
                social_proof_weight=3.0,
                is_low_signal=False,
                extracted_at=datetime.now(UTC),
            )
        )
        s.add(
            CandidateSignal(
                candidate_id=cand_id,
                raw_signal_id=new_raw.id,
                created_at=datetime.now(UTC) + timedelta(seconds=1),
            )
        )
        s.commit()

    # Hit the inbox; decision should STILL be 'rejected'.
    client.get("/inbox")
    with factory() as s:
        cand_after = s.execute(select(Candidate).where(Candidate.id == cand_id)).scalar_one()
        assert cand_after.decision == Decision.REJECTED, (
            "decision must stay rejected; signals_since_rejection is UI-only"
        )


def test_approve_after_rejection_flips_via_explicit_operator_action(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """The operator CAN re-decide by hitting approve on the resurrect button;
    that's an explicit action, not an auto-flip."""
    client, factory = client_with_session
    with factory() as s:
        cand = _seed_candidate(s, problem="Resurrect via explicit approve")
        cand_id = cand.id

    client.post(f"/inbox/{cand_id}/reject")
    client.post(f"/inbox/{cand_id}/approve", data={"comment": "changed my mind"})

    with factory() as s:
        cand_after = s.execute(select(Candidate).where(Candidate.id == cand_id)).scalar_one()
        assert cand_after.decision == Decision.APPROVED
        approvals = (
            s.execute(
                select(Approval).where(Approval.candidate_id == cand_id).order_by(Approval.id)
            )
            .scalars()
            .all()
        )
        # Both decisions persisted as audit trail
        assert [a.decision for a in approvals] == [
            ApprovalDecision.REJECT,
            ApprovalDecision.APPROVE,
        ]
        assert approvals[1].comment == "changed my mind"
