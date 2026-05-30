"""DataForSEO HTTP client (task 015 / orchestrator request 033).

Foundation for Stages 2-5 — no consumers yet. Two endpoint families:

- **SERP** (`serp/google/organic/...`) — Stage 3 (016) competitive scrape.
- **Google Ads Keyword Data** (`keywords_data/google_ads/search_volume/...`) —
  Stage 4 (017) saturation scoring (search volume, CPC, competition).

The module centralizes four concerns so Stage 3/4 don't have to repeat them:

1. **HTTP Basic Auth** via the dedicated API password (NOT the account login —
   #1 setup failure per DataForSEO's integration guides; loud-failure with a
   pointing error at first call).
2. **Monthly budget guard** ($25/mo default). Pre-call check raises
   `DataForSEOBudgetExceededError`; operator must explicitly raise the cap and
   restart to resume. Soft cap — see "Budget cap timing edge case" in 033 §Q6.
3. **Rate-limit awareness.** Reactive (read `X-RateLimit-Remaining` and slow
   down when low) + special-cased for Google Ads Live's 12 req/min cap.
4. **Per-call audit** to `dataforseo_usage` (endpoint, cost, latency, status,
   task_id). Same table powers the /ops budget surface and the budget guard's
   month-to-date sum.

Sandbox-first build pattern — settings default to `sandbox.dataforseo.com`;
operator flips to production after a green smoke test (see runbook 005).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Literal, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apfun.config import Settings
from apfun.config import settings as _global_settings
from apfun.db import SessionLocal
from apfun.models import DataForSEOUsage

logger = logging.getLogger(__name__)

# ─────────────────────────── pricing constants ────────────────────────

# SERP API per-query pricing (USD), depth=10 base. Above depth 10, cost doubles
# per extra 100 results — see `_estimate_serp_cost`.
# verified 2026-05-30 https://dataforseo.com/apis/serp-api/pricing
_SERP_COST_PER_QUERY_USD: dict[str, float] = {
    "standard": 0.0006,
    "priority": 0.0012,
    "live": 0.002,
}

# Google Ads Keyword Data: one task carries up to 1000 keywords, cost is per
# task (NOT per keyword). At apfun's scale (~50 candidates/week × ~5 keywords)
# this is the dominant Stage-4 line item.
# verified 2026-05-30 https://dataforseo.com/pricing/keywords-data/google-ads
_KEYWORDS_GOOGLE_ADS_COST_PER_TASK_USD: float = 0.075

# heuristic 2026-05-30 — DataForSEO's documented per-account limit is 2000
# req/min general, but a queue-side limit of ~100 remaining is when their docs
# suggest backing off. Reactive (sleep before the next call), not predictive.
_RATE_REMAINING_THROTTLE: int = 100

# verified 2026-05-30 https://dataforseo.com/help-center/dataforseo-api-limits
# Google Ads Live endpoints are capped at 12 req/min per account regardless of
# the general 2000/min limit — separate tracker per family.
_GOOGLE_ADS_PER_MIN_CAP: int = 12

# heuristic 2026-05-30 — when X-RateLimit-Remaining is below the threshold,
# sleep this long before the next call. Crude but safe; revisit if observed
# burstiness ever wedges a run.
_RATE_LIMIT_SLEEP_S: float = 2.0

# DataForSEO Standard Queue: documented average latency ~5 min. Poll cadence
# starts here and backs off; total poll timeout bounds a wedged task.
# heuristic 2026-05-30 — 5/10/20s with a 10-min ceiling.
_TASK_POLL_INITIAL_S: float = 5.0
_TASK_POLL_BACKOFF_FACTOR: float = 1.5
_TASK_POLL_MAX_INTERVAL_S: float = 30.0
_TASK_POLL_TIMEOUT_S: float = 600.0

# DataForSEO HTTP timeout for individual requests. Standard Queue's task_post
# returns immediately; task_get within a few seconds. Live mode can take ~10s
# for SERP advanced + busy hours. 120s is comfortable headroom.
# heuristic 2026-05-30 — matches the LLM client's `_JUDGE_TIMEOUT_S` choice.
_HTTP_TIMEOUT_S: float = 120.0

# DataForSEO status codes used in this module (status_code lives in the
# response body, distinct from the HTTP status). 20000 = OK, 20100 = task
# created (Standard Queue), 40201 = account suspended/unusual activity, 40202 =
# rate-limit exceeded.
# verified 2026-05-30 https://docs.dataforseo.com/v3/appendix/errors
_STATUS_OK: int = 20000
_STATUS_TASK_CREATED: int = 20100
_STATUS_ACCOUNT_SUSPENDED: int = 40201
_STATUS_RATE_LIMITED: int = 40202

# Default: US English. Stage 3/4 callers override per-candidate as needed.
_DEFAULT_LOCATION_CODE: int = 2840  # United States
_DEFAULT_LANGUAGE_CODE: str = "en"


# ─────────────────────────────── errors ───────────────────────────────


class DataForSEOError(RuntimeError):
    """Base class for everything this client raises."""


class DataForSEOBudgetExceededError(DataForSEOError):
    """Raised pre-call when the monthly budget would be crossed."""


class DataForSEOAccountSuspendedError(DataForSEOError):
    """Status 40201 — DataForSEO paused the account. Don't retry; contact support."""


class DataForSEOAPIError(DataForSEOError):
    """Any other API-side error (non-20000 status_code in the response body)."""


class DataForSEOTaskTimeoutError(DataForSEOError):
    """A Standard Queue task did not reach status 20000 within the poll budget."""


# ───────────────────────── response schemas ──────────────────────────


class OrganicItem(BaseModel):
    """One organic result row in a SERP response (subset of fields apfun uses)."""

    model_config = ConfigDict(extra="ignore")
    type: str = "organic"
    rank_absolute: int | None = None
    rank_group: int | None = None
    url: str | None = None
    title: str | None = None
    description: str | None = None
    domain: str | None = None


class SerpResult(BaseModel):
    """Parsed `serp/google/organic/...` result for one keyword."""

    model_config = ConfigDict(extra="ignore")
    keyword: str
    items_count: int | None = None
    items: list[OrganicItem] = Field(default_factory=lambda: list[OrganicItem]())


class KeywordVolumeItem(BaseModel):
    """One keyword's Google Ads Keyword Planner metrics.

    `competition` is the qualitative bucket (LOW/MEDIUM/HIGH) and
    `competition_index` is the 0-100 numeric. `low_top_of_page_bid` and
    `high_top_of_page_bid` are the historical paid-search bid range in USD.
    """

    model_config = ConfigDict(extra="ignore")
    keyword: str
    search_volume: int | None = None
    competition: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    competition_index: int | None = None
    cpc: float | None = None
    low_top_of_page_bid: float | None = None
    high_top_of_page_bid: float | None = None


class KeywordVolumeResult(BaseModel):
    """Parsed `keywords_data/google_ads/search_volume/live` result for N keywords."""

    model_config = ConfigDict(extra="ignore")
    items: list[KeywordVolumeItem] = Field(default_factory=lambda: list[KeywordVolumeItem]())


# ──────────────────────────── cost math ──────────────────────────────


def _estimate_serp_cost(mode: str, depth: int = 10) -> float:
    """Estimate SERP cost for the budget pre-check. Doubles per extra 100 results
    above depth 10 (DataForSEO's documented depth multiplier)."""
    base = _SERP_COST_PER_QUERY_USD[mode]
    extra_hundreds = max(0, (depth - 10 + 99) // 100)
    return base * (2**extra_hundreds)


def _estimate_keywords_google_ads_cost(num_keywords: int) -> float:
    """Per-task flat rate — capped at 1000 keywords/task by DataForSEO."""
    if num_keywords < 1:
        raise ValueError("at least one keyword required")
    if num_keywords > 1000:
        raise ValueError(
            f"Google Ads keyword endpoint accepts max 1000 keywords/task; got {num_keywords}"
        )
    return _KEYWORDS_GOOGLE_ADS_COST_PER_TASK_USD


# ────────────────────────────── client ────────────────────────────────


class DataForSEOClient:
    """Synchronous DataForSEO HTTP client. One per process is enough.

    Default settings + SessionLocal are picked up from the global config; the
    `client` and `_session_factory` test seams let tests inject an
    `httpx.MockTransport` and an in-memory DB without monkeypatching.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        _session_factory: Callable[[], Session] | None = None,
    ) -> None:
        s = settings or _global_settings
        if not s.dataforseo_login or not s.dataforseo_password:
            raise DataForSEOError(
                "APFUN_DATAFORSEO_LOGIN and APFUN_DATAFORSEO_PASSWORD are required. "
                "The password is your DEDICATED API password from "
                "https://app.dataforseo.com/api-access — NOT your account login "
                "password. Using the wrong password is the #1 setup failure for "
                "this API. See docs/operator/SETUP.md → DataForSEO."
            )
        self._settings = s
        self._client = client or httpx.Client(
            base_url=s.dataforseo_base_url,
            auth=(s.dataforseo_login, s.dataforseo_password),
            timeout=httpx.Timeout(_HTTP_TIMEOUT_S),
        )
        self._session_factory: Callable[[], Session] = _session_factory or SessionLocal
        # Rate-limit awareness state. Optimistic init; updated from response headers.
        self._rate_remaining: int = 2000
        # 12-req/min sliding window for Google Ads Live (per-account cap).
        self._google_ads_call_times: deque[float] = deque(maxlen=_GOOGLE_ADS_PER_MIN_CAP)

    # ── public methods ─────────────────────────────────────────────

    def serp_google_organic(
        self,
        keyword: str,
        *,
        location_code: int = _DEFAULT_LOCATION_CODE,
        language_code: str = _DEFAULT_LANGUAGE_CODE,
        queue_mode: Literal["standard", "priority", "live"] | None = None,
        depth: int = 10,
    ) -> SerpResult:
        """Run a Google organic-SERP query. Returns the parsed result.

        Standard / Priority modes use the async task_post → task_get flow;
        Live mode is single-request. Cost goes to `dataforseo_usage` with
        family="serp" before any return.
        """
        mode = queue_mode or self._settings.dataforseo_serp_queue_mode
        estimated = _estimate_serp_cost(mode, depth)
        self._check_budget(estimated)
        self._maybe_throttle_rate()

        payload = [
            {
                "keyword": keyword,
                "location_code": location_code,
                "language_code": language_code,
                "depth": depth,
            }
        ]

        if mode == "live":
            endpoint = "serp/google/organic/live/advanced"
            task = self._call(
                endpoint, payload, family="serp", queue_mode=mode, estimated=estimated
            )
        else:
            # Standard or Priority: task_post then poll task_get. Priority is
            # set via the `priority` field on the payload (1=normal, 2=priority).
            if mode == "priority":
                payload[0]["priority"] = 2
            task_id = self._task_post(
                "serp/google/organic/task_post",
                payload,
                family="serp",
                queue_mode=mode,
                estimated=estimated,
            )
            task = self._task_get(
                f"serp/google/organic/task_get/advanced/{task_id}",
                task_id,
                family="serp",
                queue_mode=mode,
            )

        results = cast("list[dict[str, Any]]", task.get("result") or [{}])
        return SerpResult.model_validate(results[0])

    def keywords_google_ads_search_volume(
        self,
        keywords: list[str],
        *,
        location_code: int = _DEFAULT_LOCATION_CODE,
        language_code: str = _DEFAULT_LANGUAGE_CODE,
    ) -> KeywordVolumeResult:
        """Run a Google Ads Keyword Data search-volume query. Live mode only —
        Standard Queue would add 5-min latency to a per-task-priced endpoint
        for no per-call savings."""
        estimated = _estimate_keywords_google_ads_cost(len(keywords))
        self._check_budget(estimated)
        # Google Ads Live has its own 12/min cap on top of the general one.
        self._maybe_throttle_google_ads()
        self._maybe_throttle_rate()

        payload = [
            {
                "keywords": keywords,
                "location_code": location_code,
                "language_code": language_code,
            }
        ]
        task = self._call(
            "keywords_data/google_ads/search_volume/live",
            payload,
            family="keywords_google_ads",
            queue_mode=None,
            estimated=estimated,
        )
        items: list[dict[str, Any]] = task.get("result") or []
        return KeywordVolumeResult.model_validate({"items": items})

    # ── private: HTTP + accounting ─────────────────────────────────

    def _call(
        self,
        endpoint: str,
        payload: list[dict[str, Any]],
        *,
        family: str,
        queue_mode: str | None,
        estimated: float,
    ) -> dict[str, Any]:
        """One-shot POST. Returns the body's `tasks[0]` dict, records usage."""
        return self._post_and_record(
            endpoint, payload, family=family, queue_mode=queue_mode, estimated=estimated
        )

    def _task_post(
        self,
        endpoint: str,
        payload: list[dict[str, Any]],
        *,
        family: str,
        queue_mode: str,
        estimated: float,
    ) -> str:
        """POST a Standard Queue task. Returns task_id. Records usage row."""
        task = self._post_and_record(
            endpoint,
            payload,
            family=family,
            queue_mode=queue_mode,
            estimated=estimated,
            expected_status=_STATUS_TASK_CREATED,
        )
        task_id = task.get("id")
        if not isinstance(task_id, str):
            raise DataForSEOAPIError(f"task_post: no `id` in response: {task!r}")
        return task_id

    def _task_get(
        self,
        endpoint: str,
        task_id: str,
        *,
        family: str,
        queue_mode: str,
    ) -> dict[str, Any]:
        """Poll a Standard Queue task until status 20000 or timeout. Each poll is
        free (DataForSEO doesn't bill task_get separately when the task is
        already paid for via task_post), so polls record cost=0 audit rows."""
        deadline = time.monotonic() + _TASK_POLL_TIMEOUT_S
        interval = _TASK_POLL_INITIAL_S
        while True:
            self._maybe_throttle_rate()
            response = self._client.get(endpoint)
            self._record_rate_headers(response)
            body = self._parse_body(response)
            task = self._extract_task(body)
            status_code = task.get("status_code", 0)
            if status_code == _STATUS_OK:
                # Done. Record a $0 audit row for the polling pass.
                self._record_usage(
                    family=family,
                    endpoint=endpoint,
                    queue_mode=queue_mode,
                    est_cost_usd=0.0,
                    latency_ms=0,
                    status_code=response.status_code,
                    task_id=task_id,
                    response_size_bytes=len(response.content),
                    ok=True,
                    error=None,
                )
                return task
            if time.monotonic() >= deadline:
                raise DataForSEOTaskTimeoutError(
                    f"task {task_id} did not reach status {_STATUS_OK} within "
                    f"{_TASK_POLL_TIMEOUT_S:.0f}s (last status: {status_code})"
                )
            time.sleep(interval)
            interval = min(interval * _TASK_POLL_BACKOFF_FACTOR, _TASK_POLL_MAX_INTERVAL_S)

    def _post_and_record(
        self,
        endpoint: str,
        payload: list[dict[str, Any]],
        *,
        family: str,
        queue_mode: str | None,
        estimated: float,
        expected_status: int = _STATUS_OK,
    ) -> dict[str, Any]:
        """The common path: POST, parse body, record usage, raise on API error."""
        started = time.monotonic()
        if family == "keywords_google_ads":
            # Stamp the throttle window NOW so the next call sees the timestamp
            # even if this one raises mid-flight.
            self._google_ads_call_times.append(time.monotonic())
        response = self._client.post(endpoint, json=payload)
        latency_ms = int((time.monotonic() - started) * 1000)
        self._record_rate_headers(response)

        # Parse body even on non-2xx — DataForSEO returns 200 + body status_code
        # for most errors, but a 401/403 wouldn't have JSON. Be defensive.
        try:
            body = self._parse_body(response)
            task = self._extract_task(body)
        except DataForSEOAPIError as exc:
            self._record_usage(
                family=family,
                endpoint=endpoint,
                queue_mode=queue_mode,
                est_cost_usd=0.0,
                latency_ms=latency_ms,
                status_code=response.status_code,
                task_id=None,
                response_size_bytes=len(response.content),
                ok=False,
                error=str(exc),
            )
            raise

        task_status = task.get("status_code", 0)
        task_id = task.get("id") if isinstance(task.get("id"), str) else None
        # Actual cost lives on the task: prefer it over our estimate so the row
        # reflects what DataForSEO billed (depth multipliers, edge cases, etc.).
        actual_cost = float(task.get("cost") or 0.0)
        if actual_cost <= 0:
            actual_cost = estimated

        if task_status == _STATUS_ACCOUNT_SUSPENDED:
            self._record_usage(
                family=family,
                endpoint=endpoint,
                queue_mode=queue_mode,
                est_cost_usd=0.0,
                latency_ms=latency_ms,
                status_code=response.status_code,
                task_id=task_id,
                response_size_bytes=len(response.content),
                ok=False,
                error=f"40201 account suspended: {task.get('status_message')}",
            )
            raise DataForSEOAccountSuspendedError(
                f"DataForSEO suspended this account (status 40201): "
                f"{task.get('status_message')}. Contact support@dataforseo.com; "
                f"do not retry into a suspended account."
            )
        if task_status != expected_status:
            err = f"status {task_status}: {task.get('status_message')!r}"
            self._record_usage(
                family=family,
                endpoint=endpoint,
                queue_mode=queue_mode,
                est_cost_usd=0.0,
                latency_ms=latency_ms,
                status_code=response.status_code,
                task_id=task_id,
                response_size_bytes=len(response.content),
                ok=False,
                error=err,
            )
            raise DataForSEOAPIError(err)

        self._record_usage(
            family=family,
            endpoint=endpoint,
            queue_mode=queue_mode,
            est_cost_usd=actual_cost,
            latency_ms=latency_ms,
            status_code=response.status_code,
            task_id=task_id,
            response_size_bytes=len(response.content),
            ok=True,
            error=None,
        )
        return task

    @staticmethod
    def _parse_body(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise DataForSEOAPIError(
                f"non-JSON response from DataForSEO (HTTP {response.status_code}): "
                f"{response.text[:200]!r}"
            ) from exc
        if not isinstance(data, dict):
            raise DataForSEOAPIError(
                f"expected JSON object at top level, got {type(data).__name__}"
            )
        return cast("dict[str, Any]", data)

    @staticmethod
    def _extract_task(body: dict[str, Any]) -> dict[str, Any]:
        """DataForSEO wraps every result in `tasks: [{...}]` (always length 1 for
        our single-payload calls). Pull the first task out."""
        tasks_raw = body.get("tasks")
        if not isinstance(tasks_raw, list) or not tasks_raw:
            raise DataForSEOAPIError(f"no tasks in response body (top-level keys: {list(body)})")
        tasks = cast("list[Any]", tasks_raw)
        first = tasks[0]
        if not isinstance(first, dict):
            raise DataForSEOAPIError(f"tasks[0] is not a dict (got {type(first).__name__})")
        return cast("dict[str, Any]", first)

    # ── private: budget + rate limiting ────────────────────────────

    def _check_budget(self, estimated_cost_usd: float) -> None:
        """Sum dataforseo_usage.est_cost_usd for the current calendar month
        (UTC); raise if `spent + estimated` would exceed the cap. Loud failure
        on purpose — operator increases the cap explicitly to resume."""
        start_of_month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with self._session_factory() as session:
            spent = float(
                session.execute(
                    select(func.coalesce(func.sum(DataForSEOUsage.est_cost_usd), 0.0)).where(
                        DataForSEOUsage.created_at >= start_of_month
                    )
                ).scalar_one()
            )
        cap = self._settings.dataforseo_budget_usd_per_month
        if spent + estimated_cost_usd > cap:
            raise DataForSEOBudgetExceededError(
                f"DataForSEO monthly cap ${cap:.2f} would be exceeded: "
                f"${spent:.4f} spent this month + ${estimated_cost_usd:.4f} requested. "
                f"Raise APFUN_DATAFORSEO_BUDGET_USD_PER_MONTH and restart to resume."
            )

    def _maybe_throttle_rate(self) -> None:
        """If the last response said remaining was low, sleep briefly before the
        next call. Crude reactive throttling — fine at our scale."""
        if self._rate_remaining < _RATE_REMAINING_THROTTLE:
            logger.info(
                "dataforseo throttle (rate_remaining=%d < %d), sleeping %.1fs",
                self._rate_remaining,
                _RATE_REMAINING_THROTTLE,
                _RATE_LIMIT_SLEEP_S,
            )
            time.sleep(_RATE_LIMIT_SLEEP_S)

    def _maybe_throttle_google_ads(self) -> None:
        """Stay under the 12-req/min per-account Google Ads Live cap.

        Keep a sliding window of recent call timestamps. When the window already
        holds the cap and the oldest entry is still within the last 60s, sleep
        until that entry would fall out of the window.
        """
        if len(self._google_ads_call_times) < _GOOGLE_ADS_PER_MIN_CAP:
            return
        now = time.monotonic()
        oldest = self._google_ads_call_times[0]
        # We pruned-on-insert via maxlen, but the deque still holds the last N
        # regardless of age — check explicitly.
        sleep_until = oldest + 60.0
        wait = sleep_until - now
        if wait > 0:
            logger.info("dataforseo google-ads throttle (12/min cap), sleeping %.2fs", wait)
            time.sleep(wait)

    def _record_rate_headers(self, response: httpx.Response) -> None:
        """Update the in-process rate counter from headers."""
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            with suppress(ValueError):
                self._rate_remaining = int(remaining)

    def _record_usage(
        self,
        *,
        family: str,
        endpoint: str,
        queue_mode: str | None,
        est_cost_usd: float,
        latency_ms: int,
        status_code: int | None,
        task_id: str | None,
        response_size_bytes: int | None,
        ok: bool,
        error: str | None,
    ) -> None:
        """Write one row to dataforseo_usage. Independent session so the audit
        survives a caller-side rollback (mirrors the LLM client's pattern).
        Truncates `error` so a giant API blob doesn't bloat the table."""
        with self._session_factory() as session:
            session.add(
                DataForSEOUsage(
                    family=family,
                    endpoint=endpoint,
                    queue_mode=queue_mode,
                    est_cost_usd=est_cost_usd,
                    latency_ms=latency_ms,
                    status_code=status_code,
                    task_id=task_id,
                    response_size_bytes=response_size_bytes,
                    ok=ok,
                    error=(error or "")[:1000] or None,
                )
            )
            session.commit()


__all__ = [
    "DataForSEOAPIError",
    "DataForSEOAccountSuspendedError",
    "DataForSEOBudgetExceededError",
    "DataForSEOClient",
    "DataForSEOError",
    "DataForSEOTaskTimeoutError",
    "KeywordVolumeItem",
    "KeywordVolumeResult",
    "OrganicItem",
    "SerpResult",
]
