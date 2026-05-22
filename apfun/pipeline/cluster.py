"""Stage 1 clustering: signal_text rows → candidate idea cards.

Pipeline (per task 010 spec + orchestrator feedback 016):
  1. Read unclustered signal_text rows (skip is_low_signal=True; skip rows
     already linked via candidate_signals).
  2. Haiku pre-pass per signal — extracts `(core_complaint, vertical,
     keywords)` via `mechanic_json("dedup", schema=SignalCoreComplaint, ...)`.
  3. Bucket signals by `(vertical, frozenset(keywords))`. Buckets are a
     cost-shaping tool, not a quality-defining one — Opus inside the bucket
     does the actual clustering.
  4. Opus per bucket — `judge_json("cluster", schema=ClusterOutput,
     cache_ttl="1h", ...)`. Each bucket becomes 0+ idea cards.
  5. Persist candidates with `dedup_key`-based linking. If a candidate with
     the same `dedup_key` exists already (any decision), link new signals
     to it but DO NOT touch its `decision` — see CLAUDE.md → "HITL
     decisions are durable."

Soft caps (`_MAX_BUCKETS_PER_RUN`, `_MAX_SIGNALS_PER_RUN`) prevent runaway
cost; excess processes in the next scheduled run.

Pass-2 merge across chunks is implemented when a single bucket would exceed
~150k tokens (rare in practice for v1 volumes); see `_run_pass_2_merge`.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.db import try_insert
from apfun.llm import prompts
from apfun.llm.client import LLMClient
from apfun.models import (
    Candidate,
    CandidateSignal,
    Decision,
    PipelineStage,
    RawSignal,
    SchedulerRun,
    SignalText,
)

logger = logging.getLogger(__name__)


# heuristic 2026-05-21 — bucket count dominates Opus cost (one judge call each).
# Cap the schedule's reach so a noisy batch doesn't trigger a spending event.
# Retune trigger: consecutive runs that hit the cap → open orchestrator
# request to either bump the cap or schedule Stage 1 more frequently.
# Per feedback 016 Q7.
_MAX_BUCKETS_PER_RUN: int = 50

# heuristic 2026-05-21 — signal count drives the Haiku pre-pass cost, which
# is ~50× cheaper per token than Opus. The cap exists to prevent a
# pathological batch from running Haiku on tens of thousands of rows in
# one invocation. Per feedback 016 Q7.
_MAX_SIGNALS_PER_RUN: int = 500


# Schemas — Pydantic types the wrapper validates LLM output against.


class SignalCoreComplaint(BaseModel):
    """Output schema for the Haiku pre-pass on one signal."""

    core_complaint: str
    vertical: str
    keywords: list[str] = Field(default_factory=lambda: list[str]())


class IdeaCard(BaseModel):
    """One clustered idea emitted by `judge_json("cluster", ...)` pass 1."""

    problem_statement: str
    suspected_user: str | None = None
    seed_keywords: list[str] = Field(default_factory=lambda: list[str]())
    contributing_signal_ids: list[int] = Field(default_factory=lambda: list[int]())


class ClusterOutput(BaseModel):
    """Full output of one pass-1 Opus call (zero or more IdeaCards)."""

    clusters: list[IdeaCard] = Field(default_factory=lambda: list[IdeaCard]())


class ClusterMergeOutput(BaseModel):
    """Output of pass-2 merge: maps each pass-1 cluster id to a canonical id."""

    merge_map: dict[str, str] = Field(default_factory=lambda: dict[str, str]())


# Result shape returned by `cluster_signals` and recorded in scheduler_runs.


@dataclass
class ClusterResult:
    processed_signals: int = 0
    buckets: int = 0
    candidates_inserted: int = 0
    signals_linked: int = 0
    capped: bool = False
    latency_ms: int = 0
    error_class: str | None = None


# ─────────────────────────────── helpers ──────────────────────────────


def _slugify(text: str, *, max_len: int = 200) -> str:
    """Best-effort slug for `dedup_key`. Lowercase, ascii-fold, hyphenated."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return cleaned[:max_len] if cleaned else "unspecified"


def _bucket_key(vertical: str, keywords: list[str]) -> tuple[str, frozenset[str]]:
    """Deterministic bucket key. Lowercase + dedupe + frozenset so input order
    doesn't change the hash (per feedback 016 Q1 implementation note)."""
    cleaned = {k.strip().lower() for k in keywords if k and k.strip()}
    return (vertical.strip().lower() or "unknown", frozenset(cleaned))


@dataclass
class _SignalForCluster:
    raw_signal_id: int
    text: str
    source_kind: str
    social_proof_weight: float
    core_complaint: str
    vertical: str
    keywords: list[str]


# ────────────────────────────── public API ─────────────────────────────


def cluster_signals(
    session: Session,
    *,
    llm_client: LLMClient,
    job_id: str = "pipeline.cluster",
    max_buckets: int = _MAX_BUCKETS_PER_RUN,
    max_signals: int = _MAX_SIGNALS_PER_RUN,
) -> ClusterResult:
    """Run one Stage 1 pass over unclustered signal_text rows."""
    started = time.monotonic()
    started_at = datetime.now(UTC)
    result = ClusterResult()
    batch_error: str | None = None

    try:
        unclustered = _load_unclustered(session, limit=max_signals + 1)
        capped_on_signals = len(unclustered) > max_signals
        if capped_on_signals:
            unclustered = unclustered[:max_signals]
            result.capped = True

        if not unclustered:
            return result

        enriched = _haiku_prepass(llm_client, unclustered)
        result.processed_signals = len(enriched)

        buckets = _bucket(enriched)
        if len(buckets) > max_buckets:
            logger.warning(
                "cluster.bucket_cap_hit",
                extra={
                    "cluster_caps": {
                        "buckets_total": len(buckets),
                        "buckets_processed": max_buckets,
                    }
                },
            )
            # Process the largest buckets first — they have the most evidence.
            buckets_sorted = sorted(buckets.items(), key=lambda kv: -len(kv[1]))
            buckets = dict(buckets_sorted[:max_buckets])
            result.capped = True
        result.buckets = len(buckets)

        cards = _judge_buckets(llm_client, buckets)

        inserted, linked = _persist_clusters(session, cards, enriched)
        result.candidates_inserted = inserted
        result.signals_linked = linked

    except Exception as exc:  # noqa: BLE001 — capture for scheduler_runs row
        logger.exception("cluster.failed")
        result.error_class = type(exc).__name__
        batch_error = result.error_class
        session.rollback()
        raise
    finally:
        result.latency_ms = int((time.monotonic() - started) * 1000)
        finished_at = datetime.now(UTC)
        session.add(
            SchedulerRun(
                job_id=job_id,
                started_at=started_at,
                finished_at=finished_at,
                ok=batch_error is None,
                error=batch_error,
                items_processed=result.processed_signals,
            )
        )
        session.commit()

    return result


# ───────────────────────── pipeline steps ──────────────────────────────


def _load_unclustered(session: Session, *, limit: int) -> list[tuple[SignalText, RawSignal]]:
    """Read unclustered signal_text rows joined with raw_signals for metadata.

    Skips `is_low_signal=True` rows (per feedback 016 Q6: deleted-post titles
    are too noisy for clustering input). Skips rows already linked via
    `candidate_signals` (per feedback 016 Q8: permanent-skip; manual delete
    is the re-cluster mechanism).
    """
    linked_ids_subq = select(CandidateSignal.raw_signal_id)
    rows = session.execute(
        select(SignalText, RawSignal)
        .join(RawSignal, RawSignal.id == SignalText.raw_signal_id)
        .where(SignalText.is_low_signal.is_(False))
        .where(SignalText.raw_signal_id.notin_(linked_ids_subq))
        .order_by(SignalText.id)
        .limit(limit)
    ).all()
    return [(r[0], r[1]) for r in rows]


def _haiku_prepass(
    llm_client: LLMClient, signals: list[tuple[SignalText, RawSignal]]
) -> list[_SignalForCluster]:
    """One mechanic_json("dedup", ...) call per signal. Returns enriched signals."""
    enriched: list[_SignalForCluster] = []
    system = "You normalize one signal into a structured pre-cluster representation."
    for st, _raw in signals:
        user_prompt = prompts.render("dedup_signal.j2", text=st.text, source_kind=st.source_kind)
        result = llm_client.mechanic_json(
            "dedup",
            system,
            [{"role": "user", "content": user_prompt}],
            schema=SignalCoreComplaint,
        )
        enriched.append(
            _SignalForCluster(
                raw_signal_id=st.raw_signal_id,
                text=st.text,
                source_kind=st.source_kind,
                social_proof_weight=st.social_proof_weight,
                core_complaint=result.core_complaint,
                vertical=result.vertical,
                keywords=result.keywords,
            )
        )
    return enriched


def _bucket(
    enriched: list[_SignalForCluster],
) -> dict[tuple[str, frozenset[str]], list[_SignalForCluster]]:
    """Group by (vertical, frozenset(keywords)). Deterministic ordering."""
    buckets: dict[tuple[str, frozenset[str]], list[_SignalForCluster]] = {}
    for sig in enriched:
        key = _bucket_key(sig.vertical, sig.keywords)
        buckets.setdefault(key, []).append(sig)
    return buckets


def _judge_buckets(
    llm_client: LLMClient,
    buckets: dict[tuple[str, frozenset[str]], list[_SignalForCluster]],
) -> list[tuple[IdeaCard, list[_SignalForCluster]]]:
    """Call judge_json on each bucket. Returns (idea_card, contributing_signal_objs)."""
    out: list[tuple[IdeaCard, list[_SignalForCluster]]] = []
    system = "You cluster signals into distinct unmet-need idea cards."
    for (vertical, keyword_set), bucket_signals in buckets.items():
        # Render with stable ordering — IDs sorted so cache-keys are stable.
        ordered = sorted(bucket_signals, key=lambda s: s.raw_signal_id)
        signal_dicts = [
            {
                "id": s.raw_signal_id,
                "source_kind": s.source_kind,
                "text": s.text,
                "social_proof_weight": s.social_proof_weight,
            }
            for s in ordered
        ]
        user_prompt = prompts.render(
            "cluster.j2",
            vertical=vertical,
            keyword_set=sorted(keyword_set),
            signals=signal_dicts,
        )
        bucket_result = llm_client.judge_json(
            "cluster",
            system,
            [{"role": "user", "content": user_prompt}],
            schema=ClusterOutput,
            cache_ttl="1h",
        )
        # Map raw_signal_ids back to enriched signal objects for persistence.
        by_id = {s.raw_signal_id: s for s in ordered}
        for card in bucket_result.clusters:
            contributing = [by_id[sid] for sid in card.contributing_signal_ids if sid in by_id]
            if not contributing:
                # LLM hallucinated ids or all ids are filtered. Skip rather
                # than persist an evidence-less candidate.
                logger.warning(
                    "cluster.card_without_evidence",
                    extra={
                        "cluster_warn": {
                            "problem_statement": card.problem_statement[:120],
                            "claimed_ids": card.contributing_signal_ids,
                        }
                    },
                )
                continue
            out.append((card, contributing))
    return out


def _persist_clusters(
    session: Session,
    cards: list[tuple[IdeaCard, list[_SignalForCluster]]],
    enriched: list[_SignalForCluster],
) -> tuple[int, int]:
    """Persist candidates + candidate_signals. Returns (inserted, linked).

    Dedup behavior per feedback 016 Q5: when a candidate with the same
    `dedup_key` exists (regardless of decision), link new signals to it
    rather than create a duplicate. Do NOT auto-flip the decision —
    HITL durability is the convention.
    """
    inserted = 0
    linked = 0
    for card, contributing in cards:
        dedup_key = _slugify(card.problem_statement)
        existing = session.execute(
            select(Candidate).where(Candidate.dedup_key == dedup_key)
        ).scalar_one_or_none()
        if existing is not None:
            candidate = existing
        else:
            candidate = Candidate(
                problem_statement=card.problem_statement,
                suspected_user=card.suspected_user,
                seed_keywords_json=list(card.seed_keywords),
                vertical=contributing[0].vertical if contributing else None,
                dedup_key=dedup_key,
                decision=Decision.PENDING,
                pipeline_stage=PipelineStage.NONE,
            )
            session.add(candidate)
            session.flush()
            inserted += 1

        for sig in contributing:
            already_linked = session.execute(
                select(CandidateSignal).where(
                    CandidateSignal.candidate_id == candidate.id,
                    CandidateSignal.raw_signal_id == sig.raw_signal_id,
                )
            ).scalar_one_or_none()
            if already_linked is not None:
                continue
            link = CandidateSignal(
                candidate_id=candidate.id,
                raw_signal_id=sig.raw_signal_id,
            )
            if try_insert(session, link):
                linked += 1
    session.commit()
    return inserted, linked


# ───────────────────── pass-2 merge (chunked buckets) ──────────────────


def _run_pass_2_merge(
    llm_client: LLMClient,
    pass1_clusters: dict[str, IdeaCard],
) -> dict[str, str]:
    """Pass-2 merge across chunks of a single oversized bucket.

    Receives only cluster *titles* + *seed_keywords* (per feedback 016 Q4:
    full evidence stays out of pass-2's input). Returns a merge map.

    Not wired into the main pipeline yet — single buckets exceeding ~150k
    tokens are rare for v1 volumes; this function exists as the scaffolding
    for when scale demands it.
    """
    system = "You merge already-formed clusters into canonical groups."
    user_prompt = prompts.render(
        "cluster_merge.j2",
        clusters=[
            {
                "id": cid,
                "problem_statement": card.problem_statement,
                "seed_keywords": card.seed_keywords,
            }
            for cid, card in sorted(pass1_clusters.items())
        ],
    )
    result = llm_client.judge_json(
        "cluster",
        system,
        [{"role": "user", "content": user_prompt}],
        schema=ClusterMergeOutput,
        cache_ttl="1h",
    )
    # Sanity: every pass-1 id must map to one of the pass-1 ids (canonical).
    valid_ids = set(pass1_clusters.keys())
    cleaned: dict[str, str] = {}
    for src, canonical in result.merge_map.items():
        if src in valid_ids and canonical in valid_ids:
            cleaned[src] = canonical
        else:
            logger.warning(
                "cluster_merge.invalid_id",
                extra={"cluster_merge_warn": {"src": src, "canonical": canonical}},
            )
    return cleaned


__all__ = [
    "ClusterMergeOutput",
    "ClusterOutput",
    "ClusterResult",
    "IdeaCard",
    "SignalCoreComplaint",
    "_run_pass_2_merge",  # exported so tests + future schedule wiring can reach it
    "cluster_signals",
]
