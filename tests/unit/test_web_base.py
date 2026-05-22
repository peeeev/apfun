"""Tests for the admin UI base (task 013): static mount, /healthz, /, /inbox shell.

Uses FastAPI TestClient against a per-test app instance configured to use the
shared SQLite test engine. The `engine`/`session` conftest fixtures back the
test DB; we override `apfun.db.SessionLocal` so route handlers see the same DB.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def client(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A TestClient where every route's session points at the test engine.

    Two routes use `SessionLocal`: the inbox endpoints (via `_session` dep)
    and any future caller that imports from `apfun.db`. The monkeypatch hits
    both at the symbol level so dependency-overriding through FastAPI's DI
    isn't required. The `_stub_scheduler` autouse fixture (conftest) takes
    care of the lifespan-startup scheduler stub.
    """
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("apfun.db.SessionLocal", factory)
    monkeypatch.setattr("apfun.web.routes.inbox.SessionLocal", factory)

    from apfun.main import app

    with TestClient(app) as c:
        yield c


def test_healthz_returns_ok(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "scheduler": {"running": True}}


def test_static_app_css_served(client: TestClient) -> None:
    r = client.get("/static/app.css")
    assert r.status_code == 200
    assert "candidate-card" in r.text  # one of the inbox-specific utility classes
    assert "text/css" in r.headers.get("content-type", "")


def test_root_renders_base_layout(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    # Base chrome present
    assert "<title>apfun</title>" in r.text
    assert "/static/app.css" in r.text
    # Nav links
    for href in ("/inbox", "/opportunities", "/sources", "/projects"):
        assert f'href="{href}"' in r.text
    # HTMX script pinned + SRI
    assert "htmx.org@2.0.4" in r.text
    assert "integrity=" in r.text


def test_inbox_renders_with_no_candidates(client: TestClient) -> None:
    r = client.get("/inbox")
    assert r.status_code == 200
    assert "Inbox" in r.text
    assert "No pending candidates" in r.text
