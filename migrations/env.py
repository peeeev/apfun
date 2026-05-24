"""Alembic env: wires our sync engine and metadata into Alembic's migration context."""

from __future__ import annotations

import sqlite3
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, event

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apfun.config import settings  # noqa: E402
from apfun.models import Base  # noqa: E402  (import registers every model)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Single source of truth: settings.db_url overrides whatever's in alembic.ini.
config.set_main_option("sqlalchemy.url", settings.db_url)

target_metadata = Base.metadata


def _migration_engine():
    """Dedicated engine for migrations, with `foreign_keys` OFF.

    SQLite `render_as_batch` migrations recreate a table to alter it (CREATE
    new → copy rows → DROP old → rename). With foreign keys enforced — which
    the *app* engine (`apfun.db.engine`) sets via its connect listener — the
    implicit DELETE-before-DROP fires every `ON DELETE CASCADE` pointing at the
    table and silently destroys child rows. So migrations get their own engine
    with FK enforcement OFF; the app keeps FK ON.

    The pragma is set at *connect* time (autocommit, before any transaction),
    which is the only point SQLite honors `PRAGMA foreign_keys`. Doing it via
    `connection.execute` after connect instead would either be a no-op (inside
    Alembic's transaction) or break Alembic's commit handling.

    Tuition: migration 7f3a9c2e1d04 cascade-wiped `candidate_signals` +
    `approvals` on the live DB, 2026-05-24, before this guard existed.
    """
    eng = create_engine(settings.db_url, future=True)

    @event.listens_for(eng, "connect")
    def _migration_pragmas(dbapi_connection: object, _record: object) -> None:
        if isinstance(dbapi_connection, sqlite3.Connection):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=OFF")
            finally:
                cursor.close()

    return eng


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    migration_engine = _migration_engine()
    try:
        with migration_engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        migration_engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
