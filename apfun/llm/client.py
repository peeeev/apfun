"""Single Anthropic entrypoint — enforces model policy, retries, logs every call.

See CLAUDE.md → "Model selection policy". Two entrypoints:
- `judge()` → Opus 4.7 with extended thinking enabled. For any judgment task.
- `mechanic()` → Haiku 4.5. For trivial mechanical work (dedup, classification,
  JSON reshape). Refuses to run a task listed in `JUDGMENT_TASKS`.

No method accepts an arbitrary `model` string. If a third model is genuinely
needed, add a new entrypoint here with rationale and update the policy.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from typing import Any, Literal, TypeVar, cast

import anthropic
from anthropic.types import Message
from sqlalchemy.orm import Session

from apfun.config import settings
from apfun.db import SessionLocal
from apfun.models import LLMRun

logger = logging.getLogger(__name__)

T = TypeVar("T")
CacheTTL = Literal["5m", "1h"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


# Model identifiers — both confirmed present on Anthropic's pricing page.
# verified 2026-05-18 https://docs.anthropic.com/en/docs/about-claude/pricing
JUDGE_MODEL: str = "claude-opus-4-7"
MECHANIC_MODEL: str = "claude-haiku-4-5"


# Tasks that REQUIRE judge() — calling mechanic() with one of these is a policy
# violation. Membership is semantically anchored to project-brief.md §3 (Model
# Selection Policy): anything involving niche evaluation, competitor analysis,
# prioritization, or "is this opportunity real" belongs here. Don't let the set
# drift into "things I added LLM calls for so far."
#
# Extend in the same PR that adds the call site — never preemptively. Each
# entry must correspond to an actual judge() callsite somewhere in the repo.
# verified 2026-05-18 project-brief.md §3
JUDGMENT_TASKS: frozenset[str] = frozenset(
    {
        "cluster",  # Stage 1: clustering raw signals into idea cards
        "score",  # Stage 4: complaint clustering for UnmetPain
        "synthesize",  # Stage 5: differentiation synthesis
        "prd",  # Gate 1: PRD generation
        "architecture",  # Gate 2: architecture proposal
    }
)


# Pricing in USD per 1M tokens. Compute est_cost_usd at call time and persist
# the dollar value (not the formula) so historical rows survive price changes.
# `cache_write_5m` is the default 5-minute ephemeral cache rate;
# `cache_write_1h` is the 1-hour cache rate (Opus 4.7 only — Haiku is short
# calls; the 1h TTL doesn't apply). Per orchestrator feedback 016 Q2 — the
# 1h knob exists for long-running batch reuse (Stage 1 cluster_merge crosses
# the 5-min default TTL boundary).
# verified 2026-05-21 https://docs.anthropic.com/en/docs/about-claude/pricing
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {
        "input": 5.0,
        "output": 25.0,
        "cache_read": 0.50,
        "cache_write_5m": 6.25,
        "cache_write_1h": 10.00,
    },
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_read": 0.10,
        "cache_write_5m": 1.25,
    },
}


# Per-task default reasoning effort for `judge()`. Spend reasoning where it
# pays off: Stage 5 synthesis is the most important call in the system (where
# opportunity quality lives), so it gets `xhigh`. Stage 1 clustering is
# dedup-pass work over narrow choices — `medium` is enough. Callers can
# override with an explicit `effort="..."` kwarg.
#
# Migrated from `DEFAULT_THINKING_BUDGET` (token budgets) on 2026-05-22 when
# Opus 4.7 deprecated `thinking.type="enabled"` + budget_tokens in favor of
# `thinking.type="adaptive"` + `output_config.effort`. The per-task ordering
# is preserved; the absolute mapping (4k→medium, 8k→high, 16k→xhigh, 12k→high)
# is a # heuristic informed by project-brief.md's "default to high effort"
# baseline.
# verified 2026-05-22 https://docs.anthropic.com/en/api/messages
DEFAULT_EFFORT: dict[str, Effort] = {
    "cluster": "medium",  # Stage 1: dedup over narrow choices
    "score": "high",  # Stage 4: quantitative weighing
    "synthesize": "xhigh",  # Stage 5: differentiation — the highest-stakes call
    "prd": "high",  # Gate 1: PRD generation
    "architecture": "high",  # Gate 2: tech-stack proposals
}


# Retry and timeout shape comes from orchestrator feedback 003. SDK default is
# max_retries=2; we override to 3 so the wrapper-side count is visible in
# llm_runs.attempts. Timeouts are task-kind-shaped: judge() with extended
# thinking can take ~2 minutes; mechanic() short calls finish well under 30s.
# verified 2026-05-18 docs/orchestrator/003-feedback.md
_MAX_RETRIES: int = 3
_JUDGE_TIMEOUT_S: int = 120
_MECHANIC_TIMEOUT_S: int = 30


_FALLBACK_EFFORT: Effort = "high"
_JUDGE_MAX_TOKENS: int = 8_000
_MECHANIC_MAX_TOKENS: int = 2_000


# Cache-control markers. The 5-minute ephemeral cache is the SDK default.
# `cache_control={"type": "ephemeral"}` with no `ttl` is equivalent to the
# 5m tier; passing `ttl="1h"` enables the longer cache for batches that
# span the 5-min boundary (Stage 1 cluster_merge in particular).
# verified 2026-05-21 https://docs.anthropic.com/en/docs/about-claude/pricing
_CACHE_CONTROL: dict[CacheTTL, dict[str, str]] = {
    "5m": {"type": "ephemeral"},
    "1h": {"type": "ephemeral", "ttl": "1h"},
}


class PolicyViolation(RuntimeError):
    """Raised when a call would violate the model-selection policy."""


class JSONParseError(RuntimeError):
    """Raised by a `parse_fn` inside `_call` when the LLM response doesn't
    match the requested schema.

    Treated as retryable by the wrapper's retry loop, like `_RETRYABLE` API
    errors. The wrapper logs the truncated raw response on the final attempt's
    failure so debugging has an artifact (per orchestrator feedback 016 Q3).
    """

    def __init__(self, msg: str, *, raw_response: str = "") -> None:
        super().__init__(msg)
        self.raw_response = raw_response[:2000]


_RETRYABLE: tuple[type[Exception], ...] = (
    anthropic.RateLimitError,
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
    JSONParseError,
)


def estimate_cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    cache_ttl: CacheTTL = "5m",
) -> float:
    """Compute USD cost from token counts using the static PRICING table.

    Returns 0.0 if the model isn't in the table — silent, because the row is
    still logged for accounting and a missing price is a config gap, not a bug
    that should crash the pipeline.

    `cache_ttl` picks the right `cache_write_*` rate (5m default; 1h for
    long-batch reuse). Per orchestrator feedback 016 Q2.
    """
    p = PRICING.get(model)
    if p is None:
        return 0.0
    cache_write_key = f"cache_write_{cache_ttl}"
    cache_write_rate = p.get(cache_write_key, p.get("cache_write_5m", 0.0))
    return (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_read_tokens * p["cache_read"]
        + cache_write_tokens * cache_write_rate
    ) / 1_000_000


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter; attempt is 1-indexed."""
    base = 2.0 ** (attempt - 1)
    return base + random.uniform(0, base * 0.25)


# Matches a fenced code block at the start/end of an LLM response. Captures
# the inner content. Permissive about the language tag (`json` is common but
# the model sometimes emits no tag, or a misspelled one).
# heuristic 2026-05-22 — surfaced by runbook 001: Haiku occasionally fences
# JSON output even when the prompt explicitly requests strict JSON. Defensive.
_FENCED_BLOCK_RE = re.compile(
    r"^\s*```(?:[a-zA-Z0-9_-]+)?\s*\n?(.*?)\n?```\s*$",
    re.DOTALL,
)


def _strip_json_fences(text: str) -> str:
    """Strip a leading/trailing markdown code fence from an LLM JSON response.

    Returns the unwrapped JSON when the input is fence-wrapped; returns input
    unchanged when no fences are present. Idempotent.
    """
    match = _FENCED_BLOCK_RE.match(text)
    if match is None:
        return text
    return match.group(1).strip()


def _build_system(
    system: str,
    cache_blocks: list[str] | None,
    *,
    cache_ttl: CacheTTL = "5m",
) -> str | list[dict[str, Any]]:
    """Build the system parameter. With cache_blocks, mark them ephemeral.

    Each entry in `cache_blocks` becomes a system content block tagged with
    `cache_control: {"type": "ephemeral"}` (5-min default) or
    `{"type": "ephemeral", "ttl": "1h"}` (1h tier for long-running batches).
    The per-call `system` string is appended uncached at the end.

    `cache_ttl="1h"` applies to ALL cache_blocks in this call; per-block TTL
    selection isn't a real use case yet (would require a richer parameter
    shape — defer until a call site needs it). Per orchestrator feedback 016 Q2.
    """
    if not cache_blocks:
        return system
    control = _CACHE_CONTROL[cache_ttl]
    blocks: list[dict[str, Any]] = []
    for block in cache_blocks:
        blocks.append(
            {
                "type": "text",
                "text": block,
                "cache_control": control,
            }
        )
    blocks.append({"type": "text", "text": system})
    return blocks


def _ms_since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class LLMClient:
    """The single Anthropic entrypoint. Construct once per process (or per test)."""

    def __init__(
        self,
        *,
        client: anthropic.Anthropic | None = None,
        _session_factory: Callable[[], Session] | None = None,
    ) -> None:
        # max_retries=0 — the wrapper owns the retry loop so it can log attempts.
        self._client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            max_retries=0,
        )
        # Test seam (not public API): override the session factory in unit tests.
        self._session_factory: Callable[[], Session] = _session_factory or SessionLocal

    def judge(
        self,
        task: str,
        system: str,
        messages: list[dict[str, Any]],
        *,
        cache_blocks: list[str] | None = None,
        cache_ttl: CacheTTL = "5m",
        effort: Effort | None = None,
        max_tokens: int = _JUDGE_MAX_TOKENS,
        candidate_id: int | None = None,
    ) -> Message:
        """Opus 4.7 with adaptive extended thinking. Use for any task that needs judgment.

        `effort=None` (default) → look up DEFAULT_EFFORT[task], falling back to
        `_FALLBACK_EFFORT` if the task isn't in the dict. Pass an explicit
        Effort literal to override.

        `cache_ttl="1h"` opts into the 1-hour cache tier (more expensive write,
        useful for batches that exceed the 5-min default TTL — Stage 1's
        cluster_merge pass in particular). Per orchestrator feedback 016 Q2.

        Migrated from `thinking_budget_tokens` on 2026-05-22: Opus 4.7
        deprecated the explicit-token-budget API in favor of adaptive thinking
        + `output_config.effort`.
        """
        chosen_effort: Effort = (
            effort if effort is not None else DEFAULT_EFFORT.get(task, _FALLBACK_EFFORT)
        )
        return self._call(
            model=JUDGE_MODEL,
            task=task,
            system=system,
            messages=messages,
            cache_blocks=cache_blocks,
            cache_ttl=cache_ttl,
            timeout_s=_JUDGE_TIMEOUT_S,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            effort=chosen_effort,
            candidate_id=candidate_id,
        )

    def judge_json(
        self,
        task: str,
        system: str,
        messages: list[dict[str, Any]],
        *,
        schema: type[T],
        cache_blocks: list[str] | None = None,
        cache_ttl: CacheTTL = "5m",
        effort: Effort | None = None,
        max_tokens: int = _JUDGE_MAX_TOKENS,
        candidate_id: int | None = None,
    ) -> T:
        """Opus 4.7 + Pydantic schema validation. Retries on JSONParseError.

        The prompt MUST instruct the model to emit JSON that matches `schema`.
        On schema mismatch, raises `JSONParseError` — treated as retryable by
        the wrapper's retry loop alongside transient API errors. The final-
        attempt failure logs the truncated raw response into `llm_runs.error`.

        `schema` is a Pydantic model class with `model_validate_json` available.
        Per orchestrator feedback 016 Q3.
        """
        chosen_effort: Effort = (
            effort if effort is not None else DEFAULT_EFFORT.get(task, _FALLBACK_EFFORT)
        )

        def parse(msg: Message) -> T:
            text = "".join(
                cast(Any, b).text for b in msg.content if getattr(b, "type", None) == "text"
            )
            # Strip markdown code fences defensively — LLMs occasionally wrap
            # JSON even when explicitly told not to (per runbook 001 finding).
            cleaned = _strip_json_fences(text)
            try:
                # Pydantic's model_validate_json gives canonical error messages
                # and avoids a round-trip through json.loads.
                return cast(Any, schema).model_validate_json(cleaned)
            except Exception as e:  # noqa: BLE001 — ValidationError + json errors
                raise JSONParseError(
                    f"{task!r}: response did not match schema {schema.__name__}: {e}",
                    raw_response=text,
                ) from e

        return cast(
            T,
            self._call(
                model=JUDGE_MODEL,
                task=task,
                system=system,
                messages=messages,
                cache_blocks=cache_blocks,
                cache_ttl=cache_ttl,
                timeout_s=_JUDGE_TIMEOUT_S,
                max_tokens=max_tokens,
                thinking={"type": "adaptive"},
                effort=chosen_effort,
                candidate_id=candidate_id,
                parse_fn=parse,
            ),
        )

    def mechanic(
        self,
        task: str,
        system: str,
        messages: list[dict[str, Any]],
        *,
        cache_blocks: list[str] | None = None,
        max_tokens: int = _MECHANIC_MAX_TOKENS,
        candidate_id: int | None = None,
    ) -> Message:
        """Haiku 4.5 for trivial mechanical work. Refuses tasks listed in JUDGMENT_TASKS."""
        if task in JUDGMENT_TASKS:
            raise PolicyViolation(
                f"task={task!r} requires judge() (Opus 4.7), not mechanic() (Haiku 4.5). "
                "If you genuinely need Haiku here, add rationale to JUDGMENT_TASKS in client.py."
            )
        return self._call(
            model=MECHANIC_MODEL,
            task=task,
            system=system,
            messages=messages,
            cache_blocks=cache_blocks,
            cache_ttl="5m",  # Haiku doesn't use the 1h tier in practice
            timeout_s=_MECHANIC_TIMEOUT_S,
            max_tokens=max_tokens,
            thinking=None,
            candidate_id=candidate_id,
        )

    def mechanic_json(
        self,
        task: str,
        system: str,
        messages: list[dict[str, Any]],
        *,
        schema: type[T],
        cache_blocks: list[str] | None = None,
        max_tokens: int = _MECHANIC_MAX_TOKENS,
        candidate_id: int | None = None,
    ) -> T:
        """Haiku 4.5 + Pydantic schema validation. Retries on JSONParseError.

        Same shape as `judge_json` but for mechanical work (Stage 1's per-signal
        Haiku pre-pass extracting `{core_complaint, vertical, keywords}`).
        Refuses tasks in JUDGMENT_TASKS.
        """
        if task in JUDGMENT_TASKS:
            raise PolicyViolation(
                f"task={task!r} requires judge_json() (Opus 4.7), not mechanic_json() (Haiku 4.5)."
            )

        def parse(msg: Message) -> T:
            text = "".join(
                cast(Any, b).text for b in msg.content if getattr(b, "type", None) == "text"
            )
            cleaned = _strip_json_fences(text)
            try:
                return cast(Any, schema).model_validate_json(cleaned)
            except Exception as e:  # noqa: BLE001
                raise JSONParseError(
                    f"{task!r}: response did not match schema {schema.__name__}: {e}",
                    raw_response=text,
                ) from e

        return cast(
            T,
            self._call(
                model=MECHANIC_MODEL,
                task=task,
                system=system,
                messages=messages,
                cache_blocks=cache_blocks,
                cache_ttl="5m",
                timeout_s=_MECHANIC_TIMEOUT_S,
                max_tokens=max_tokens,
                thinking=None,
                candidate_id=candidate_id,
                parse_fn=parse,
            ),
        )

    def _call(
        self,
        *,
        model: str,
        task: str,
        system: str,
        messages: list[dict[str, Any]],
        cache_blocks: list[str] | None,
        cache_ttl: CacheTTL,
        timeout_s: int,
        max_tokens: int,
        thinking: dict[str, Any] | None,
        candidate_id: int | None,
        effort: Effort | None = None,
        parse_fn: Callable[[Message], Any] | None = None,
    ) -> Any:
        """Inner call/retry loop.

        Returns `Message` when `parse_fn` is None (the historical shape).
        When `parse_fn` is provided, applies it to the response and returns
        its result; a `JSONParseError` raised by `parse_fn` is treated as
        retryable in the same loop as transient API errors. On the final
        attempt's `JSONParseError`, the truncated raw response is logged into
        `llm_runs.error` for debugging (per orchestrator feedback 016 Q3).
        """
        kwargs: dict[str, Any] = {
            "model": model,
            "system": _build_system(system, cache_blocks, cache_ttl=cache_ttl),
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if thinking is not None:
            kwargs["thinking"] = thinking
        if effort is not None:
            # Per the 2026-05-22 Opus 4.7 API migration: effort lives under
            # `output_config`, not directly on the top-level kwargs.
            kwargs["output_config"] = {"effort": effort}

        retry_log: list[dict[str, Any]] = []
        started = time.monotonic()
        for attempt in range(1, _MAX_RETRIES + 1):
            attempt_started = time.monotonic()
            try:
                # Cast: SDK's create() overloads don't resolve through **kwargs.
                msg = cast(
                    Message,
                    self._client.with_options(timeout=timeout_s, max_retries=0).messages.create(
                        **kwargs
                    ),
                )
            except _RETRYABLE as e:
                if attempt < _MAX_RETRIES:
                    retry_log.append(
                        {
                            "attempt": attempt,
                            "error_type": type(e).__name__,
                            "error_msg": str(e),
                            "latency_ms": int((time.monotonic() - attempt_started) * 1000),
                        }
                    )
                    time.sleep(_backoff_seconds(attempt))
                    continue
                self._log_failure(
                    task=task,
                    model=model,
                    attempts=attempt,
                    latency_ms=_ms_since(started),
                    candidate_id=candidate_id,
                    error=f"{type(e).__name__}: {e}",
                    retry_log_json=retry_log,
                )
                raise
            except anthropic.APIError as e:
                # Non-retryable (4xx other than 429, auth, schema). Log and re-raise.
                self._log_failure(
                    task=task,
                    model=model,
                    attempts=attempt,
                    latency_ms=_ms_since(started),
                    candidate_id=candidate_id,
                    error=f"{type(e).__name__}: {e}",
                    retry_log_json=retry_log,
                )
                raise

            # Token accounting happens before the parse step so even a
            # parse-failure attempt is fully logged.
            #
            # NOTE: the legacy `_maybe_warn_budget` retune signal (per feedback
            # 005) doesn't apply under the adaptive-thinking API — there's no
            # explicit budget to be ">90% of." The retune discipline needs
            # revisiting; see draft request 018.
            usage = cast(Any, msg.usage)
            input_tokens = int(usage.input_tokens or 0)
            output_tokens = int(usage.output_tokens or 0)
            cache_read = int(getattr(usage, "cache_read_input_tokens", None) or 0)
            cache_write = int(getattr(usage, "cache_creation_input_tokens", None) or 0)

            parsed: Any = msg
            if parse_fn is not None:
                try:
                    parsed = parse_fn(msg)
                except JSONParseError as e:
                    if attempt < _MAX_RETRIES:
                        retry_log.append(
                            {
                                "attempt": attempt,
                                "error_type": "JSONParseError",
                                "error_msg": str(e),
                                "raw_response": e.raw_response,
                                "latency_ms": int((time.monotonic() - attempt_started) * 1000),
                            }
                        )
                        time.sleep(_backoff_seconds(attempt))
                        continue
                    # Final attempt failed; log with truncated raw response.
                    self._log_failure(
                        task=task,
                        model=model,
                        attempts=attempt,
                        latency_ms=_ms_since(started),
                        candidate_id=candidate_id,
                        error=f"JSONParseError: {e}\nraw_response[:2000]={e.raw_response!r}",
                        retry_log_json=retry_log,
                    )
                    raise

            self._log_success(
                task=task,
                model=model,
                attempts=attempt,
                latency_ms=_ms_since(started),
                candidate_id=candidate_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                est_cost_usd=estimate_cost_usd(
                    model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read,
                    cache_write_tokens=cache_write,
                    cache_ttl=cache_ttl,
                ),
                retry_log_json=retry_log,
            )
            return parsed

        # The retry loop returns or raises on every path; if we reach here, something is wrong.
        raise RuntimeError("LLMClient retry loop exited without success or exception")

    def _log_success(
        self,
        *,
        task: str,
        model: str,
        attempts: int,
        latency_ms: int,
        candidate_id: int | None,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        est_cost_usd: float,
        retry_log_json: list[dict[str, Any]],
    ) -> None:
        with self._session_factory() as s:
            s.add(
                LLMRun(
                    task=task,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                    latency_ms=latency_ms,
                    est_cost_usd=est_cost_usd,
                    attempts=attempts,
                    candidate_id=candidate_id,
                    ok=True,
                    error=None,
                    retry_log_json=retry_log_json,
                )
            )
            s.commit()

    def _log_failure(
        self,
        *,
        task: str,
        model: str,
        attempts: int,
        latency_ms: int,
        candidate_id: int | None,
        error: str,
        retry_log_json: list[dict[str, Any]],
    ) -> None:
        with self._session_factory() as s:
            s.add(
                LLMRun(
                    task=task,
                    model=model,
                    latency_ms=latency_ms,
                    attempts=attempts,
                    candidate_id=candidate_id,
                    ok=False,
                    error=error,
                    retry_log_json=retry_log_json,
                )
            )
            s.commit()
