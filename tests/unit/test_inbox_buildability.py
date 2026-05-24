"""Inbox buildability UI (task 015 / orchestrator request 030):

- color-coded badge per buildability value (and none when unassessed)
- `?hide_non_software=true` excludes non_software candidates; default shows all
- detail view renders the buildability rationale
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

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
    buildability: Buildability | None,
    rationale: str = "",
    decision: Decision = Decision.PENDING,
) -> int:
    src = Source(kind="hn", name=f"hn:{problem[:20]}", config_json={})
    session.add(src)
    session.flush()
    cand = Candidate(
        problem_statement=problem,
        suspected_user="founders",
        seed_keywords_json=["alpha"],
        vertical="dev-tools",
        dedup_key=f"slug-{id(problem)}",
        decision=decision,
        pipeline_stage=PipelineStage.NONE,
        buildability=buildability,
        buildability_rationale=rationale,
    )
    session.add(cand)
    session.flush()
    raw = RawSignal(
        source_id=src.id,
        external_id=f"ext-{id(problem)}",
        url="https://example.com/1",
        captured_at=datetime.now(UTC),
        content_hash=f"h-{id(problem)}",
        payload_json={"text": f"signal for {problem}"},
    )
    session.add(raw)
    session.flush()
    session.add(
        SignalText(
            raw_signal_id=raw.id,
            source_kind="hn",
            text=f"signal for {problem}",
            social_proof_weight=5.0,
            is_low_signal=False,
            extracted_at=datetime.now(UTC),
        )
    )
    session.add(CandidateSignal(candidate_id=cand.id, raw_signal_id=raw.id))
    session.commit()
    return cand.id


def test_inbox_renders_buildability_badges(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        _seed(s, problem="high one", buildability=Buildability.HIGH)
        _seed(s, problem="medium one", buildability=Buildability.MEDIUM)
        _seed(s, problem="low one", buildability=Buildability.LOW)
        _seed(s, problem="nonsw one", buildability=Buildability.NON_SOFTWARE)

    body = client.get("/inbox").text
    assert "Buildable" in body
    assert "Maybe" in body
    assert "Unlikely" in body
    assert "Non-software" in body
    # The CSS hooks for color-coding are present.
    assert "tag-build-high" in body
    assert "tag-build-none" in body


def test_unassessed_candidate_shows_no_badge(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        _seed(s, problem="never assessed", buildability=None)

    body = client.get("/inbox").text
    assert "never assessed" in body
    # No buildability badge classes for an unassessed candidate.
    assert "tag-build-" not in body


def test_hide_non_software_filter(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        _seed(s, problem="buildable thing", buildability=Buildability.HIGH)
        _seed(s, problem="cultural thing", buildability=Buildability.NON_SOFTWARE)

    # Default: both visible.
    default_body = client.get("/inbox").text
    assert "buildable thing" in default_body
    assert "cultural thing" in default_body
    # Toggle affordance present and unchecked.
    assert "hide non-software" in default_body
    assert "?hide_non_software=true" in default_body

    # Filtered: non_software excluded; toggle reflects checked state.
    hidden_body = client.get("/inbox?hide_non_software=true").text
    assert "buildable thing" in hidden_body
    assert "cultural thing" not in hidden_body
    assert "☑" in hidden_body


def test_hide_non_software_applies_to_status_filter(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        _seed(
            s,
            problem="approved nonsw",
            buildability=Buildability.NON_SOFTWARE,
            decision=Decision.APPROVED,
        )
        _seed(
            s,
            problem="approved buildable",
            buildability=Buildability.HIGH,
            decision=Decision.APPROVED,
        )

    body = client.get("/inbox/approved?hide_non_software=true").text
    assert "approved buildable" in body
    assert "approved nonsw" not in body


def test_inbox_detail_shows_rationale(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        cid = _seed(
            s,
            problem="detail with rationale",
            buildability=Buildability.MEDIUM,
            rationale="Workflow and data are buildable; payments need a partner.",
        )

    body = client.get(f"/inbox/{cid}").text
    assert "Workflow and data are buildable; payments need a partner." in body
    assert "Maybe" in body
