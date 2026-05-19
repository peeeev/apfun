"""Seed the `sources` table with the brief's core subreddits + vertical placeholders.

Idempotent: re-running is safe — existing sources (by (kind, name)) are skipped
rather than duplicated. Run once after `make init-db` to bootstrap Reddit ingest.

Usage::

    uv run python scripts/seed_sources.py
"""

from __future__ import annotations

from sqlalchemy import select

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


def _ensure_source(session, name: str) -> bool:  # type: ignore[no-untyped-def]
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


def main() -> int:
    inserted = 0
    skipped = 0
    with SessionLocal() as session:
        for name in _CORE_SUBS + _VERTICAL_SUBS:
            if _ensure_source(session, name):
                inserted += 1
            else:
                skipped += 1
        session.commit()
    print(f"Seeded sources: inserted={inserted}, skipped={skipped} (already present)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
