"""Capture a real review-site page HTML and save it as a contract-test fixture.

One script for all three sites (G2 / Capterra / Trustpilot). The integration
test (`tests/integration/test_review_sites_live.py`) does NOT write fixtures —
capture is a separate, intentional action.

If a site is actively blocking Playwright (Cloudflare challenge, residential-IP
fingerprinting), this script will fail loudly. Per task 009 Notes + feedback
014 risk profile: the answer is `scripts/import_reviews.py`, not stealth-mode
arms races.

Usage::

    uv run python scripts/capture_review_fixture.py --site g2 --slug asana
    uv run python scripts/capture_review_fixture.py --site capterra --slug zendesk
    uv run python scripts/capture_review_fixture.py --site trustpilot --slug example.com
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SUPPORTED_SITES = ("g2", "capterra", "trustpilot")
_FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "review_sites"


def _url_for(site: str, slug: str, stars: str) -> str:
    if site == "g2":
        return f"https://www.g2.com/products/{slug}/reviews?filter={stars}&page=1"
    if site == "capterra":
        return f"https://www.capterra.com/p/{slug}/reviews/?rating={stars}&page=1"
    if site == "trustpilot":
        # Trustpilot uses repeated stars params.
        from urllib.parse import urlencode

        params = [("stars", s) for s in stars.split(",")] + [("page", "1")]
        return f"https://www.trustpilot.com/review/{slug}?{urlencode(params)}"
    raise ValueError(f"unknown site: {site}")


def _previous_meta_block(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    text = path.read_text()
    marker = "_fixture_meta:"
    if marker not in text:
        return None
    after = text.split(marker, 1)[1]
    start = after.find("{")
    if start == -1:
        return None
    depth = 0
    end = -1
    for i, ch in enumerate(after[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return None
    try:
        return json.loads(after[start:end])
    except json.JSONDecodeError:
        return None


def _build_meta_comment(
    *, site: str, slug: str, url: str, prior_meta: dict[str, Any] | None, reason: str | None
) -> str:
    today = datetime.now(UTC).date().isoformat()
    meta: dict[str, Any] = {
        "captured": today,
        "refreshed": None,
        "source": f"GET {url} (site={site}, slug={slug})",
    }
    if prior_meta is not None:
        prior_captured = prior_meta.get("captured")
        suffix = f" — {reason}" if reason else ""
        meta["refreshed"] = f"{today} (was {prior_captured}){suffix}"
    return f"<!--\n_fixture_meta:\n{json.dumps(meta, indent=2)}\n-->\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--site", required=True, choices=_SUPPORTED_SITES)
    parser.add_argument("--slug", required=True, help="Product slug (e.g. asana, zendesk)")
    parser.add_argument("--stars", default="1,2,3", help="Star filter as CSV (default 1,2,3)")
    parser.add_argument("--out", type=Path, default=None, help="Output HTML path")
    parser.add_argument("--reason", default=None, help="Reason recorded in refreshed line")
    args = parser.parse_args()

    out_path: Path = (
        args.out
        if args.out is not None
        else _FIXTURE_ROOT / args.site / f"{args.slug.replace('.', '_')}_page1.html"
    )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright not installed. Run scripts/setup_playwright.py --install.", file=sys.stderr
        )
        return 2

    url = _url_for(args.site, args.slug, args.stars)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:  # noqa: BLE001
            print(f"navigation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            browser.close()
            return 1
        if resp is not None and resp.status >= 400:
            print(
                f"non-success status {resp.status} for {url} — site may be blocking us.\n"
                "Per task 009: if this persists, use scripts/import_reviews.py.",
                file=sys.stderr,
            )
            browser.close()
            return 1
        html_body = page.content()
        browser.close()

    prior = _previous_meta_block(out_path)
    meta_comment = _build_meta_comment(
        site=args.site, slug=args.slug, url=url, prior_meta=prior, reason=args.reason
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(meta_comment + html_body)
    print(f"Wrote {out_path} ({len(html_body)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
