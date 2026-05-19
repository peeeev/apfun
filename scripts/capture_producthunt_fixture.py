"""Capture a real ProductHunt GraphQL response and save it as a contract-test fixture.

Mirrors `scripts/capture_reddit_fixture.py` / `capture_hn_fixture.py`. The
integration test (`tests/integration/test_producthunt_live.py`) does NOT write
fixtures — capture is a separate, intentional action.

Requires APFUN_PRODUCTHUNT_TOKEN (Client-only token).

Usage::

    APFUN_PRODUCTHUNT_TOKEN=... uv run python scripts/capture_producthunt_fixture.py
    APFUN_PRODUCTHUNT_TOKEN=... uv run python scripts/capture_producthunt_fixture.py \\
        --topic productivity --out tests/fixtures/producthunt/posts_productivity.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from apfun.config import settings

_DEFAULT_TOPIC = "developer-tools"
_DEFAULT_OUT = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "producthunt"
    / "posts_topic.json"
)
_GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"

_POSTS_QUERY = """
query Posts($first: Int!, $after: String, $featuredAfter: DateTime, $topic: String) {
  posts(first: $first, after: $after, featuredAfter: $featuredAfter, topic: $topic) {
    pageInfo { endCursor hasNextPage }
    edges {
      cursor
      node {
        id
        slug
        name
        tagline
        description
        url
        votesCount
        commentsCount
        featuredAt
        topics { edges { node { name slug } } }
        makers { edges { node { username } } }
      }
    }
  }
}
""".strip()


def _previous_meta(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        prior: dict[str, Any] = json.loads(path.read_text())
        meta = prior.get("_fixture_meta")
        return meta if isinstance(meta, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _build_meta(
    *, topic: str | None, prior_meta: dict[str, Any] | None, reason: str | None
) -> dict[str, Any]:
    today = datetime.now(UTC).date().isoformat()
    topic_label = f"topic: {topic!r}" if topic else "no topic filter (leaderboard surface)"
    meta: dict[str, Any] = {
        "captured": today,
        "refreshed": None,
        "source": f"POST {_GRAPHQL_URL} posts({topic_label})",
    }
    if prior_meta is not None:
        prior_captured = prior_meta.get("captured")
        suffix = f" — {reason}" if reason else ""
        meta["refreshed"] = f"{today} (was {prior_captured}){suffix}"
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--topic", default=_DEFAULT_TOPIC)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--n-days", type=int, default=7)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    parser.add_argument(
        "--reason",
        default=None,
        help="Short reason recorded in `_fixture_meta.refreshed` on refresh",
    )
    args = parser.parse_args()

    if not settings.producthunt_token:
        print(
            "APFUN_PRODUCTHUNT_TOKEN is required (Client-only developer token).",
            file=sys.stderr,
        )
        return 2

    featured_after_dt = datetime.now(UTC) - timedelta(days=args.n_days)
    featured_after = featured_after_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    variables: dict[str, Any] = {
        "first": args.page_size,
        "after": None,
        "featuredAfter": featured_after,
        "topic": args.topic,
    }
    request_body: dict[str, Any] = {"query": _POSTS_QUERY, "variables": variables}
    headers = {
        "Authorization": f"Bearer {settings.producthunt_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    with httpx.Client() as client:
        resp = client.post(_GRAPHQL_URL, json=request_body, headers=headers, timeout=30.0)
    resp.raise_for_status()
    body: dict[str, Any] = resp.json()

    prior_meta = _previous_meta(args.out)
    meta = _build_meta(topic=args.topic, prior_meta=prior_meta, reason=args.reason)

    fixture = {"_fixture_meta": meta, **body}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
    edges = (
        body.get("data", {}).get("posts", {}).get("edges", [])
        if isinstance(body.get("data"), dict)
        else []
    )
    print(f"Wrote {args.out} (topic={args.topic!r}, posts={len(edges)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
