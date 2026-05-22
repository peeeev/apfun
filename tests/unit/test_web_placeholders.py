"""Tests for nav-link placeholder routes (chore PR per feedback 019 Q5).

Three routes that previously 404'd render a friendly "Coming in task NNN"
page using the standard chrome.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def client(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("apfun.db.SessionLocal", factory)
    monkeypatch.setattr("apfun.web.routes.inbox.SessionLocal", factory)
    from apfun.main import app

    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize(
    ("path", "heading", "task_id"),
    [
        ("/opportunities", "Opportunities", "020"),
        ("/sources", "Sources", "021"),
        ("/projects", "Projects", "021"),
    ],
)
def test_placeholder_renders_with_chrome(
    client: TestClient, path: str, heading: str, task_id: str
) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert heading in r.text
    assert f"Coming in task {task_id}." in r.text
    # Base chrome present (nav + stylesheet).
    assert "/static/app.css" in r.text
    for href in ("/inbox", "/opportunities", "/sources", "/projects"):
        assert f'href="{href}"' in r.text
