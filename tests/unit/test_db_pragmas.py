"""Verify SQLite pragmas are applied to every new connection (CLAUDE.md → DB)."""

from __future__ import annotations

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session


def test_pragmas_via_session(session: Session) -> None:
    """Session-scoped connection has every pragma set by the connect listener."""
    assert session.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    # PRAGMA synchronous returns the integer code; NORMAL is 1.
    assert session.execute(text("PRAGMA synchronous")).scalar() == 1
    assert session.execute(text("PRAGMA busy_timeout")).scalar() == 5000
    assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_pragmas_apply_to_fresh_connections(engine: Engine) -> None:
    """Open several fresh connections; the listener must run on each."""
    for _ in range(3):
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000
