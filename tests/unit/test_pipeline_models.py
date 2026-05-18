"""Core pipeline tables: candidate → demand_check → approval → CA → score → opportunity."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apfun.models import (
    Approval,
    ApprovalDecision,
    Candidate,
    CompetitiveAnalysis,
    DemandCheck,
    DemandVerdict,
    Opportunity,
    OpportunityStatus,
    Project,
    ProjectStatus,
    Score,
)


@pytest.fixture
def candidate(session: Session) -> Candidate:
    c = Candidate(
        problem_statement="founders waste time wiring up Stripe billing",
        seed_keywords_json=["stripe billing"],
        dedup_key="stripe-billing-1",
    )
    session.add(c)
    session.flush()
    return c


def test_full_pipeline_object_graph(session: Session, candidate: Candidate) -> None:
    now = datetime.now(UTC)

    session.add(
        DemandCheck(
            candidate_id=candidate.id,
            run_at=now,
            trend_slope=0.3,
            autosuggest_json={"alternatives_to": ["stripe"]},
            verdict=DemandVerdict.PASS,
        )
    )
    session.add(
        Approval(
            candidate_id=candidate.id,
            decision=ApprovalDecision.APPROVE,
            comment="looks promising",
            decided_at=now,
        )
    )
    session.add(
        CompetitiveAnalysis(
            candidate_id=candidate.id,
            competitor_name="Acme Corp",
            competitor_url="https://acme.example",
            pricing_json={"tiers": [{"name": "free", "price": 0}]},
            features_json=[{"name": "billing"}],
            funding_json={"last_round": "Series A"},
            reviews_summary_json={"top_complaints": ["slow support"]},
            scraped_at=now,
        )
    )
    session.add(
        Score(
            candidate_id=candidate.id,
            demand=0.7,
            supply=0.4,
            unmet_pain=0.6,
            moat_potential=0.5,
            composite=1.05,
            breakdown_json={"demand_components": {"volume": 1000}},
            scored_at=now,
            model_version="v0.1",
        )
    )
    opp = Opportunity(
        candidate_id=candidate.id,
        top_complaints_json=[{"theme": "slow", "severity": 4}],
        feature_gaps_json=[{"gap": "no-api"}],
        pricing_gaps_json=[],
        vertical_wedge="solo SaaS founders",
        sources_json=["https://reddit.com/r/SaaS/abc"],
        synthesized_at=now,
    )
    session.add(opp)
    session.flush()
    session.add(
        Project(
            opportunity_id=opp.id,
            slug="stripe-billing-helper",
            subdomain="stripe-billing-helper.apfun.online",
        )
    )
    session.commit()

    fetched_opp = session.get(Opportunity, opp.id)
    assert fetched_opp is not None
    assert fetched_opp.status == OpportunityStatus.ACTIVE
    assert fetched_opp.candidate_id == candidate.id

    proj = session.execute(
        text("SELECT slug, status FROM projects WHERE opportunity_id = :oid"),
        {"oid": opp.id},
    ).one()
    assert proj.slug == "stripe-billing-helper"
    # DB-level value is the lowercase string (values_callable).
    assert proj.status == ProjectStatus.PLACEHOLDER.value


def test_opportunity_unique_per_candidate(session: Session, candidate: Candidate) -> None:
    now = datetime.now(UTC)
    session.add(Opportunity(candidate_id=candidate.id, synthesized_at=now))
    session.commit()
    session.add(Opportunity(candidate_id=candidate.id, synthesized_at=now))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_project_slug_unique(session: Session, candidate: Candidate) -> None:
    now = datetime.now(UTC)
    opp = Opportunity(candidate_id=candidate.id, synthesized_at=now)
    session.add(opp)
    session.flush()
    session.add(Project(opportunity_id=opp.id, slug="dup", subdomain="dup.example"))
    session.commit()
    session.add(Project(opportunity_id=opp.id, slug="dup", subdomain="dup2.example"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_check_constraint_blocks_invalid_enum(session: Session, candidate: Candidate) -> None:
    """Raw SQL bypassing the ORM still hits the DB-level CHECK."""
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO demand_checks "
                "(candidate_id, run_at, autosuggest_json, verdict, created_at, updated_at) "
                "VALUES (:cid, '2026-01-01', '{}', 'BOGUS', '2026-01-01', '2026-01-01')"
            ),
            {"cid": candidate.id},
        )
        session.commit()
    session.rollback()
