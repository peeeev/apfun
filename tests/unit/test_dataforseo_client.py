"""Unit tests for `apfun.clients.dataforseo.DataForSEOClient` (task 015).

httpx is wired via `MockTransport` so tests exercise the real client without a
network. DB writes go to the conftest test engine via an injected
`_session_factory`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import sessionmaker

from apfun.clients.dataforseo import (
    _GOOGLE_ADS_PER_MIN_CAP,
    _RATE_REMAINING_THROTTLE,
    DataForSEOAccountSuspendedError,
    DataForSEOAPIError,
    DataForSEOBudgetExceededError,
    DataForSEOClient,
    DataForSEOError,
    KeywordVolumeResult,
    SerpResult,
    _estimate_keywords_google_ads_cost,
    _estimate_serp_cost,
)
from apfun.config import Settings
from apfun.models import DataForSEOUsage

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "dataforseo"


def _load(name: str) -> dict[str, Any]:
    """Load a fixture, stripping the _fixture_meta header used to mark synthetic ones."""
    data = json.loads((_FIXTURES / name).read_text())
    data.pop("_fixture_meta", None)
    return data


def _settings_with_creds(**overrides: Any) -> Settings:
    """Settings with non-empty creds so DataForSEOClient construction doesn't raise."""
    defaults: dict[str, Any] = {
        "dataforseo_login": "test@example.com",
        "dataforseo_password": "test-dedicated-password",
        "dataforseo_budget_usd_per_month": 25.0,
        "dataforseo_base_url": "https://sandbox.dataforseo.com/v3/",
        "dataforseo_serp_queue_mode": "live",  # avoid the polling path in most tests
    }
    return Settings(**(defaults | overrides))


@pytest.fixture
def factory(engine: Engine) -> sessionmaker:
    """Sessionmaker bound to the conftest test engine."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def make_client(factory: sessionmaker) -> Iterator[Any]:
    """Build a DataForSEOClient backed by a programmable MockTransport.

    Returns a factory the test calls with `make_client(handler, **settings_overrides)`
    where `handler(request: httpx.Request) -> httpx.Response`.
    """

    def _make(
        handler: Any,
        **settings_overrides: Any,
    ) -> DataForSEOClient:
        transport = httpx.MockTransport(handler)
        s = _settings_with_creds(**settings_overrides)
        http = httpx.Client(
            base_url=s.dataforseo_base_url,
            auth=(s.dataforseo_login, s.dataforseo_password),
            transport=transport,
        )
        return DataForSEOClient(s, client=http, _session_factory=factory)

    yield _make


# ─────────────────────────── estimators ───────────────────────────


def test_serp_cost_depth_multiplier() -> None:
    assert _estimate_serp_cost("standard", 10) == 0.0006
    assert _estimate_serp_cost("standard", 100) == 0.0012  # 1 doubling
    assert _estimate_serp_cost("priority", 10) == 0.0012
    assert _estimate_serp_cost("live", 10) == 0.002


def test_keywords_cost_is_per_task_flat() -> None:
    assert _estimate_keywords_google_ads_cost(1) == 0.075
    assert _estimate_keywords_google_ads_cost(500) == 0.075
    assert _estimate_keywords_google_ads_cost(1000) == 0.075


def test_keywords_cost_validates_bounds() -> None:
    with pytest.raises(ValueError, match="at least one"):
        _estimate_keywords_google_ads_cost(0)
    with pytest.raises(ValueError, match="max 1000"):
        _estimate_keywords_google_ads_cost(1001)


# ──────────────────────── auth + construction ────────────────────


def test_construct_without_creds_raises_with_pointing_message() -> None:
    s = Settings(dataforseo_login="", dataforseo_password="")
    with pytest.raises(DataForSEOError, match="DEDICATED API password"):
        DataForSEOClient(s)


def test_construct_with_only_login_still_raises() -> None:
    s = Settings(dataforseo_login="x@y.z", dataforseo_password="")
    with pytest.raises(DataForSEOError):
        DataForSEOClient(s)


# ─────────────────────── happy paths (parsed) ────────────────────


def test_serp_live_parses_fixture(make_client: Any) -> None:
    fixture = _load("serp_google_organic_synthetic.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/serp/google/organic/live/advanced")
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body[0]["keyword"] == "best note-taking app for founders"
        return httpx.Response(200, json=fixture, headers={"X-RateLimit-Remaining": "1999"})

    client = make_client(handler)
    result = client.serp_google_organic("best note-taking app for founders")
    assert isinstance(result, SerpResult)
    assert result.keyword == "best note-taking app for founders"
    assert len(result.items) == 3
    assert result.items[0].domain == "notion.so"
    assert result.items[0].rank_absolute == 1


def test_keywords_live_parses_fixture(make_client: Any) -> None:
    fixture = _load("keywords_google_ads_search_volume_synthetic.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/keywords_data/google_ads/search_volume/live")
        return httpx.Response(200, json=fixture, headers={"X-RateLimit-Remaining": "1999"})

    client = make_client(handler)
    result = client.keywords_google_ads_search_volume(
        ["note taking app", "obsidian alternative", "roam research"]
    )
    assert isinstance(result, KeywordVolumeResult)
    assert len(result.items) == 3
    medium = next(i for i in result.items if i.keyword == "note taking app")
    assert medium.competition == "MEDIUM"
    assert medium.competition_index == 47
    assert medium.search_volume == 12100
    assert medium.cpc == 2.31
    assert medium.low_top_of_page_bid == 0.92


# ─────────────────────── budget guard ────────────────────────────


def _seed_usage(
    factory: sessionmaker, *, est_cost_usd: float, when: datetime, family: str = "serp"
) -> None:
    with factory() as s:
        row = DataForSEOUsage(
            family=family,
            endpoint="serp/google/organic/live/advanced",
            queue_mode="live",
            est_cost_usd=est_cost_usd,
            latency_ms=100,
            status_code=200,
            ok=True,
        )
        s.add(row)
        s.flush()
        # Override the auto-now created_at so seed sits in the desired month.
        s.execute(
            update(DataForSEOUsage)
            .where(DataForSEOUsage.id == row.id)
            .values(created_at=when, updated_at=when)
        )
        s.commit()


def test_budget_guard_allows_small_call_under_cap(make_client: Any, factory: sessionmaker) -> None:
    _seed_usage(factory, est_cost_usd=24.99, when=datetime.now(UTC))
    fixture = _load("serp_google_organic_synthetic.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture, headers={"X-RateLimit-Remaining": "1999"})

    client = make_client(handler)
    # $0.002 (live) + $24.99 = $24.992 < $25 → allowed
    client.serp_google_organic("x")


def test_budget_guard_blocks_when_over_cap(make_client: Any, factory: sessionmaker) -> None:
    _seed_usage(factory, est_cost_usd=24.95, when=datetime.now(UTC))

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("budget guard should have aborted before any HTTP call")

    client = make_client(handler)
    with pytest.raises(DataForSEOBudgetExceededError, match=r"\$25\.00 would be exceeded"):
        # $24.95 + $0.075 = $25.025 > $25 cap
        client.keywords_google_ads_search_volume(["a", "b", "c"])


def test_budget_guard_resets_across_months(make_client: Any, factory: sessionmaker) -> None:
    last_month = datetime.now(UTC).replace(day=1) - timedelta(days=1)
    _seed_usage(factory, est_cost_usd=999.0, when=last_month)  # huge prior month
    fixture = _load("serp_google_organic_synthetic.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture, headers={"X-RateLimit-Remaining": "1999"})

    client = make_client(handler)
    # Prior-month spend doesn't block this month.
    client.serp_google_organic("x")


# ───────────────────────── usage recording ────────────────────────


def test_serp_records_usage_row(make_client: Any, factory: sessionmaker) -> None:
    fixture = _load("serp_google_organic_synthetic.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture, headers={"X-RateLimit-Remaining": "1500"})

    client = make_client(handler)
    client.serp_google_organic("x")

    with factory() as s:
        rows = s.execute(select(DataForSEOUsage)).scalars().all()
    assert len(rows) == 1
    r = rows[0]
    assert r.family == "serp"
    assert r.endpoint == "serp/google/organic/live/advanced"
    assert r.queue_mode == "live"
    assert r.ok is True
    # Records the actual cost from the response body, not the estimate.
    assert r.est_cost_usd == 0.002


def test_keywords_records_usage_row(make_client: Any, factory: sessionmaker) -> None:
    fixture = _load("keywords_google_ads_search_volume_synthetic.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture, headers={"X-RateLimit-Remaining": "1500"})

    client = make_client(handler)
    client.keywords_google_ads_search_volume(["x", "y", "z"])

    with factory() as s:
        r = s.execute(select(DataForSEOUsage)).scalar_one()
    assert r.family == "keywords_google_ads"
    assert r.endpoint == "keywords_data/google_ads/search_volume/live"
    assert r.queue_mode is None
    assert r.est_cost_usd == 0.075


def test_failed_response_records_ok_false(make_client: Any, factory: sessionmaker) -> None:
    """A non-20000 status_code on the task should mark the usage row ok=False
    and surface the message."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status_code": 40400,
                "tasks": [{"status_code": 40400, "status_message": "Not Found.", "cost": 0}],
            },
            headers={"X-RateLimit-Remaining": "1500"},
        )

    client = make_client(handler)
    with pytest.raises(DataForSEOAPIError, match="status 40400"):
        client.serp_google_organic("x")

    with factory() as s:
        r = s.execute(select(DataForSEOUsage)).scalar_one()
    assert r.ok is False
    assert "40400" in (r.error or "")


def test_account_suspended_raises_distinct_error(make_client: Any, factory: sessionmaker) -> None:
    """40201 is a specific operator-facing surface — needs its own exception."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tasks": [
                    {
                        "status_code": 40201,
                        "status_message": "Unusual activity, account paused.",
                    }
                ]
            },
            headers={"X-RateLimit-Remaining": "1999"},
        )

    client = make_client(handler)
    with pytest.raises(DataForSEOAccountSuspendedError, match="suspended"):
        client.serp_google_organic("x")

    with factory() as s:
        r = s.execute(select(DataForSEOUsage)).scalar_one()
    assert r.ok is False
    assert "40201" in (r.error or "")


# ───────────────────────── rate limit awareness ───────────────────


def test_rate_remaining_below_threshold_triggers_sleep(
    make_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the previous response reported low headroom, the NEXT call sleeps
    briefly before going out (reactive throttle)."""
    fixture = _load("serp_google_organic_synthetic.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        # First response reports low remaining; next call should throttle.
        return httpx.Response(
            200,
            json=fixture,
            headers={"X-RateLimit-Remaining": str(_RATE_REMAINING_THROTTLE - 1)},
        )

    sleeps: list[float] = []
    monkeypatch.setattr("apfun.clients.dataforseo.time.sleep", lambda s: sleeps.append(s))

    client = make_client(handler)
    client.serp_google_organic("first")  # response sets _rate_remaining low
    client.serp_google_organic("second")  # second call sees it and throttles
    assert any(s > 0 for s in sleeps), "throttle should have slept at least once"


def test_google_ads_12_per_min_cap_throttles(
    make_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After 12 keyword calls in rapid succession, the 13th sleeps to stay
    under the 12-req/min per-account Google Ads Live cap."""
    fixture = _load("keywords_google_ads_search_volume_synthetic.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture, headers={"X-RateLimit-Remaining": "1999"})

    sleeps: list[float] = []
    monkeypatch.setattr("apfun.clients.dataforseo.time.sleep", lambda s: sleeps.append(s))

    client = make_client(handler)
    for _ in range(_GOOGLE_ADS_PER_MIN_CAP + 1):
        client.keywords_google_ads_search_volume(["x"])
    # At least one sleep > 0 should fire on the 13th call (the deque is full
    # and the oldest entry is still within the 60s window).
    assert any(s > 0 for s in sleeps), (
        "13th rapid call must throttle to respect the 12/min Google Ads Live cap"
    )


# ───────────────────────── schema contract ────────────────────────


def test_serp_fixture_validates_against_schema() -> None:
    """If DataForSEO changes the SERP response shape, this fails first —
    sibling to Reddit/HN contract tests. Synthetic fixture for now; runbook 005
    replaces with a real Sandbox capture."""
    fixture = _load("serp_google_organic_synthetic.json")
    task = fixture["tasks"][0]
    result = task["result"][0]
    SerpResult.model_validate(result)


def test_keywords_fixture_validates_against_schema() -> None:
    fixture = _load("keywords_google_ads_search_volume_synthetic.json")
    items = fixture["tasks"][0]["result"]
    KeywordVolumeResult.model_validate({"items": items})


# ─────────────────── lower-level Standard Queue path ──────────────


def test_serp_standard_queue_polls_until_done(
    make_client: Any, factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standard mode: task_post returns task_id; task_get polls until status 20000."""
    monkeypatch.setattr("apfun.clients.dataforseo.time.sleep", lambda _s: None)

    post_response = {
        "tasks": [
            {
                "id": "task-abc-123",
                "status_code": 20100,
                "status_message": "Task Created.",
                "cost": 0.0006,
            }
        ]
    }
    pending = {"tasks": [{"id": "task-abc-123", "status_code": 40601, "result": None}]}
    done_fixture = _load("serp_google_organic_synthetic.json")
    done = {"tasks": [done_fixture["tasks"][0]]}

    call_count = {"task_get": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/task_post"):
            return httpx.Response(
                200, json=post_response, headers={"X-RateLimit-Remaining": "1999"}
            )
        if "/task_get/" in path:
            call_count["task_get"] += 1
            # First poll returns "in queue"; second returns the result.
            payload = done if call_count["task_get"] >= 2 else pending
            return httpx.Response(200, json=payload, headers={"X-RateLimit-Remaining": "1999"})
        raise AssertionError(f"unexpected path: {path}")

    client = make_client(handler, dataforseo_serp_queue_mode="standard")
    result = client.serp_google_organic("x")
    assert isinstance(result, SerpResult)
    assert call_count["task_get"] >= 2

    with factory() as s:
        rows = s.execute(select(DataForSEOUsage).order_by(DataForSEOUsage.id)).scalars().all()
    # One row for task_post + at least one for the successful task_get poll.
    assert any(r.endpoint.endswith("/task_post") for r in rows)
    assert any("/task_get/" in r.endpoint for r in rows)
    # The task_post row carries the cost; task_get poll rows are $0.
    total_cost = sum(r.est_cost_usd for r in rows)
    assert total_cost == pytest.approx(0.0006)


# ────────────────── budget integration with usage write ───────────


def test_consecutive_calls_accumulate_toward_budget(
    make_client: Any, factory: sessionmaker
) -> None:
    """Each successful call writes a row, and the next call's budget check
    sees the prior spend — proves budget + recording are wired together."""
    fixture = _load("serp_google_organic_synthetic.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture, headers={"X-RateLimit-Remaining": "1999"})

    # Cap tight enough that 3 live SERP calls (3 × $0.002 = $0.006) fit but
    # 4 would not (over a tiny $0.005 cap).
    client = make_client(handler, dataforseo_budget_usd_per_month=0.005)
    client.serp_google_organic("a")  # spend 0.002
    client.serp_google_organic("b")  # spend 0.004
    with pytest.raises(DataForSEOBudgetExceededError):
        client.serp_google_organic("c")  # would push to 0.006 > 0.005 cap

    with factory() as s:
        n = s.execute(select(func.count()).select_from(DataForSEOUsage)).scalar_one()
    assert n == 2, "two successful calls; the aborted third did not write a row"
