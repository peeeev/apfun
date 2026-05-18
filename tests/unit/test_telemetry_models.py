"""Telemetry tables: llm_runs, scheduler_runs, api_usage."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apfun.models import ApiUsage, Candidate, LLMRun, SchedulerRun


def test_llm_run_with_candidate(session: Session) -> None:
    cand = Candidate(
        problem_statement="x",
        seed_keywords_json=[],
        dedup_key="t1",
    )
    session.add(cand)
    session.flush()

    session.add(
        LLMRun(
            task="cluster",
            model="claude-opus-4-7",
            input_tokens=12_000,
            output_tokens=1_500,
            cache_read_tokens=8_000,
            cache_write_tokens=0,
            latency_ms=3_400,
            est_cost_usd=0.42,
            candidate_id=cand.id,
            ok=True,
        )
    )
    session.commit()

    fetched = session.query(LLMRun).filter_by(task="cluster").one()
    assert fetched.model == "claude-opus-4-7"
    assert fetched.candidate_id == cand.id
    assert fetched.ok is True


def test_llm_run_without_candidate(session: Session) -> None:
    """`candidate_id` is NULLABLE — not every call is bound to a candidate."""
    session.add(
        LLMRun(
            task="dedup",
            model="claude-haiku-4-5",
            input_tokens=200,
            output_tokens=20,
            ok=True,
        )
    )
    session.commit()
    row = session.query(LLMRun).filter_by(task="dedup").one()
    assert row.candidate_id is None


def test_scheduler_run(session: Session) -> None:
    started = datetime.now(UTC)
    session.add(
        SchedulerRun(
            job_id="reddit-ingest",
            started_at=started,
            finished_at=started,
            ok=True,
            items_processed=42,
        )
    )
    session.commit()
    row = session.query(SchedulerRun).filter_by(job_id="reddit-ingest").one()
    assert row.items_processed == 42


def test_api_usage_unique_provider_day(session: Session) -> None:
    today = date(2026, 5, 18)
    session.add(ApiUsage(provider="dataforseo", day=today, est_cost_usd=1.5, calls=3))
    session.commit()
    session.add(ApiUsage(provider="dataforseo", day=today, est_cost_usd=2.0, calls=5))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Different day → fine.
    session.add(ApiUsage(provider="dataforseo", day=date(2026, 5, 19), est_cost_usd=0.5, calls=1))
    session.commit()
