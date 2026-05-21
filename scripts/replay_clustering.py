"""Replay Stage 1 clustering against a snapshot of signal_text rows.

Per orchestrator feedback 016 risk-profile: prompt iteration dominates Stage 1's
iteration time. This script lets prompt refinements happen WITHOUT re-running
the upstream ingest+normalize pipeline.

Usage::

    # Run against an arbitrary signal_text id range from the live DB.
    APFUN_ANTHROPIC_API_KEY=... uv run python scripts/replay_clustering.py \\
        --ids 1,2,3,4,5

    # Run against ALL currently-unclustered signal_text rows.
    APFUN_ANTHROPIC_API_KEY=... uv run python scripts/replay_clustering.py --all

The script:
- Loads the specified signal_text rows.
- Runs the Haiku pre-pass + Opus per-bucket against them.
- Prints the resulting IdeaCards as JSON to stdout.
- DOES NOT persist anything — read-only against signal_text/raw_signals.

For destructive runs (insert candidates), use the production
`cluster_signals(session)` entry point through the scheduler, not this script.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apfun.config import settings
from apfun.db import engine
from apfun.llm.client import LLMClient
from apfun.models import RawSignal, SignalText
from apfun.pipeline import cluster as cluster_mod
from apfun.pipeline.cluster import _SignalForCluster


def _parse_ids(value: str | None) -> list[int] | None:
    if value is None:
        return None
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def _load_signals(
    session: Session, ids: list[int] | None, *, all_unclustered: bool
) -> list[tuple[SignalText, RawSignal]]:
    query = select(SignalText, RawSignal).join(RawSignal, RawSignal.id == SignalText.raw_signal_id)
    if ids is not None:
        query = query.where(SignalText.id.in_(ids))
    elif not all_unclustered:
        raise ValueError("must pass --ids or --all")
    else:
        query = query.where(SignalText.is_low_signal.is_(False))
    rows = session.execute(query.order_by(SignalText.id)).all()
    return [(r[0], r[1]) for r in rows]


def _result_to_json(
    cards: list[tuple[cluster_mod.IdeaCard, list[_SignalForCluster]]],
) -> str:
    payload: list[dict[str, Any]] = []
    for card, contributing in cards:
        payload.append(
            {
                "problem_statement": card.problem_statement,
                "suspected_user": card.suspected_user,
                "seed_keywords": card.seed_keywords,
                "contributing_signal_ids": [s.raw_signal_id for s in contributing],
                "contributing_count": len(contributing),
            }
        )
    return json.dumps(payload, indent=2, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--ids",
        help="Comma-separated signal_text ids to replay (e.g. '1,2,3').",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Replay against all unclustered signal_text rows.",
    )
    args = parser.parse_args()

    if not settings.anthropic_api_key:
        print("APFUN_ANTHROPIC_API_KEY is required to call Anthropic.", file=sys.stderr)
        return 2

    ids = _parse_ids(args.ids)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with factory() as session:
        signals = _load_signals(session, ids, all_unclustered=args.all)
        if not signals:
            print("No matching signal_text rows.", file=sys.stderr)
            return 1

        client = LLMClient()
        enriched = cluster_mod._haiku_prepass(client, signals)
        buckets = cluster_mod._bucket(enriched)
        print(
            f"Replay: signals={len(enriched)} buckets={len(buckets)}",
            file=sys.stderr,
        )
        cards = cluster_mod._judge_buckets(client, buckets)

    print(_result_to_json(cards))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
