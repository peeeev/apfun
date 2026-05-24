"""Tests for the null-rate diagnostic dump script (runbook 004 / request 027)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from apfun.models import RawSignal, SignalText, Source
from scripts.dump_nulled_signals import _source_identifier, collect_nulled


@pytest.mark.parametrize(
    ("kind", "payload", "expected"),
    [
        ("reddit", {"subreddit": "SaaS"}, "r/SaaS"),
        ("hn", {"_apfun_query": "wishes"}, "hn:wishes"),
        ("producthunt", {"_apfun_surface": "topics"}, "ph:topics"),
        ("indiehackers", {"_apfun_group": "starting-up"}, "ih:starting-up"),
        ("review_sites", {"site": "g2", "product_slug": "asana"}, "g2:asana"),
        ("reddit", {}, "reddit"),
        ("mastodon", {"x": 1}, "mastodon"),
    ],
)
def test_inline_source_identifier(kind: str, payload: dict, expected: str) -> None:
    assert _source_identifier(kind, payload) == expected


def _add_signal(session: Session, *, kind: str, text: str, is_low: bool, payload: dict) -> None:
    src = Source(kind=kind, name=f"{kind}:{text[:8]}", config_json={})
    session.add(src)
    session.flush()
    raw = RawSignal(
        source_id=src.id,
        external_id=f"ext-{id(text)}",
        url="https://example.com/x",
        captured_at=datetime.now(UTC),
        content_hash=f"h-{id(text)}",
        payload_json=payload,
    )
    session.add(raw)
    session.flush()
    session.add(
        SignalText(
            raw_signal_id=raw.id,
            source_kind=kind,
            text=text,
            social_proof_weight=1.0,
            is_low_signal=is_low,
            extracted_at=datetime.now(UTC),
        )
    )
    session.flush()


def test_collect_nulled_includes_only_haiku_judged(session: Session) -> None:
    # Haiku-judged null (substantive text, low_signal=True) → included.
    _add_signal(
        session,
        kind="reddit",
        text="What's the best billing tool?",
        is_low=True,
        payload={"subreddit": "SaaS"},
    )
    # Structural low-signal ([deleted]) → excluded.
    _add_signal(session, kind="reddit", text="[deleted]", is_low=True, payload={"subreddit": "x"})
    _add_signal(session, kind="reddit", text="[removed]", is_low=True, payload={"subreddit": "y"})
    # Not low-signal at all (clustered fine) → excluded.
    _add_signal(
        session,
        kind="hn",
        text="Stripe proration is broken",
        is_low=False,
        payload={"_apfun_query": "wishes"},
    )
    session.commit()

    rows = collect_nulled(session)
    texts = [r["text_preview"] for r in rows]
    assert texts == ["What's the best billing tool?"]
    assert rows[0]["source_identifier"] == "r/SaaS"
    assert rows[0]["source_kind"] == "reddit"


def test_collect_nulled_truncates_and_flattens_preview(session: Session) -> None:
    long_text = "complaint " * 200  # ~1800 chars, with newlines added below
    _add_signal(
        session,
        kind="hn",
        text=long_text + "\nline2\ttab",
        is_low=True,
        payload={"_apfun_query": "tools"},
    )
    session.commit()

    rows = collect_nulled(session)
    assert len(rows) == 1
    preview = rows[0]["text_preview"]
    assert len(preview) <= 500
    assert "\n" not in preview and "\t" not in preview


def test_collect_nulled_empty_when_nothing_judged(session: Session) -> None:
    _add_signal(
        session,
        kind="hn",
        text="a real complaint",
        is_low=False,
        payload={"_apfun_query": "q"},
    )
    session.commit()
    assert collect_nulled(session) == []
