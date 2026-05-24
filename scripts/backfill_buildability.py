"""One-time backfill of `candidates.buildability` (task 015 / request 030).

The cluster pass now emits buildability inline for *new* candidates; this script
fills in candidates created before the buildability layer existed. It runs one
Opus call per unassessed candidate against `buildability_only.j2` (leaner than
re-running the full `cluster.j2`, which has no clustering work to do here).

Idempotent — re-running skips candidates that already have a buildability value
(`buildability IS NOT NULL`), so a crash mid-run is safe to resume.

Cost guard: the run aborts if cumulative `est_cost_usd` for this invocation's
`buildability` calls exceeds `--budget` (default $5.00). The orchestrator's
estimate is ~$1.25 for ~168 candidates; approaching $5 means something is wrong
(retry storms, a much larger candidate set than expected).

Usage::

    # Eyeball a handful before committing to the full run (pre-merge step 10).
    APFUN_ANTHROPIC_API_KEY=... uv run python scripts/backfill_buildability.py --limit 10

    # Full backfill of all unassessed candidates.
    APFUN_ANTHROPIC_API_KEY=... uv run python scripts/backfill_buildability.py

    # List what WOULD be assessed without spending a token.
    uv run python scripts/backfill_buildability.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apfun.config import settings
from apfun.db import engine
from apfun.llm import prompts
from apfun.llm.client import LLMClient
from apfun.models import Candidate, LLMRun
from apfun.pipeline.cluster import BuildabilityAssessment

_SYSTEM = "You assess whether a single opportunity is software-addressable."
_DEFAULT_BUDGET_USD = 5.0


class _Assessor(Protocol):
    """The slice of LLMClient the backfill needs (lets tests stub it)."""

    def judge_json(
        self,
        task: str,
        system: str,
        messages: list[dict[str, Any]],
        *,
        schema: type[BuildabilityAssessment],
        candidate_id: int | None = ...,
    ) -> BuildabilityAssessment: ...


@dataclass
class BackfillReport:
    assessed: int = 0
    cost_usd: float = 0.0
    aborted: bool = False
    counts: Counter[str] = field(default_factory=Counter)


def _unassessed(session: Session, *, limit: int | None) -> list[Candidate]:
    """Candidates with no buildability yet, oldest first (stable resume order)."""
    query = select(Candidate).where(Candidate.buildability.is_(None)).order_by(Candidate.id)
    if limit is not None:
        query = query.limit(limit)
    return list(session.execute(query).scalars().all())


def _run_cost_usd(session: Session, *, since: datetime) -> float:
    """Cumulative est_cost_usd for this run's buildability calls."""
    total = session.execute(
        select(func.coalesce(func.sum(LLMRun.est_cost_usd), 0.0)).where(
            LLMRun.task == "buildability", LLMRun.created_at >= since
        )
    ).scalar_one()
    return float(total)


def _assess_one(client: _Assessor, candidate: Candidate) -> BuildabilityAssessment:
    user_prompt = prompts.render(
        "buildability_only.j2",
        problem_statement=candidate.problem_statement,
        suspected_user=candidate.suspected_user,
        keywords=candidate.seed_keywords_json,
    )
    return client.judge_json(
        "buildability",
        _SYSTEM,
        [{"role": "user", "content": user_prompt}],
        schema=BuildabilityAssessment,
        candidate_id=candidate.id,
    )


def backfill(
    session: Session,
    client: _Assessor,
    *,
    limit: int | None = None,
    budget: float = _DEFAULT_BUDGET_USD,
    on_each: Callable[[Candidate, BuildabilityAssessment], None] | None = None,
) -> BackfillReport:
    """Assess every unassessed candidate. Commits per candidate (idempotent
    resume on crash). Aborts if cumulative `buildability` cost exceeds `budget`.

    `on_each` is an optional progress hook (the CLI prints through it). Cost is
    read from `llm_runs` rows the client logs for `task='buildability'` since
    this call started.
    """
    report = BackfillReport()
    run_start = datetime.now(UTC)
    for candidate in _unassessed(session, limit=limit):
        assessment = _assess_one(client, candidate)
        candidate.buildability = assessment.buildability
        candidate.buildability_rationale = assessment.buildability_rationale
        candidate.buildability_assessed_at = datetime.now(UTC)
        session.commit()
        report.assessed += 1
        report.counts[assessment.buildability.value] += 1
        if on_each is not None:
            on_each(candidate, assessment)

        report.cost_usd = _run_cost_usd(session, since=run_start)
        if report.cost_usd > budget:
            report.aborted = True
            break
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--limit", type=int, default=None, help="Cap candidates assessed this run.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List candidates that would be assessed; make no LLM calls, no writes.",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=_DEFAULT_BUDGET_USD,
        help=f"Abort if run cost exceeds this many USD (default {_DEFAULT_BUDGET_USD}).",
    )
    args = parser.parse_args()

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with factory() as session:
        pending = _unassessed(session, limit=args.limit)
        if not pending:
            print("No unassessed candidates — buildability backfill already complete.")
            return 0

        print(f"{len(pending)} candidate(s) to assess (buildability IS NULL).")
        if args.dry_run:
            for c in pending:
                print(f"  [{c.id}] {c.problem_statement[:90]}")
            print("dry-run: no LLM calls made, nothing written.")
            return 0

        if not settings.anthropic_api_key:
            print("APFUN_ANTHROPIC_API_KEY is required to call Anthropic.", file=sys.stderr)
            return 2

        def _print(c: Candidate, a: BuildabilityAssessment) -> None:
            print(
                f"  [{c.id}] {a.buildability.value:<12} {c.problem_statement[:70]}"
                f"\n      → {a.buildability_rationale}"
            )

        report = backfill(
            session, LLMClient(), limit=args.limit, budget=args.budget, on_each=_print
        )

        if report.aborted:
            print(
                f"\nABORT: run cost ${report.cost_usd:.2f} exceeded budget ${args.budget:.2f} "
                f"after {report.assessed} candidate(s). Re-run to resume — idempotent.",
                file=sys.stderr,
            )
            return 3

        print(f"\nAssessed {report.assessed} candidate(s). Cost: ${report.cost_usd:.4f}")
        for value in ("high", "medium", "low", "non_software"):
            print(f"  {value:<12} {report.counts.get(value, 0)}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
