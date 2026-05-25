"""Candidate merge (task 014-fix-2 / orchestrator request 031).

The operator selects N candidates that describe the same underlying problem;
Opus synthesizes one unified card; the N sources are soft-deleted (their
`merged_into_id` set to the new candidate) and their contributing signals are
re-pointed to the new candidate. The merged candidate is always `pending` —
merging is a decision-changing action that demands fresh review (HITL durability,
feedback 016).

Reversibility: not in v1. The `merged_into_id` chain + `created_at` is the audit
trail. An "unmerge" is a future feature if friction arises.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from apfun.llm import prompts
from apfun.models import (
    Buildability,
    Candidate,
    CandidateSignal,
    Decision,
    PipelineStage,
)

_SYSTEM = "You merge several opportunity cards that describe the same problem into one."


class _Merger(Protocol):
    """The slice of LLMClient the merge needs (lets tests stub it cleanly)."""

    def judge_json(
        self,
        task: str,
        system: str,
        messages: list[dict[str, Any]],
        *,
        schema: type[MergedCard],
    ) -> MergedCard: ...


class MergeError(ValueError):
    """Raised when a merge request is invalid (too few, missing, or already-merged
    candidates). The web layer maps this to a 400."""


class MergedCard(BaseModel):
    """Opus output for a merge. Mirrors the buildability-bearing IdeaCard shape
    minus `contributing_signal_ids` (signals come from the DB, not the LLM).
    `buildability` is required (no default) so an omission fails validation."""

    problem_statement: str
    suspected_user: str | None = None
    seed_keywords: list[str] = Field(default_factory=lambda: list[str]())
    buildability: Buildability
    buildability_rationale: str


def _slug(text: str, *, max_len: int = 120) -> str:
    """Lowercase ascii-folded hyphenated slug for the readable part of a merged
    dedup_key. Local (not cluster's `_slugify`) to keep this module decoupled."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return cleaned[:max_len] if cleaned else "merged"


def _merged_dedup_key(problem_statement: str, source_ids: list[int]) -> str:
    """Collision-proof dedup_key for a merged candidate.

    Prefixed with the sorted source ids: a source candidate is soft-deleted after
    one merge so it can never be a source again, making the id-set unique. Avoids
    colliding with the sources' own (unprefixed) dedup_keys under the UNIQUE
    constraint.
    """
    ids = "-".join(str(i) for i in sorted(set(source_ids)))
    return f"merge-{ids}-{_slug(problem_statement)}"


def merge_candidates(
    session: Session,
    *,
    llm_client: _Merger,
    candidate_ids: list[int],
) -> Candidate:
    """Merge N candidates into one new pending candidate. Returns the new candidate.

    Validates (>=2 distinct, all exist, none already-merged), asks Opus for the
    unified card, then persists atomically: insert the new candidate, re-link the
    DISTINCT contributing signals (dedup so a shared signal isn't double-linked —
    `candidate_signals` has a composite PK), and soft-delete the sources. A
    failure before commit rolls back, leaving no partial state.
    """
    distinct_ids = sorted(set(candidate_ids))
    if len(distinct_ids) < 2:
        raise MergeError("merge requires at least 2 distinct candidates")

    candidates = (
        session.execute(select(Candidate).where(Candidate.id.in_(distinct_ids))).scalars().all()
    )
    found = {c.id for c in candidates}
    missing = set(distinct_ids) - found
    if missing:
        raise MergeError(f"candidates not found: {sorted(missing)}")
    already_merged = sorted(c.id for c in candidates if c.merged_into_id is not None)
    if already_merged:
        raise MergeError(f"cannot merge already-merged candidates: {already_merged}")

    # Opus synthesis happens BEFORE the DB transaction — it's a network call and
    # mustn't hold a write lock. On failure nothing is persisted.
    user_prompt = prompts.render(
        "merge_candidates.j2",
        candidates=[
            {
                "id": c.id,
                "problem_statement": c.problem_statement,
                "suspected_user": c.suspected_user,
                "seed_keywords": c.seed_keywords_json,
            }
            for c in candidates
        ],
    )
    merged = llm_client.judge_json(
        "merge",
        _SYSTEM,
        [{"role": "user", "content": user_prompt}],
        schema=MergedCard,
    )

    # First non-null vertical among the sources (in id order) is a reasonable
    # default for the merged card; verticals are advisory.
    vertical = next((c.vertical for c in candidates if c.vertical), None)

    try:
        new = Candidate(
            problem_statement=merged.problem_statement,
            suspected_user=merged.suspected_user,
            seed_keywords_json=list(merged.seed_keywords)[:8],
            vertical=vertical,
            dedup_key=_merged_dedup_key(merged.problem_statement, distinct_ids),
            decision=Decision.PENDING,
            pipeline_stage=PipelineStage.NONE,
            buildability=merged.buildability,
            buildability_rationale=merged.buildability_rationale,
            buildability_assessed_at=datetime.now(UTC),
        )
        session.add(new)
        session.flush()

        # Re-link DISTINCT signals: gather, delete the source links, insert one
        # link per distinct raw_signal_id (composite-PK-safe; weight = sum over
        # distinct signals, so a shared signal isn't counted twice).
        distinct_rsids = (
            session.execute(
                select(CandidateSignal.raw_signal_id)
                .where(CandidateSignal.candidate_id.in_(distinct_ids))
                .distinct()
            )
            .scalars()
            .all()
        )
        session.execute(
            delete(CandidateSignal).where(CandidateSignal.candidate_id.in_(distinct_ids))
        )
        for rsid in distinct_rsids:
            session.add(CandidateSignal(candidate_id=new.id, raw_signal_id=rsid))

        # Soft-delete the sources. Their own decision is preserved (a rejected
        # source stays rejected on its soft-deleted row — no silent flip).
        for c in candidates:
            c.merged_into_id = new.id

        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(new)
    return new
