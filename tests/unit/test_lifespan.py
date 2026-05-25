"""App lifespan: scheduler pause re-apply + startup resilience (follow-up to 014-fix-2).

The lifespan re-applies a persisted scheduler pause on startup, but must NOT
crash if `runtime_state` isn't there yet (code deployed before the migration ran
under `--reload`). The scheduler is the conftest stub (autouse `_stub_scheduler`).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from apfun.scheduler.pause_state import set_scheduler_paused


def test_lifespan_survives_runtime_state_read_error(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing runtime_state read at startup (missing table during the
    code-before-migration window) degrades to 'not paused' — it does NOT take
    down app startup. Tuition: 014-fix-2 took /inbox + /ops down until migrate."""
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("apfun.db.SessionLocal", factory)

    def boom(_session: object) -> bool:
        raise Exception("no such table: runtime_state")  # noqa: TRY002

    monkeypatch.setattr("apfun.scheduler.pause_state.is_scheduler_paused", boom)

    from apfun.main import app

    # Entering the context runs the lifespan startup — must not raise.
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        # Degraded to not-paused: pause() was never called.
        assert app.state.scheduler.pause_calls == 0


def test_lifespan_reapplies_pause_when_flag_set(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the persisted flag is set, startup re-applies scheduler.pause()."""
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("apfun.db.SessionLocal", factory)
    with factory() as s:
        set_scheduler_paused(s, True)

    from apfun.main import app

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert app.state.scheduler.pause_calls == 1


def test_lifespan_starts_unpaused_when_flag_absent(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No flag → no pause re-apply (the normal case)."""
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("apfun.db.SessionLocal", factory)

    from apfun.main import app

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert app.state.scheduler.pause_calls == 0
