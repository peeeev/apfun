"""Tests for the task 014-fix-1 inbox UX bundle (orchestrator request 028):

- source badges in the listing (first 3 + "+N more")
- detail view at /inbox/<id> (signals + URLs + 404)
- ternary decisions (approve/reject/unsure) with notes persisted to approvals
- status-filtered listings (/inbox/approved|rejected|unsure) with empty-states
- operator re-decision of any candidate; HITL durability still holds
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

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

_COUNTER = {"n": 0}


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


def _seed(
    session: Session,
    *,
    problem: str,
    decision: Decision = Decision.PENDING,
    sources: list[tuple[str, dict[str, Any]]] | None = None,
    weight: float = 5.0,
) -> Candidate:
    """Create a candidate with one signal per (source_kind, payload) tuple."""
    if sources is None:
        sources = [("hn", {"_apfun_query": "wishes"})]
    _COUNTER["n"] += 1
    uid = _COUNTER["n"]
    cand = Candidate(
        problem_statement=problem,
        suspected_user="founders",
        seed_keywords_json=["alpha"],
        vertical="dev-tools",
        dedup_key=f"slug-{uid}",
        decision=decision,
        pipeline_stage=PipelineStage.NONE,
    )
    session.add(cand)
    session.flush()
    for i, (kind, payload) in enumerate(sources):
        src = Source(kind=kind, name=f"{kind}:{uid}:{i}", config_json={})
        session.add(src)
        session.flush()
        raw = RawSignal(
            source_id=src.id,
            external_id=f"ext-{uid}-{i}",
            url=f"https://example.com/{uid}/{i}",
            captured_at=datetime.now(UTC),
            content_hash=f"h-{uid}-{i}",
            payload_json=payload,
        )
        session.add(raw)
        session.flush()
        session.add(
            SignalText(
                raw_signal_id=raw.id,
                source_kind=kind,
                text=f"signal {i} text for {problem}",
                social_proof_weight=weight,
                is_low_signal=False,
                extracted_at=datetime.now(UTC),
            )
        )
        session.add(CandidateSignal(candidate_id=cand.id, raw_signal_id=raw.id))
    session.commit()
    return cand


# ───────────────────────────── source badges ───────────────────────────


def test_listing_shows_source_badges(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        _seed(
            s,
            problem="Multi-source candidate",
            sources=[("reddit", {"subreddit": "SaaS"}), ("hn", {"_apfun_query": "wishes"})],
        )
    body = client.get("/inbox").text
    assert "r/SaaS" in body
    assert "hn:wishes" in body


def test_listing_collapses_many_sources_to_first_three_plus_more(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        _seed(
            s,
            problem="Six-source candidate",
            sources=[("reddit", {"subreddit": f"sub{i}"}) for i in range(6)],
        )
    body = client.get("/inbox").text
    # First 3 distinct subreddits shown...
    assert "r/sub0" in body
    assert "r/sub2" in body
    # ...and a "+3 more" overflow (6 distinct - 3 shown).
    assert "+3 more" in body
    # The 4th+ aren't rendered as their own badge.
    assert "r/sub5" not in body


# ───────────────────────────── detail view ─────────────────────────────


def test_detail_view_renders_signals_and_urls(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        cand = _seed(
            s,
            problem="Detailed candidate",
            sources=[("reddit", {"subreddit": "Entrepreneur"})],
        )
        cand_id = cand.id

    r = client.get(f"/inbox/{cand_id}")
    assert r.status_code == 200
    assert "Detailed candidate" in r.text
    assert "Contributing signals" in r.text
    assert "r/Entrepreneur" in r.text
    # The original-post URL is a clickable link.
    assert f'href="https://example.com/{_COUNTER["n"]}/0"' in r.text or "view original" in r.text


def test_detail_view_404_for_unknown(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, _ = client_with_session
    assert client.get("/inbox/999999").status_code == 404


def test_detail_view_shows_decision_history(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        cand = _seed(s, problem="History candidate")
        cand_id = cand.id
    client.post(f"/inbox/{cand_id}/reject", data={"comment": "not a fit"})
    client.post(f"/inbox/{cand_id}/approve", data={"comment": "changed my mind"})

    body = client.get(f"/inbox/{cand_id}").text
    assert "Decision history" in body
    assert "not a fit" in body
    assert "changed my mind" in body


# ───────────────────────────── decisions ───────────────────────────────


def test_unsure_decision_persists_and_flips_state(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        cand = _seed(s, problem="Unsure me")
        cand_id = cand.id

    r = client.post(f"/inbox/{cand_id}/unsure", data={"comment": "could go either way"})
    assert r.status_code == 200
    assert 'data-decision="unsure"' in r.text

    with factory() as s:
        refreshed = s.execute(select(Candidate).where(Candidate.id == cand_id)).scalar_one()
        assert refreshed.decision == Decision.UNSURE
        appr = s.execute(select(Approval).where(Approval.candidate_id == cand_id)).scalar_one()
        assert appr.decision == ApprovalDecision.UNSURE
        assert appr.comment == "could go either way"


def test_notes_saved_on_approve(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        cand = _seed(s, problem="Note me")
        cand_id = cand.id
    client.post(f"/inbox/{cand_id}/approve", data={"comment": "strong signal from r/SaaS"})
    with factory() as s:
        appr = s.execute(select(Approval).where(Approval.candidate_id == cand_id)).scalar_one()
        assert appr.comment == "strong signal from r/SaaS"


def test_empty_notes_is_fine(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        cand = _seed(s, problem="No note")
        cand_id = cand.id
    r = client.post(f"/inbox/{cand_id}/reject")  # no comment field at all
    assert r.status_code == 200
    with factory() as s:
        appr = s.execute(select(Approval).where(Approval.candidate_id == cand_id)).scalar_one()
        assert appr.comment is None


# ─────────────────────────── status filters ────────────────────────────


def test_status_filters_show_only_matching_decision(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        _seed(s, problem="APPROVED-one", decision=Decision.APPROVED)
        _seed(s, problem="REJECTED-one", decision=Decision.REJECTED)
        _seed(s, problem="UNSURE-one", decision=Decision.UNSURE)
        _seed(s, problem="PENDING-one", decision=Decision.PENDING)

    approved = client.get("/inbox/approved").text
    assert "APPROVED-one" in approved
    assert "REJECTED-one" not in approved
    assert "UNSURE-one" not in approved

    rejected = client.get("/inbox/rejected").text
    assert "REJECTED-one" in rejected
    assert "APPROVED-one" not in rejected

    unsure = client.get("/inbox/unsure").text
    assert "UNSURE-one" in unsure
    assert "APPROVED-one" not in unsure


def test_status_filter_empty_state(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, _ = client_with_session
    body = client.get("/inbox/approved").text
    assert "No approved candidates yet." in body


def test_unknown_filter_404(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, _ = client_with_session
    assert client.get("/inbox/bogus").status_code == 404


def test_integer_path_routes_to_detail_not_filter(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """`/inbox/<int>` must hit the detail view, not the string filter route."""
    client, factory = client_with_session
    with factory() as s:
        cand = _seed(s, problem="Routing check")
        cand_id = cand.id
    r = client.get(f"/inbox/{cand_id}")
    assert r.status_code == 200
    assert "Contributing signals" in r.text  # detail-only heading


# ───────────────────── re-decision + HITL durability ───────────────────


def test_any_candidate_is_re_decidable(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """Operator can explicitly re-decide an already-approved candidate — this
    is an explicit action, NOT the auto-flip HITL durability forbids."""
    client, factory = client_with_session
    with factory() as s:
        cand = _seed(s, problem="Re-decide me", decision=Decision.APPROVED)
        cand_id = cand.id

    # Status-filtered listing shows decision controls so it can be re-decided.
    approved_body = client.get("/inbox/approved").text
    assert f"/inbox/{cand_id}/reject" in approved_body

    client.post(f"/inbox/{cand_id}/reject", data={"comment": "on reflection, no"})
    with factory() as s:
        refreshed = s.execute(select(Candidate).where(Candidate.id == cand_id)).scalar_one()
        assert refreshed.decision == Decision.REJECTED


def test_hitl_durability_rejected_with_new_signals_does_not_autoflip(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """Carried over from feedback 016 Q5: new signals after rejection surface a
    re-review prompt but never auto-flip the decision."""
    from datetime import timedelta

    client, factory = client_with_session
    with factory() as s:
        cand = _seed(s, problem="Durable rejection", sources=[("reddit", {"subreddit": "SaaS"})])
        cand_id = cand.id
    client.post(f"/inbox/{cand_id}/reject")

    # New signal linked after the rejection timestamp.
    with factory() as s:
        src = s.execute(select(Source)).scalars().first()
        assert src is not None
        new_raw = RawSignal(
            source_id=src.id,
            external_id="ext-late",
            url="https://example.com/late",
            captured_at=datetime.now(UTC),
            content_hash="h-late",
            payload_json={"subreddit": "SaaS"},
        )
        s.add(new_raw)
        s.flush()
        s.add(
            SignalText(
                raw_signal_id=new_raw.id,
                source_kind="reddit",
                text="more evidence",
                social_proof_weight=9.0,
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

    body = client.get("/inbox").text
    assert "Re-review?" in body
    # Decision stays rejected — no auto-flip.
    with factory() as s:
        refreshed = s.execute(select(Candidate).where(Candidate.id == cand_id)).scalar_one()
        assert refreshed.decision == Decision.REJECTED
