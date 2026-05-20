"""CSV manual-import fallback for the review miner.

Per feedback 014 Q4 + task 009 Notes: if a review site repeatedly blocks
scraping, this script imports a hand-exported CSV into `raw_signals` with the
same payload shape as the scraping path. No bot-fighting, no proxies — just
a clean operator workflow when the edge wins.

CSV format (one row per review, header required):

    site,product_slug,product_name,review_id,title,body,rating,author,posted_at,helpful_count,permalink

Required columns: site, product_slug, product_name, body, rating. Others may
be blank. `posted_at` must be ISO-8601 (e.g. `2026-05-19T14:30:00Z`) when
present.

Usage::

    uv run python scripts/import_reviews.py path/to/reviews.csv
    uv run python scripts/import_reviews.py reviews.csv --source-name g2:asana-manual

When `--source-name` is omitted, the importer derives `"<site>:<product_slug>-manual"`
per row's site/slug. A source row is created on first encounter; subsequent
imports for the same `(kind="review_sites", name=...)` reuse it.

Deduplication is via the same `review_content_hash` used by scraping —
re-importing the same CSV produces zero new rows.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from apfun.db import SessionLocal
from apfun.models import RawSignal, Source
from apfun.sourcing.review_sites._common import ReviewDict, review_content_hash

_REQUIRED_COLUMNS = {"site", "product_slug", "product_name", "body", "rating"}


def _read_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"{csv_path} has no header row")
        missing = _REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"{csv_path} missing required columns: {sorted(missing)}. Got: {reader.fieldnames}"
            )
        return [dict(row) for row in reader]


def _parse_rating(raw: str) -> int:
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        return 0


def _parse_helpful(raw: str | None) -> int | None:
    if not raw or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _row_to_review(row: dict[str, str]) -> ReviewDict:
    return {
        "site": row["site"].strip(),
        "product_slug": row["product_slug"].strip(),
        "product_name": row["product_name"].strip(),
        "review_id": (row.get("review_id") or "").strip() or None,
        "title": (row.get("title") or "").strip() or None,
        "body": row["body"].strip(),
        "rating": _parse_rating(row["rating"]),
        "author": (row.get("author") or "").strip() or None,
        "posted_at": (row.get("posted_at") or "").strip() or None,
        "helpful_count": _parse_helpful(row.get("helpful_count")),
        "permalink": (row.get("permalink") or "").strip() or None,
    }


def _ensure_source(session: Any, site: str, product_slug: str, source_name: str | None) -> Source:
    name = source_name if source_name else f"{site}:{product_slug}-manual"
    existing = session.execute(
        select(Source).where(Source.kind == "review_sites", Source.name == name)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    src = Source(
        kind="review_sites",
        name=name,
        config_json={
            "site": site,
            "products": [{"slug": product_slug, "name": product_slug}],
            "_apfun_origin": "manual_csv",
        },
        is_active=True,
    )
    session.add(src)
    session.flush()
    return src


def _captured_at(posted_at: str | None) -> datetime:
    if not posted_at:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)


def _insert_review(session: Any, source: Source, review: ReviewDict) -> bool:
    body = review.get("body") or ""
    site = review.get("site") or ""
    product_slug = review.get("product_slug") or ""
    digest = review_content_hash(
        site,
        product_slug,
        review.get("review_id"),
        rating=review.get("rating"),
        posted_at=review.get("posted_at"),
        author=review.get("author"),
        body=body,
    )
    review_id = review.get("review_id")
    external_id = review_id if review_id else digest[:32]
    permalink = review.get("permalink")

    signal = RawSignal(
        source_id=source.id,
        external_id=external_id,
        url=permalink if permalink else None,
        captured_at=_captured_at(review.get("posted_at")),
        content_hash=digest,
        payload_json=dict(review),
    )
    session.add(signal)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return False
    return True


def import_csv(csv_path: Path, source_name: str | None = None) -> tuple[int, int]:
    """Import reviews from `csv_path`. Returns (inserted, skipped_as_duplicate)."""
    rows = _read_rows(csv_path)
    inserted = 0
    skipped = 0
    with SessionLocal() as session:
        # Cache sources per (site, slug) so we don't query the DB once per row.
        source_cache: dict[tuple[str, str], Source] = {}
        for row in rows:
            review = _row_to_review(row)
            site = review.get("site") or ""
            slug = review.get("product_slug") or ""
            body = review.get("body") or ""
            if not site or not slug or not body:
                skipped += 1
                continue
            key = (site, slug)
            if key not in source_cache:
                source_cache[key] = _ensure_source(session, site, slug, source_name)
            if _insert_review(session, source_cache[key], review):
                inserted += 1
            else:
                skipped += 1
        session.commit()
    return inserted, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("csv_path", type=Path, help="CSV file of reviews to import")
    parser.add_argument(
        "--source-name",
        default=None,
        help="Override the Source name (default: '<site>:<product_slug>-manual')",
    )
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"{args.csv_path} not found", file=sys.stderr)
        return 2

    try:
        inserted, skipped = import_csv(args.csv_path, args.source_name)
    except ValueError as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1

    print(f"Imported {inserted} reviews; skipped {skipped} (duplicate or invalid).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
