"""Inbox nav counts, merge soft-deletion, and the merge endpoint (task 014-fix-2).

Counts in the nav chrome; soft-deleted (merged) candidates excluded from
listings; detail-view redirect for a merged candidate; the POST /inbox/merge
endpoint (with a stubbed LLM) and its validation.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
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
from apfun.pipeline.merge import MergedCard


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
    buildability: Buildability | None = Buildability.HIGH,
    merged_into_id: int | None = None,
    weight: float = 5.0,
) -> int:
    src = Source(kind="hn", name=f"hn:{id(problem)}", config_json={})
    session.add(src)
    session.flush()
    c = Candidate(
        problem_statement=problem,
        seed_keywords_json=["k"],
        dedup_key=f"slug-{id(problem)}",
        decision=decision,
        pipeline_stage=PipelineStage.NONE,
        buildability=buildability,
        buildability_rationale="r",
        merged_into_id=merged_into_id,
    )
    session.add(c)
    session.flush()
    raw = RawSignal(
        source_id=src.id,
        external_id=f"e-{id(problem)}",
        url="https://example.com",
        captured_at=datetime.now(UTC),
        content_hash=f"h-{id(problem)}",
        payload_json={"text": problem},
    )
    session.add(raw)
    session.flush()
    session.add(
        SignalText(
            raw_signal_id=raw.id,
            source_kind="hn",
            text=problem,
            social_proof_weight=weight,
            is_low_signal=False,
            extracted_at=datetime.now(UTC),
        )
    )
    session.add(CandidateSignal(candidate_id=c.id, raw_signal_id=raw.id))
    session.commit()
    return c.id


# ───────────────────────────── nav counts ─────────────────────────────


def test_nav_counts_render_per_decision(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        _seed(s, problem="p1", decision=Decision.PENDING)
        _seed(s, problem="p2", decision=Decision.PENDING)
        _seed(s, problem="a1", decision=Decision.APPROVED)

    body = client.get("/inbox").text
    assert "pending (2)" in body
    assert "approved (1)" in body
    assert "rejected (0)" in body
    assert "unsure (0)" in body


def test_nav_counts_exclude_soft_deleted(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        keep = _seed(s, problem="keep", decision=Decision.PENDING)
        # A merged-away pending candidate must not be counted.
        _seed(s, problem="gone", decision=Decision.PENDING, merged_into_id=keep)

    body = client.get("/inbox").text
    assert "pending (1)" in body
    assert "gone" not in body  # excluded from the listing too


def test_hide_non_software_count(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        _seed(s, problem="buildable", decision=Decision.PENDING, buildability=Buildability.HIGH)
        _seed(
            s, problem="cultural", decision=Decision.PENDING, buildability=Buildability.NON_SOFTWARE
        )

    body = client.get("/inbox").text
    assert "hide non-software (1)" in body


# ──────────────────────── soft-delete behaviors ───────────────────────


def test_merged_candidate_excluded_from_listing(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        keep = _seed(s, problem="keeper", decision=Decision.APPROVED)
        _seed(s, problem="merged-away", decision=Decision.APPROVED, merged_into_id=keep)

    body = client.get("/inbox/approved").text
    assert "keeper" in body
    assert "merged-away" not in body


def test_detail_of_merged_candidate_redirects(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        keep = _seed(s, problem="target", decision=Decision.PENDING)
        gone = _seed(s, problem="source", decision=Decision.PENDING, merged_into_id=keep)

    r = client.get(f"/inbox/{gone}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/inbox/{keep}?merged_from={gone}"

    # Following it lands on the target with the banner.
    r2 = client.get(f"/inbox/{gone}", follow_redirects=True)
    assert r2.status_code == 200
    assert f"Candidate #{gone} was merged into this one." in r2.text


# ───────────────────────────── merge endpoint ─────────────────────────


def test_merge_endpoint_redirects_to_new_candidate(
    client_with_session: tuple[TestClient, sessionmaker], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, factory = client_with_session
    with factory() as s:
        a = _seed(s, problem="A", decision=Decision.PENDING)
        b = _seed(s, problem="B", decision=Decision.PENDING)

    class _StubLLM:
        def judge_json(self, *args: Any, **kwargs: Any) -> MergedCard:
            return MergedCard(
                problem_statement="Merged",
                suspected_user="u",
                seed_keywords=["x"],
                buildability=Buildability.HIGH,
                buildability_rationale="r",
            )

    monkeypatch.setattr("apfun.web.routes.inbox.LLMClient", lambda: _StubLLM())

    r = client.post("/inbox/merge", data={"ids": [str(a), str(b)]}, follow_redirects=False)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/inbox/")
    new_id = int(location.rsplit("/", 1)[1])
    assert new_id not in (a, b)

    with factory() as s:
        new = s.get(Candidate, new_id)
        assert new is not None
        assert new.decision == Decision.PENDING
        assert new.problem_statement == "Merged"
        # Sources soft-deleted.
        assert s.get(Candidate, a).merged_into_id == new_id  # type: ignore[union-attr]
        assert s.get(Candidate, b).merged_into_id == new_id  # type: ignore[union-attr]


def test_merge_endpoint_rejects_single_selection(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        a = _seed(s, problem="A")

    r = client.post("/inbox/merge", data={"ids": [str(a)]}, follow_redirects=False)
    assert r.status_code == 400


def test_merge_button_and_checkboxes_present_in_listing(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        _seed(s, problem="p1")

    body = client.get("/inbox").text
    assert 'action="/inbox/merge"' in body
    assert 'class="merge-cb' in body
    assert 'id="merge-btn"' in body
    assert "disabled" in body  # button starts disabled (needs 2+)
