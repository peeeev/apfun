"""Capture a real IndieHackers grouppage and save it as a contract-test fixture.

Mirrors `scripts/capture_*_fixture.py` for the other sources. The integration
test (`tests/integration/test_indiehackers_live.py`) does NOT write fixtures —
capture is an intentional, separate action.

If IndieHackers is Cloudflare-challenging the request, this script will fail
loudly (403/429 raise_for_status) and the operational call (per task 008
Notes) is to park IH as a source.

Usage::

    uv run python scripts/capture_indiehackers_fixture.py
    uv run python scripts/capture_indiehackers_fixture.py --group starting-up \\
        --out tests/fixtures/indiehackers/grouppage_starting_up.html
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

_DEFAULT_GROUP = "main"
_DEFAULT_OUT = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "indiehackers"
    / "grouppage_main.html"
)
_GROUPPAGE_URL_TEMPLATE = "https://www.indiehackers.com/grouppage/{group}"
_USER_AGENT = "apfun-funnel/0.1 (https://apfun.online)"


def _previous_meta_block(path: Path) -> dict[str, Any] | None:
    """The IH fixture is HTML; we store `_fixture_meta` inside an HTML comment at the top."""
    if not path.exists():
        return None
    text = path.read_text()
    marker = "_fixture_meta:"
    if marker not in text:
        return None
    after = text.split(marker, 1)[1]
    # Find the JSON object between the next "{" and the matching closing "}".
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
    *, group: str, prior_meta: dict[str, Any] | None, reason: str | None
) -> str:
    today = datetime.now(UTC).date().isoformat()
    meta: dict[str, Any] = {
        "captured": today,
        "refreshed": None,
        "source": f"GET {_GROUPPAGE_URL_TEMPLATE.format(group=group)}",
    }
    if prior_meta is not None:
        prior_captured = prior_meta.get("captured")
        suffix = f" — {reason}" if reason else ""
        meta["refreshed"] = f"{today} (was {prior_captured}){suffix}"
    return f"<!--\n_fixture_meta:\n{json.dumps(meta, indent=2)}\n-->\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--group", default=_DEFAULT_GROUP)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    parser.add_argument(
        "--reason",
        default=None,
        help="Short reason recorded in `_fixture_meta.refreshed` on refresh",
    )
    args = parser.parse_args()

    url = _GROUPPAGE_URL_TEMPLATE.format(group=args.group)
    with httpx.Client() as client:
        resp = client.get(url, headers={"User-Agent": _USER_AGENT}, timeout=30.0)
    resp.raise_for_status()
    html_body = resp.text

    prior_meta = _previous_meta_block(args.out)
    meta_comment = _build_meta_comment(group=args.group, prior_meta=prior_meta, reason=args.reason)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(meta_comment + html_body)
    print(f"Wrote {args.out} (group={args.group!r}, html_bytes={len(html_body)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
