"""Derive a human-readable source identifier from a `raw_signals` payload.

Each ingester tags its `payload_json` differently, so "where did this signal
come from" is a per-source-kind lookup:

- reddit       → `r/<subreddit>`            (payload `subreddit`)
- hn           → `hn:<query>`               (payload `_apfun_query`)
- producthunt  → `ph:<surface>`             (payload `_apfun_surface`)
- indiehackers → `ih:<group>`               (payload `_apfun_group`)
- review_sites → `<site>:<product_slug>`    (payload `site` + `product_slug`)

Used by the inbox source badges (task 014-fix-1). Runbook 004's diagnostic
script needs the same mapping but carries its own inline copy to stay an
independent one-time-use script (per request 028 — no cross-PR import).
Falls back to the bare `source_kind` when the expected payload key is absent.
"""

from __future__ import annotations

from typing import Any


def source_identifier(source_kind: str, payload_json: dict[str, Any] | None) -> str:
    """Best-effort human-readable origin label for one signal."""
    payload = payload_json or {}
    if source_kind == "reddit":
        sub = payload.get("subreddit")
        return f"r/{sub}" if sub else "reddit"
    if source_kind == "hn":
        query = payload.get("_apfun_query")
        return f"hn:{query}" if query else "hn"
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
