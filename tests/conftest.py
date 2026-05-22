"""Shared pytest fixtures for apfun tests."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

# Must run BEFORE any apfun import — `apfun.config.Settings()` fails-loud at
# construction without APFUN_REDDIT_USERNAME (per docs/tasks/005-reddit-
# ingester.md → Config). The test default is a sentinel handle that mirrors
# the production UA format without claiming a real Reddit account.
os.environ.setdefault("APFUN_REDDIT_USERNAME", "apfun_test_runner")
# Reddit OAuth client credentials default to sentinels so module-level
# `_get_auth()` paths (when reached) construct without raising the loud-
# failure. Tests that exercise OAuth fetch paths monkeypatch `_get_auth` to
# return a stub so the real token endpoint is never hit. See
# `tests/unit/test_reddit_ingester.py::stub_reddit_auth`.
os.environ.setdefault("APFUN_REDDIT_CLIENT_ID", "test_client_id")
os.environ.setdefault("APFUN_REDDIT_CLIENT_SECRET", "test_client_secret")
# ProductHunt token is loud-failure (per CLAUDE.md → Auth secret discipline) —
# defaults to empty, used at the call site. Tests that exercise the happy path
# monkeypatch `settings.producthunt_token`; the missing-token test leaves the
# default empty and asserts the no-op path. See `tests/unit/test_producthunt_*`.

import pytest  # noqa: E402
from sqlalchemy import Engine, create_engine, event  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from apfun.db import apply_sqlite_pragmas  # noqa: E402

# Importing this package registers every model on Base.metadata.
from apfun.models import Base  # noqa: E402


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """A file-backed SQLite engine in a tmp dir with our pragma listener attached.

    File-backed (not :memory:) because `PRAGMA journal_mode=WAL` is a no-op on
    in-memory databases and we want to verify the listener actually runs.
    """
    db_path = tmp_path / "test.db"
    eng = create_engine(f"sqlite:///{db_path}", future=True)

    @event.listens_for(eng, "connect")
    def _on_connect(dbapi_connection: object, _record: object) -> None:
        if isinstance(dbapi_connection, sqlite3.Connection):
            apply_sqlite_pragmas(dbapi_connection)

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as s:
        yield s
