"""Regression tests for `apfun.db.try_insert`.

The bug fix this pins: on `IntegrityError`, `try_insert` uses a SAVEPOINT
(`session.begin_nested()`) so only the failing insert is rolled back. A bare
`session.rollback()` would roll back the whole transaction — including every
prior successful insert in the same batch — and silently destroy data the
caller thinks persisted.

Surfaced by runbook 001 on 2026-05-22 against real HN data: `ingest_batch`
reported `items_captured=11` but a fresh session saw 0 rows.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.db import try_insert
from apfun.models import RawSignal, Source


def _make_signal(source_id: int, content_hash: str, external_id: str) -> RawSignal:
    return RawSignal(
        source_id=source_id,
        external_id=external_id,
        url="https://example.com",
        captured_at=datetime.now(UTC),
        content_hash=content_hash,
        payload_json={"text": "x"},
    )


def test_try_insert_returns_true_on_success(session: Session) -> None:
    src = Source(kind="reddit", name="r/a", config_json={})
    session.add(src)
    session.flush()

    assert try_insert(session, _make_signal(src.id, "h-1", "ext-1")) is True
    session.commit()

    rows = session.execute(select(RawSignal)).scalars().all()
    assert len(rows) == 1


def test_try_insert_returns_false_on_unique_collision(session: Session) -> None:
    src = Source(kind="reddit", name="r/a", config_json={})
    session.add(src)
    session.flush()

    assert try_insert(session, _make_signal(src.id, "h-dup", "ext-1")) is True
    # Same content_hash → UNIQUE constraint violation.
    assert try_insert(session, _make_signal(src.id, "h-dup", "ext-2")) is False
    session.commit()

    rows = session.execute(select(RawSignal)).scalars().all()
    assert len(rows) == 1, "the first row must persist; only the duplicate is skipped"


def test_intra_batch_collision_does_not_destroy_prior_inserts(session: Session) -> None:
    """The regression: a single uncommitted batch with mixed novel/duplicate inserts.

    Pre-fix, a `session.rollback()` inside the failing insert would wipe the
    earlier successful inserts from the same uncommitted transaction. The
    SAVEPOINT scope is what makes this work.
    """
    src = Source(kind="reddit", name="r/a", config_json={})
    session.add(src)
    session.flush()

    # Insert 3 novel signals + 1 duplicate of the second, all within ONE
    # uncommitted transaction (no commits between).
    results: list[bool] = [
        try_insert(session, _make_signal(src.id, "h-A", "ext-A")),  # novel
        try_insert(session, _make_signal(src.id, "h-B", "ext-B")),  # novel
        try_insert(session, _make_signal(src.id, "h-B", "ext-Bdup")),  # collision
        try_insert(session, _make_signal(src.id, "h-C", "ext-C")),  # novel
    ]
    session.commit()

    # The False return tells the caller "this one was a dup; don't count it".
    assert results == [True, True, False, True]

    # Critical: all THREE novel rows survive in a fresh session.
    # Without the SAVEPOINT fix, the collision on row 3 would roll back the
    # whole transaction and `session.commit()` would persist zero rows.
    factory = session.bind  # reuse the engine via the session's bind
    from sqlalchemy.orm import Session as _S

    with _S(factory) as fresh:
        rows = fresh.execute(select(RawSignal).order_by(RawSignal.id)).scalars().all()
        hashes = [r.content_hash for r in rows]
        assert hashes == ["h-A", "h-B", "h-C"], (
            f"fresh-session must see all 3 novel rows; got {hashes}. "
            "If this is empty, the SAVEPOINT regression is back."
        )


def test_many_intra_batch_collisions_preserve_unique_rows(session: Session) -> None:
    """Heavier version: 10 inserts where every other one is a duplicate."""
    src = Source(kind="reddit", name="r/a", config_json={})
    session.add(src)
    session.flush()

    # Pairs (novel, dup-of-prev) × 5
    successes = 0
    for i in range(5):
        if try_insert(session, _make_signal(src.id, f"h-{i}", f"ext-{i}")):
            successes += 1
        # Duplicate of the one we just inserted
        try_insert(session, _make_signal(src.id, f"h-{i}", f"ext-{i}-dup"))
    session.commit()

    assert successes == 5

    factory = session.bind
    from sqlalchemy.orm import Session as _S

    with _S(factory) as fresh:
        n = fresh.execute(select(RawSignal)).scalars().all()
        assert len(n) == 5
