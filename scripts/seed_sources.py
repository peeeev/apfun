"""Seed the `sources` table with the brief's core subreddits + HN query sets + verticals.

Idempotent: re-running is safe — existing sources (by (kind, name)) are skipped
rather than duplicated. Run once after `make init-db` to bootstrap ingest.

Usage::

    uv run python scripts/seed_sources.py
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.db import SessionLocal
from apfun.models import Source

# Per project-brief.md — the core SaaS-adjacent subs that anchor stage-1 ingest.
_CORE_SUBS = ["SaaS", "Entrepreneur", "smallbusiness"]

# Vertical placeholders — useful starting set for cross-niche signal. Edit as
# the funnel learns which verticals deserve more weight.
_VERTICAL_SUBS = [
    "indiehackers",
    "Startup_Ideas",
    "startups",
    "EntrepreneurRideAlong",
    "marketing",
    "SaaSy",
    "webdev",
    "agency",
    "consulting",
    "Accounting",
    "RealEstate",
    "Etsy",
    "ecommerce",
    "Shopify",
    "freelance",
]

# HN query bundles — opportunity-revealing phrasings from the task 006 spec.
# Each entry becomes one Source row; the source's queries run in sequence in
# a single `ingest()` call.
_HN_QUERY_BUNDLES: list[tuple[str, list[str]]] = [
    (
        "hn:wishes",
        [
            "tool you wish existed",
            "I wish there were",
            "what software is missing",
        ],
    ),
    (
        "hn:ask-hn",
        [
            "Ask HN: what tool",
            "Ask HN: what SaaS",
            "Ask HN: what software",
        ],
    ),
    (
        "hn:alternatives",
        [
            "alternatives to",
            "self-hosted alternative to",
            "open source alternative to",
        ],
    ),
]

# IndieHackers groups per task 008 spec. Each entry becomes one Source row;
# the source's `groups` config drives per-group fan-out inside a single
# `ingest()` call.
_IH_GROUPS: list[tuple[str, list[str]]] = [
    ("ih:main", ["main"]),
    ("ih:starting-up", ["starting-up"]),
    ("ih:ideas-and-validation", ["ideas-and-validation"]),
]

# Review-site sources per task 009 spec / feedback 014. Each entry becomes
# one Source row keyed by `(kind="review_sites", name="<site>:<slug>")`. Edit
# the products list per site to track what matters; the seeds below are
# placeholder anchors that match the synthetic fixtures.
_REVIEW_SITE_SOURCES: list[tuple[str, dict[str, Any]]] = [
    (
        "g2:asana",
        {
            "site": "g2",
            "products": [{"slug": "asana", "name": "Asana"}],
            "max_pages": 3,
            "min_star": 1,
            "max_star": 3,
        },
    ),
    (
        "capterra:asana",
        {
            "site": "capterra",
            "products": [{"slug": "asana", "name": "Asana"}],
            "max_pages": 3,
            "min_star": 1,
            "max_star": 3,
        },
    ),
    (
        "trustpilot:example",
        {
            "site": "trustpilot",
            "products": [{"slug": "example.com", "name": "Example"}],
            "max_pages": 3,
            "min_star": 1,
            "max_star": 3,
        },
    ),
]


# ProductHunt surfaces per task 007 spec / feedback 013 heads-up:
# - topic surface catches newer launches under specific verticals
# - leaderboard surface catches the high-attention curated set
_PH_SOURCES: list[tuple[str, dict[str, Any]]] = [
    (
        "ph:dev-tools-topic",
        {
            "surface": "topic",
            "topics": ["developer-tools", "productivity"],
            "n_days": 1,
            "min_votes_count": 10,
        },
    ),
    (
        "ph:daily-leaderboard",
        {
            "surface": "leaderboard",
            "leaderboard": "daily",
            "n_days": 1,
            "min_votes_count": 5,
        },
    ),
]


def _ensure_reddit_source(session: Session, name: str) -> bool:
    existing = session.execute(
        select(Source).where(Source.kind == "reddit", Source.name == f"r/{name}")
    ).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(
        Source(
            kind="reddit",
            name=f"r/{name}",
            config_json={"subreddits": [name], "fetch_kind": "new", "since_hours": 6},
            is_active=True,
        )
    )
    return True


def _ensure_hn_source(session: Session, name: str, queries: list[str]) -> bool:
    existing = session.execute(
        select(Source).where(Source.kind == "hn", Source.name == name)
    ).scalar_one_or_none()
    if existing is not None:
        return False
    config: dict[str, Any] = {
        "queries": queries,
        "since_hours": 24,
        "min_story_points": 3,
        "min_comment_points": 1,
    }
    session.add(Source(kind="hn", name=name, config_json=config, is_active=True))
    return True


def _ensure_ih_source(session: Session, name: str, groups: list[str]) -> bool:
    existing = session.execute(
        select(Source).where(Source.kind == "indiehackers", Source.name == name)
    ).scalar_one_or_none()
    if existing is not None:
        return False
    config: dict[str, Any] = {"groups": groups, "since_hours": 24}
    session.add(Source(kind="indiehackers", name=name, config_json=config, is_active=True))
    return True


def _ensure_review_source(session: Session, name: str, config: dict[str, Any]) -> bool:
    existing = session.execute(
        select(Source).where(Source.kind == "review_sites", Source.name == name)
    ).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(Source(kind="review_sites", name=name, config_json=config, is_active=True))
    return True


def _ensure_ph_source(session: Session, name: str, config: dict[str, Any]) -> bool:
    existing = session.execute(
        select(Source).where(Source.kind == "producthunt", Source.name == name)
    ).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(Source(kind="producthunt", name=name, config_json=config, is_active=True))
    return True


def main() -> int:
    inserted = 0
    skipped = 0
    with SessionLocal() as session:
        for name in _CORE_SUBS + _VERTICAL_SUBS:
            if _ensure_reddit_source(session, name):
                inserted += 1
            else:
                skipped += 1
        for name, queries in _HN_QUERY_BUNDLES:
            if _ensure_hn_source(session, name, queries):
                inserted += 1
            else:
                skipped += 1
        for name, groups in _IH_GROUPS:
            if _ensure_ih_source(session, name, groups):
                inserted += 1
            else:
                skipped += 1
        for name, config in _REVIEW_SITE_SOURCES:
            if _ensure_review_source(session, name, config):
                inserted += 1
            else:
                skipped += 1
        for name, config in _PH_SOURCES:
            if _ensure_ph_source(session, name, config):
                inserted += 1
            else:
                skipped += 1
        session.commit()
    print(f"Seeded sources: inserted={inserted}, skipped={skipped} (already present)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
