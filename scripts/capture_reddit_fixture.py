"""Capture a real Reddit listing JSON and save it as a contract-test fixture.

Mirrors `scripts/capture_response_fixture.py` for the Anthropic fixture. The
integration test (`tests/integration/test_reddit_live.py`) does NOT write
fixtures — capture is a separate, intentional action so it's easy to reason
about *why* a fixture changed. Per orchestrator feedback 011 Q3.

Uses the OAuth2 path (task 005b, 2026-05-22) — datacenter IPs were 403'd on
the anonymous path. Requires `APFUN_REDDIT_CLIENT_ID` +
`APFUN_REDDIT_CLIENT_SECRET` + `APFUN_REDDIT_USERNAME`. Register a "script"
app at https://www.reddit.com/prefs/apps.

Usage::

    APFUN_REDDIT_USERNAME=your_handle \\
    APFUN_REDDIT_CLIENT_ID=... APFUN_REDDIT_CLIENT_SECRET=... \\
        uv run python scripts/capture_reddit_fixture.py

    ... uv run python scripts/capture_reddit_fixture.py \\
        --subreddit programming --kind new --out tests/fixtures/reddit/listing_programming.json

When refreshing an existing fixture, the script reads the previous file's
`_fixture_meta` and forwards the prior `captured` date into the new file's
`refreshed` slot — the three-line audit trail makes "why did this fixture
change?" answerable without git archaeology (per feedback 010 Q1).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from apfun.config import settings
from apfun.sourcing.reddit import _REDDIT_OAUTH_API_BASE, _get_auth

_DEFAULT_SUBREDDIT = "SaaS"
_DEFAULT_KIND = "new"
_DEFAULT_OUT = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "reddit" / "listing_saas.json"
)
_LISTING_PATH_TEMPLATE = "/r/{subreddit}/{kind}.json"


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
    *, subreddit: str, kind: str, prior_meta: dict[str, Any] | None, reason: str | None
) -> dict[str, Any]:
    today = datetime.now(UTC).date().isoformat()
    path = _LISTING_PATH_TEMPLATE.format(subreddit=subreddit, kind=kind)
    meta: dict[str, Any] = {
        "captured": today,
        "refreshed": None,
        "source": f"GET {_REDDIT_OAUTH_API_BASE}{path} (oauth)",
    }
    if prior_meta is not None:
        prior_captured = prior_meta.get("captured")
        suffix = f" — {reason}" if reason else ""
        meta["refreshed"] = f"{today} (was {prior_captured}){suffix}"
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--subreddit", default=_DEFAULT_SUBREDDIT)
    parser.add_argument("--kind", default=_DEFAULT_KIND, choices=["new", "top", "hot", "rising"])
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT, help="Output JSON path")
    parser.add_argument(
        "--reason",
        default=None,
        help="Short reason recorded in `_fixture_meta.refreshed` on refresh",
    )
    args = parser.parse_args()

    if not settings.reddit_username:
        print(
            "APFUN_REDDIT_USERNAME is required (Reddit's UA still demands the "
            "`by /u/<handle>` suffix on OAuth requests).",
            file=sys.stderr,
        )
        return 2
    if not settings.reddit_client_id or not settings.reddit_client_secret:
        print(
            "APFUN_REDDIT_CLIENT_ID + APFUN_REDDIT_CLIENT_SECRET are required. "
            "Register a 'script' app at https://www.reddit.com/prefs/apps.",
            file=sys.stderr,
        )
        return 2

    user_agent = f"apfun-funnel:v0.1 (by /u/{settings.reddit_username})"
    path = _LISTING_PATH_TEMPLATE.format(subreddit=args.subreddit, kind=args.kind)
    url = f"{_REDDIT_OAUTH_API_BASE}{path}"

    with httpx.Client() as client:
        # Routes through the production auth singleton so the OAuth token
        # acquisition path is exercised end-to-end (same as ingest()).
        auth = _get_auth()
        token = auth.get_token(client)
        resp = client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": user_agent,
            },
            timeout=30.0,
        )
    resp.raise_for_status()
    body: dict[str, Any] = resp.json()

    prior_meta = _previous_meta(args.out)
    meta = _build_meta(
        subreddit=args.subreddit, kind=args.kind, prior_meta=prior_meta, reason=args.reason
    )

    fixture = {"_fixture_meta": meta, **body}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {args.out} (subreddit={args.subreddit}, kind={args.kind})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
