"""Single Anthropic entrypoint — enforces model policy, retries, logs every call.

See CLAUDE.md → "Model selection policy". Two entrypoints:
- `judge()` → Opus 4.7 with extended thinking enabled. For any judgment task.
- `mechanic()` → Haiku 4.5. For trivial mechanical work (dedup, classification,
  JSON reshape). Refuses to run a task listed in `JUDGMENT_TASKS`.

No method accepts an arbitrary `model` string. If a third model is genuinely
needed, add a new entrypoint here with rationale and update the policy.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any, cast

import anthropic
from anthropic.types import Message
from sqlalchemy.orm import Session

from apfun.config import settings
from apfun.db import SessionLocal
from apfun.models import LLMRun

JUDGE_MODEL: str = "claude-opus-4-7"
MECHANIC_MODEL: str = "claude-haiku-4-5"


# Tasks that REQUIRE judge() — calling mechanic() with one of these is a policy
# violation. New judgment tasks must be added here with a brief rationale.
JUDGMENT_TASKS: frozenset[str] = frozenset(
    {
        "cluster",  # Stage 1: clustering raw signals into idea cards
        "score",  # Stage 4: complaint clustering for UnmetPain
        "synthesize",  # Stage 5: differentiation synthesis
        "prd",  # Gate 1: PRD generation
        "architecture",  # Gate 2: architecture proposal
    }
)


# Pricing in USD per 1M tokens. Compute est_cost_usd at call time and persist the
# dollar value (not the formula), so historical rows survive price changes.
# When Anthropic posts new prices: update the numbers AND bump the verified date.
# verified 2026-05-18
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {
        "input": 15.0,
        "output": 75.0,
        "cache_read": 1.50,
        "cache_write": 18.75,
    },
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_read": 0.10,
        "cache_write": 1.25,
    },
}


_MAX_RETRIES: int = 3
_JUDGE_TIMEOUT_S: int = 120
_MECHANIC_TIMEOUT_S: int = 30
_JUDGE_THINKING_BUDGET_TOKENS: int = 12_000
_JUDGE_MAX_TOKENS: int = 8_000
_MECHANIC_MAX_TOKENS: int = 2_000


_RETRYABLE: tuple[type[Exception], ...] = (
    anthropic.RateLimitError,
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
)


class PolicyViolation(RuntimeError):
    """Raised when a call would violate the model-selection policy."""


def estimate_cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> float:
    """Compute USD cost from token counts using the static PRICING table.

    Returns 0.0 if the model isn't in the table — silent, because the row is
    still logged for accounting and a missing price is a config gap, not a bug
    that should crash the pipeline.
    """
    p = PRICING.get(model)
    if p is None:
        return 0.0
    return (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_read_tokens * p["cache_read"]
        + cache_write_tokens * p["cache_write"]
    ) / 1_000_000


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter; attempt is 1-indexed."""
    base = 2.0 ** (attempt - 1)
    return base + random.uniform(0, base * 0.25)


def _build_system(system: str, cache_blocks: list[str] | None) -> str | list[dict[str, Any]]:
    """Build the system parameter. With cache_blocks, mark them ephemeral."""
    if not cache_blocks:
        return system
    blocks: list[dict[str, Any]] = []
    for block in cache_blocks:
        blocks.append(
            {
                "type": "text",
                "text": block,
                "cache_control": {"type": "ephemeral"},
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
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        # max_retries=0 — the wrapper owns the retry loop so it can log attempts.
        self._client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            max_retries=0,
        )
        self._session_factory: Callable[[], Session] = session_factory or SessionLocal

    def judge(
        self,
        task: str,
        system: str,
        messages: list[dict[str, Any]],
        *,
        cache_blocks: list[str] | None = None,
        thinking_budget_tokens: int = _JUDGE_THINKING_BUDGET_TOKENS,
        max_tokens: int = _JUDGE_MAX_TOKENS,
        candidate_id: int | None = None,
    ) -> Message:
        """Opus 4.7 with extended thinking. Use for any task that needs judgment."""
        return self._call(
            model=JUDGE_MODEL,
            task=task,
            system=system,
            messages=messages,
            cache_blocks=cache_blocks,
            timeout_s=_JUDGE_TIMEOUT_S,
            max_tokens=max_tokens,
            thinking={"type": "enabled", "budget_tokens": thinking_budget_tokens},
            candidate_id=candidate_id,
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
            timeout_s=_MECHANIC_TIMEOUT_S,
            max_tokens=max_tokens,
            thinking=None,
            candidate_id=candidate_id,
        )

    def _call(
        self,
        *,
        model: str,
        task: str,
        system: str,
        messages: list[dict[str, Any]],
        cache_blocks: list[str] | None,
        timeout_s: int,
        max_tokens: int,
        thinking: dict[str, Any] | None,
        candidate_id: int | None,
    ) -> Message:
        kwargs: dict[str, Any] = {
            "model": model,
            "system": _build_system(system, cache_blocks),
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if thinking is not None:
            kwargs["thinking"] = thinking

        started = time.monotonic()
        for attempt in range(1, _MAX_RETRIES + 1):
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
                    time.sleep(_backoff_seconds(attempt))
                    continue
                self._log_failure(
                    task=task,
                    model=model,
                    attempts=attempt,
                    latency_ms=_ms_since(started),
                    candidate_id=candidate_id,
                    error=f"{type(e).__name__}: {e}",
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
                )
                raise

            usage = cast(Any, msg.usage)
            input_tokens = int(usage.input_tokens or 0)
            output_tokens = int(usage.output_tokens or 0)
            cache_read = int(getattr(usage, "cache_read_input_tokens", None) or 0)
            cache_write = int(getattr(usage, "cache_creation_input_tokens", None) or 0)
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
                ),
            )
            return msg

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
                )
            )
            s.commit()
