"""Capture a real HN Algolia search response and save it as a contract-test fixture.

Mirrors `scripts/capture_reddit_fixture.py`. The integration test
(`tests/integration/test_hn_live.py`) does NOT write fixtures — capture is a
separate, intentional action so it's easy to reason about *why* a fixture
changed.

Usage::

    uv run python scripts/capture_hn_fixture.py
    uv run python scripts/capture_hn_fixture.py --query "tool you wish existed" \\
        --out tests/fixtures/hn/search_ask_hn.json

When refreshing an existing fixture, the script reads the previous file's
`_fixture_meta` and forwards the prior `captured` date into the new file's
`refreshed` slot (same pattern as the Reddit capture script).
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

_DEFAULT_QUERY = "tool you wish existed"
_DEFAULT_TAGS = "(story,comment)"
_DEFAULT_OUT = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "hn" / "search_ask_hn.json"
)
_ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
_USER_AGENT = "apfun-funnel/0.1 (https://apfun.online)"


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
    *, query: str, tags: str, prior_meta: dict[str, Any] | None, reason: str | None
) -> dict[str, Any]:
    today = datetime.now(UTC).date().isoformat()
    source = f"GET {_ALGOLIA_SEARCH_URL}?query={query}&tags={tags}"
    meta: dict[str, Any] = {"captured": today, "refreshed": None, "source": source}
    if prior_meta is not None:
        prior_captured = prior_meta.get("captured")
        suffix = f" — {reason}" if reason else ""
        meta["refreshed"] = f"{today} (was {prior_captured}){suffix}"
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--query", default=_DEFAULT_QUERY)
    parser.add_argument("--tags", default=_DEFAULT_TAGS)
    parser.add_argument("--since-hours", type=int, default=168)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    parser.add_argument(
        "--reason",
        default=None,
        help="Short reason recorded in `_fixture_meta.refreshed` on refresh",
    )
    args = parser.parse_args()

    cutoff = int(time.time()) - args.since_hours * 3600
    params: dict[str, str] = {
        "query": args.query,
        "tags": args.tags,
        "hitsPerPage": "50",
        "numericFilters": f"created_at_i>{cutoff}",
    }

    with httpx.Client() as client:
        resp = client.get(
            _ALGOLIA_SEARCH_URL, params=params, headers={"User-Agent": _USER_AGENT}, timeout=30.0
        )
    resp.raise_for_status()
    body: dict[str, Any] = resp.json()

    prior_meta = _previous_meta(args.out)
    meta = _build_meta(query=args.query, tags=args.tags, prior_meta=prior_meta, reason=args.reason)

    fixture = {"_fixture_meta": meta, **body}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {args.out} (query={args.query!r}, hits={len(body.get('hits', []))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
