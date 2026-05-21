"""Dump candidates + llm_runs aggregates after a Stage 1 run.

Used by the operator runbook `docs/operator/runbooks/001-stage1-first-pass.md`
to capture the artifacts the orchestrator needs for request 018 routing:

1. Every candidate row with its full text and the raw text of every
   contributing signal (truncated to ~300 chars per signal).
2. `llm_runs` aggregates per task (calls, tokens, cache hit ratio, total
   est_cost_usd) plus a grand-total cost line.
3. Operational observations are operator-supplied — this script just dumps
   the structured data.

Output is plain text suitable for paste into the orchestrator request.

Usage::

    uv run python scripts/dump_run_artifacts.py
    uv run python scripts/dump_run_artifacts.py --since 2026-05-21
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from apfun.db import engine
from apfun.models import (
    Candidate,
    CandidateSignal,
    LLMRun,
    SchedulerRun,
    SignalText,
)


def _truncate(s: str | None, *, n: int = 300) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _dump_candidates(session: Any) -> None:
    print("=" * 78)
    print("CANDIDATES (read top-to-bottom; pick 10 representatives for the request)")
    print("=" * 78)
    cands = session.execute(select(Candidate).order_by(Candidate.id)).scalars().all()
    if not cands:
        print("\n(no candidate rows)\n")
        return
    print(f"\nTotal candidates: {len(cands)}\n")
    for c in cands:
        sigs = session.execute(
            select(SignalText.text, SignalText.social_proof_weight, SignalText.source_kind)
            .join(
                CandidateSignal,
                CandidateSignal.raw_signal_id == SignalText.raw_signal_id,
            )
            .where(CandidateSignal.candidate_id == c.id)
            .order_by(SignalText.social_proof_weight.desc())
        ).all()
        print(f"### Candidate #{c.id}  (dedup_key={c.dedup_key})")
        print(f"  decision: {c.decision.value}  pipeline_stage: {c.pipeline_stage.value}")
        print(f"  vertical: {c.vertical or '(none)'}")
        print("  problem_statement:")
        print(f"    {c.problem_statement}")
        print(f"  suspected_user: {c.suspected_user or '(none)'}")
        print(f"  seed_keywords: {c.seed_keywords_json}")
        print(f"  contributing signals ({len(sigs)}):")
        for i, (text, weight, kind) in enumerate(sigs[:5], start=1):
            print(f"    [{i}] ({kind}, weight={weight:.0f}) {_truncate(text)}")
        if len(sigs) > 5:
            print(f"    ...and {len(sigs) - 5} more")
        print()


def _dump_llm_runs(session: Any) -> None:
    print("=" * 78)
    print("LLM_RUNS AGGREGATES")
    print("=" * 78)
    rows = session.execute(
        select(
            LLMRun.task,
            func.count(LLMRun.id),
            func.avg(LLMRun.input_tokens),
            func.max(LLMRun.input_tokens),
            func.avg(LLMRun.output_tokens),
            func.max(LLMRun.output_tokens),
            func.sum(LLMRun.cache_read_tokens),
            func.sum(LLMRun.cache_write_tokens),
            func.sum(LLMRun.est_cost_usd),
            func.sum(LLMRun.attempts),
        )
        .group_by(LLMRun.task)
        .order_by(LLMRun.task)
    ).all()
    if not rows:
        print("\n(no llm_runs rows)\n")
        return

    # Header
    print()
    print(
        f"  {'task':<14} {'calls':>6} {'in/avg':>8} {'in/max':>8} "
        f"{'out/avg':>8} {'out/max':>8} {'cache_rd':>10} {'cache_wr':>10} "
        f"{'cost_usd':>10} {'attempts':>9}"
    )
    print("  " + "-" * 100)
    for (
        task,
        calls,
        in_avg,
        in_max,
        out_avg,
        out_max,
        cache_rd,
        cache_wr,
        cost,
        attempts,
    ) in rows:
        print(
            f"  {task:<14} {int(calls):>6} {int(in_avg or 0):>8} {int(in_max or 0):>8} "
            f"{int(out_avg or 0):>8} {int(out_max or 0):>8} {int(cache_rd or 0):>10} "
            f"{int(cache_wr or 0):>10} {float(cost or 0):>10.4f} {int(attempts or 0):>9}"
        )

    # Cache hit ratio + grand total
    total_cost = session.execute(select(func.sum(LLMRun.est_cost_usd))).scalar() or 0.0
    total_read = session.execute(select(func.sum(LLMRun.cache_read_tokens))).scalar() or 0
    total_write = session.execute(select(func.sum(LLMRun.cache_write_tokens))).scalar() or 0
    cache_total = total_read + total_write
    hit_ratio = (total_read / cache_total) if cache_total else 0.0
    print()
    print(f"  Cache hit ratio: {hit_ratio:.1%}  (read={total_read}, write={total_write})")
    print(f"  GRAND TOTAL COST: ${total_cost:.4f}")
    if total_cost > 5.0:
        print("  ⚠️  EXCEEDED $5 BUDGET GUARD — investigate before continuing.")

    # Failed-call summary
    failed = session.execute(select(func.count()).where(LLMRun.ok.is_(False))).scalar() or 0
    if failed:
        print(f"\n  ⚠️  {failed} failed call(s). Inspect with:")
        print(
            "       sqlite3 data/apfun.db 'SELECT task, attempts, substr(error, 1, 200) "
            "FROM llm_runs WHERE ok=0;'"
        )


def _dump_scheduler_runs(session: Any) -> None:
    print("=" * 78)
    print("SCHEDULER_RUNS")
    print("=" * 78)
    rows = session.execute(select(SchedulerRun).order_by(SchedulerRun.started_at)).scalars().all()
    if not rows:
        print("\n(no scheduler_runs rows)\n")
        return
    print()
    for r in rows:
        dur_ms = int((r.finished_at - r.started_at).total_seconds() * 1000) if r.finished_at else 0
        ok_marker = "✓" if r.ok else "✗"
        print(
            f"  {ok_marker} {r.job_id:<28} items={r.items_processed or 0:>4} "
            f"dur={dur_ms:>6}ms  {('error=' + r.error[:80]) if r.error else ''}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help=(
            "ISO date — only consider runs after this timestamp. "
            "Not currently filtered (placeholder); the runbook session "
            "is small enough that all rows are relevant."
        ),
    )
    _args = parser.parse_args()

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    print()
    print(f"Stage 1 run artifacts — captured {datetime.now(UTC).isoformat()}")
    print()
    with factory() as session:
        _dump_candidates(session)
        _dump_llm_runs(session)
        _dump_scheduler_runs(session)
    print()
    print("Copy the sections above into orchestrator request 018 (paste verbatim;")
    print("then add your free-form operational observations below).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
