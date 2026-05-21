"""Per-source extractors: `raw_signals.payload_json` → `ExtractedText`.

Each extractor takes the source's payload dict and returns:

- `text`: combined title + body, whitespace-normalized
- `social_proof_weight`: non-negative float of raw weighted counts (Stage 4
  owns any bucketing/normalization decisions; do NOT scale to [0,1] here).
- `is_low_signal`: flag for rows clustering should skip or down-weight
  (Reddit `[deleted]`/`[removed]`, defensive against unexpected parse errors).

New source kinds slot in by adding a key in `EXTRACTORS` and a function above.
Mirrors the dispatch pattern in `apfun.sourcing.review_sites._common._get_adapter`.

Per task 010a + orchestrator feedback 015 Q1/Q2.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class ExtractedText:
    text: str
    social_proof_weight: float
    is_low_signal: bool


def _normalize_whitespace(s: str) -> str:
    """Collapse runs of whitespace to single spaces; strip ends."""
    return _WHITESPACE_RE.sub(" ", s).strip()


def _combine(*parts: str | None, sep: str = "\n\n") -> str:
    """Join non-empty parts with `sep`; collapse whitespace on the result."""
    return _normalize_whitespace(sep.join(p for p in parts if p))


# ─────────────────────────── per-source extractors ────────────────────────────


def extract_reddit(payload: dict[str, Any]) -> ExtractedText:
    """Reddit posts: title + selftext. Deletion-tagged rows → title-only + low-signal."""
    title = payload.get("title") or ""
    selftext = payload.get("selftext") or ""
    is_deleted = bool(payload.get("is_deleted"))

    text = _normalize_whitespace(title) if is_deleted else _combine(title, selftext)

    # heuristic 2026-05-19 — Reddit weight = score + 2 × num_comments. Comments
    # are stronger engagement signal than upvotes (write > vote). Negative
    # scores floor to 0 so a brigade-downvoted post doesn't go negative.
    # Retune trigger: ≥50 scores rows in Stage 4 (task 014).
    score = payload.get("score", 0)
    num_comments = payload.get("num_comments", 0)
    weight = float(max(int(score) if isinstance(score, int) else 0, 0)) + 2.0 * float(
        int(num_comments) if isinstance(num_comments, int) else 0
    )

    return ExtractedText(text=text, social_proof_weight=weight, is_low_signal=is_deleted)


def extract_hn(payload: dict[str, Any]) -> ExtractedText:
    """HN: stories use title+story_text; comments use comment_text alone."""
    raw_tags = payload.get("_tags")
    is_comment_tag = isinstance(raw_tags, list) and "comment" in raw_tags
    is_comment = is_comment_tag or bool(payload.get("comment_text"))

    if is_comment:
        text = _normalize_whitespace(payload.get("comment_text") or "")
    else:
        text = _combine(payload.get("title"), payload.get("story_text"))

    # heuristic 2026-05-19 — same shape as Reddit: points + 2 × num_comments.
    points = payload.get("points", 0)
    num_comments = payload.get("num_comments", 0)
    weight = float(int(points) if isinstance(points, int) else 0) + 2.0 * float(
        int(num_comments) if isinstance(num_comments, int) else 0
    )

    return ExtractedText(text=text, social_proof_weight=weight, is_low_signal=False)


def extract_producthunt(payload: dict[str, Any]) -> ExtractedText:
    """ProductHunt: name + tagline + description."""
    text = _combine(payload.get("name"), payload.get("tagline"), payload.get("description"))
    # heuristic 2026-05-19 — votes_count alone (PH UX surfaces upvotes;
    # commentsCount is a weaker signal there than on HN/Reddit).
    votes = payload.get("votesCount", 0)
    weight = float(int(votes) if isinstance(votes, int) else 0)
    return ExtractedText(text=text, social_proof_weight=weight, is_low_signal=False)


def extract_indiehackers(payload: dict[str, Any]) -> ExtractedText:
    """IndieHackers: title + rawBody. Body may be absent if HTML-fallback path was sparse."""
    text = _combine(payload.get("title"), payload.get("rawBody"))
    # heuristic 2026-05-19 — IH _NEXT_DATA__ exposes upvoteCount + replyCount.
    # Same shape as Reddit/HN; floor upvotes to 0.
    upvotes = payload.get("upvoteCount", 0)
    replies = payload.get("replyCount", 0)
    weight = float(max(int(upvotes) if isinstance(upvotes, int) else 0, 0)) + 2.0 * float(
        int(replies) if isinstance(replies, int) else 0
    )
    return ExtractedText(text=text, social_proof_weight=weight, is_low_signal=False)


def extract_review_sites(payload: dict[str, Any]) -> ExtractedText:
    """Review-site reviews: product_name — title + body."""
    product_name = payload.get("product_name") or ""
    title = payload.get("title") or ""
    body = payload.get("body") or ""
    head = f"{product_name} — {title}" if product_name and title else (product_name or title)
    text = _combine(head, body)
    # heuristic 2026-05-19 — helpful_count is the strongest "this matters"
    # signal for reviews (per task 009 spec / feedback 014 risk profile).
    # Treat as the sole weight; rating itself is a payload field but not a
    # social-proof signal.
    helpful = payload.get("helpful_count") or 0
    weight = float(int(helpful) if isinstance(helpful, int) else 0)
    return ExtractedText(text=text, social_proof_weight=weight, is_low_signal=False)


# ─────────────────────────────── dispatch table ───────────────────────────────


ExtractorFn = Callable[[dict[str, Any]], ExtractedText]

EXTRACTORS: dict[str, ExtractorFn] = {
    "reddit": extract_reddit,
    "hn": extract_hn,
    "producthunt": extract_producthunt,
    "indiehackers": extract_indiehackers,
    "review_sites": extract_review_sites,
}


def get_extractor(source_kind: str) -> ExtractorFn | None:
    """Return the extractor for a source kind, or None if unknown."""
    return EXTRACTORS.get(source_kind)


__all__ = [
    "EXTRACTORS",
    "ExtractedText",
    "ExtractorFn",
    "extract_hn",
    "extract_indiehackers",
    "extract_producthunt",
    "extract_reddit",
    "extract_review_sites",
    "get_extractor",
]
