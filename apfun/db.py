"""Database engine, session factory, and SQLite pragma setup.

Pragmas are applied via a SQLAlchemy `connect` event listener so they take effect
on every new connection — the pool may open new ones at any time and most SQLite
pragmas are per-connection (journal_mode is sticky on the file, but applying it
again is harmless).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from apfun.config import settings


def _ensure_sqlite_dir(url: str) -> None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return
    raw = url[len(prefix) :]
    if not raw or raw == ":memory:":
        return
    Path(raw).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.db_url)
engine: Engine = create_engine(settings.db_url, future=True)


def apply_sqlite_pragmas(connection: sqlite3.Connection) -> None:
    """Apply WAL + safety pragmas to a fresh SQLite connection."""
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def _on_connect(dbapi_connection: object, _connection_record: object) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        apply_sqlite_pragmas(dbapi_connection)


event.listen(engine, "connect", _on_connect)


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yield a sync Session, close on exit."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
