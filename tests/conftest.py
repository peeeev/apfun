"""Shared pytest fixtures for apfun tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from apfun.db import apply_sqlite_pragmas

# Importing this package registers every model on Base.metadata.
from apfun.models import Base

# Reddit ingestion (task 005c) routes through a residential proxy
# (`APFUN_REDDIT_HTTP_PROXY`), loud-failure at the `_build_client()` call site
# rather than at `Settings()` construction — so no env default is needed here.
# Tests that exercise the fetch path either pass a mock client (bypassing the
# proxy requirement) or monkeypatch `settings.reddit_http_proxy`. ProductHunt
# token is the same loud-failure shape — monkeypatched in the happy-path tests,
# left empty for the missing-token no-op test (see `tests/unit/test_producthunt_*`).


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


@pytest.fixture(autouse=True)
def _stub_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace `apfun.main.start_scheduler` with a no-op stub.

    Any test that constructs a `TestClient(apfun.main.app)` enters the
    FastAPI lifespan handler, which calls `start_scheduler()`. The real
    scheduler would touch the prod DB jobstore and spin a worker thread —
    neither belongs in unit tests. Tests of the scheduler itself build
    their own `BackgroundScheduler` explicitly and don't go through `app`.

    autouse: applies to every test even when no FastAPI client is built.
    The monkeypatch is cheap; tests that don't import `apfun.main` are
    unaffected.
    """

    class _StubScheduler:
        running = True
        shutdown_calls = 0
        start_calls = 0
        # Tests can set these to raise on the corresponding method call.
        shutdown_raises: BaseException | None = None
        start_raises: BaseException | None = None

        def shutdown(self, *, wait: bool = True) -> None:
            self.shutdown_calls += 1
            if self.shutdown_raises is not None:
                raise self.shutdown_raises
            self.running = False

        def start(self) -> None:
            self.start_calls += 1
            if self.start_raises is not None:
                raise self.start_raises
            self.running = True

    monkeypatch.setattr("apfun.main.start_scheduler", lambda: _StubScheduler())
