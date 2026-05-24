"""Dump the signals Haiku judged non-clusterable, for the null-rate diagnosis.

Read-only. Surfaces `signal_text` rows where `is_low_signal=True` was set by
the Stage 1 Haiku pre-pass (returned `core_complaint=null`) — distinguished
from *structurally* low-signal rows (Reddit `[deleted]`/`[removed]`, set at
normalize time) by excluding those exact-match marker texts.

Output: TSV to stdout (or `--out FILE`) with a blank `operator_judgment`
column for the operator to fill in per row — `correct_null` / `missed_complaint`
/ `unclear`. See `docs/operator/runbooks/004-stage1-null-rate-diagnosis.md`.

This is a one-time diagnostic, not production code. It carries its own inline
source-identifier extraction (a ~15-line echo of
`apfun/pipeline/_source_identifier.py`) so the script stays independent of the
task 014-fix-1 branch — no cross-PR import. Per orchestrator request 027/028.

Usage::

    uv run python scripts/dump_nulled_signals.py > /tmp/nulled-signals.tsv
    uv run python scripts/dump_nulled_signals.py --out /tmp/nulled.tsv
"""

from __future__ import annotations

import argparse
import csv
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.db import SessionLocal
from apfun.models import RawSignal, SignalText

# Exact-match texts that mean "structurally low-signal" (set at normalize time,
# NOT by Haiku). Excluding these isolates the Haiku-judged nulls. Per request
# 027 §Distinguishing structural-low-signal — option (b), no schema migration.
_STRUCTURAL_MARKERS = ("[deleted]", "[removed]")

_PREVIEW_CHARS = 500


def _source_identifier(source_kind: str, payload_json: dict[str, Any] | None) -> str:
    """Inline echo of apfun.pipeline._source_identifier (see module docstring)."""
    payload = payload_json or {}
    if source_kind == "reddit":
        sub = payload.get("subreddit")
        return f"r/{sub}" if sub else "reddit"
    if source_kind == "hn":
        q = payload.get("_apfun_query")
        return f"hn:{q}" if q else "hn"
    if source_kind == "producthunt":
        surface = payload.get("_apfun_surface")
        return f"ph:{surface}" if surface else "producthunt"
    if source_kind == "indiehackers":
        group = payload.get("_apfun_group")
        return f"ih:{group}" if group else "indiehackers"
    if source_kind == "review_sites":
        site = payload.get("site")
        slug = payload.get("product_slug")
        if site and slug:
            return f"{site}:{slug}"
        return str(site) if site else "review_sites"
    return source_kind


def collect_nulled(session: Session) -> list[dict[str, Any]]:
    """Return Haiku-nulled signals as row dicts (read-only)."""
    rows = session.execute(
        select(
            SignalText.id,
            SignalText.source_kind,
            SignalText.text,
            RawSignal.url,
            RawSignal.payload_json,
        )
        .join(RawSignal, RawSignal.id == SignalText.raw_signal_id)
        .where(
            SignalText.is_low_signal.is_(True),
            SignalText.text.notin_(_STRUCTURAL_MARKERS),
        )
        .order_by(SignalText.source_kind, SignalText.id)
    ).all()
    out: list[dict[str, Any]] = []
    for sid, kind, text, url, payload in rows:
        out.append(
            {
                "signal_text_id": sid,
                "source_kind": kind,
                "source_identifier": _source_identifier(kind, payload),
                "text_preview": (text or "")[:_PREVIEW_CHARS].replace("\n", " ").replace("\t", " "),
                "url": url or "",
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--out", default=None, help="Write TSV here instead of stdout")
    args = parser.parse_args()

    import io

    with SessionLocal() as session:
        rows = collect_nulled(session)

    buf = io.StringIO()
    writer = csv.writer(buf, dialect="excel-tab")
    writer.writerow(
        [
            "signal_text_id",
            "source_kind",
            "source_identifier",
            "text_preview",
            "url",
            "operator_judgment",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r["signal_text_id"],
                r["source_kind"],
                r["source_identifier"],
                r["text_preview"],
                r["url"],
                "",  # operator fills: correct_null / missed_complaint / unclear
            ]
        )

    tsv = buf.getvalue()
    if args.out:
        with open(args.out, "w", newline="") as fh:
            fh.write(tsv)
    else:
        sys.stdout.write(tsv)

    print(f"dumped {len(rows)} Haiku-nulled signals", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
