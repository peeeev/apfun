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
