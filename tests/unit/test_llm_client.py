"""Unit tests for the LLM client. Mocks the Anthropic SDK; no network."""

from __future__ import annotations

import logging
from collections.abc import Callable
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from apfun.llm.client import (
    DEFAULT_THINKING_BUDGET,
    JUDGE_MODEL,
    JUDGMENT_TASKS,
    MECHANIC_MODEL,
    LLMClient,
    PolicyViolation,
    estimate_cost_usd,
)
from apfun.models import LLMRun


def _make_response(
    *,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: int = 0,
    cache_write: int = 0,
    has_thinking: bool = False,
) -> MagicMock:
    msg = MagicMock()
    msg.usage.input_tokens = input_tokens
    msg.usage.output_tokens = output_tokens
    msg.usage.cache_read_input_tokens = cache_read
    msg.usage.cache_creation_input_tokens = cache_write
    if has_thinking:
        msg.content = [MagicMock(type="thinking"), MagicMock(type="text")]
    else:
        msg.content = [MagicMock(type="text")]
    return msg


def _rate_limit_error() -> anthropic.RateLimitError:
    """Construct a real RateLimitError without hitting the network."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(429, request=request)
    return anthropic.RateLimitError(message="rate limited", response=response, body=None)


# Returned by `make_client` so tests reach the underlying MagicMock by name,
# avoiding pyright errors on the LLMClient's private `_client` attribute.
ClientPair = tuple[LLMClient, MagicMock]


@pytest.fixture
def make_client(engine: Engine) -> Callable[..., ClientPair]:
    factory = sessionmaker(bind=engine)

    def _make(*, side_effect: object = None, response: object = None) -> ClientPair:
        mock = MagicMock()
        mock.with_options.return_value = mock  # chainable
        if side_effect is not None:
            mock.messages.create.side_effect = side_effect
        else:
            mock.messages.create.return_value = (
                response if response is not None else _make_response()
            )
        client = LLMClient(client=mock, _session_factory=lambda: factory())
        return client, mock

    return _make


def test_mechanic_rejects_every_judgment_task(make_client: Callable[..., ClientPair]) -> None:
    client, _ = make_client()
    for task in JUDGMENT_TASKS:
        with pytest.raises(PolicyViolation, match=task):
            client.mechanic(task, "sys", [{"role": "user", "content": "hi"}])


def test_mechanic_uses_haiku_no_thinking(make_client: Callable[..., ClientPair]) -> None:
    client, mock = make_client()
    client.mechanic("dedup", "sys", [{"role": "user", "content": "hi"}])
    kwargs = mock.messages.create.call_args.kwargs
    assert kwargs["model"] == MECHANIC_MODEL
    assert "thinking" not in kwargs


def test_judge_uses_opus_with_explicit_thinking_budget(
    make_client: Callable[..., ClientPair],
) -> None:
    client, mock = make_client(response=_make_response(has_thinking=True))
    client.judge(
        "synthesize",
        "sys",
        [{"role": "user", "content": "go"}],
        thinking_budget_tokens=10_000,
    )
    kwargs = mock.messages.create.call_args.kwargs
    assert kwargs["model"] == JUDGE_MODEL
    assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 10_000}


@pytest.mark.parametrize(("task", "expected"), list(DEFAULT_THINKING_BUDGET.items()))
def test_judge_per_task_default_thinking_budget(
    make_client: Callable[..., ClientPair], task: str, expected: int
) -> None:
    """Each task in DEFAULT_THINKING_BUDGET gets its specific budget when none is passed."""
    client, mock = make_client()
    client.judge(task, "sys", [{"role": "user", "content": "go"}])
    thinking = mock.messages.create.call_args.kwargs["thinking"]
    assert thinking == {"type": "enabled", "budget_tokens": expected}


def test_judge_unknown_task_uses_fallback_budget(
    make_client: Callable[..., ClientPair],
) -> None:
    """A task not in DEFAULT_THINKING_BUDGET falls back to 12000."""
    client, mock = make_client()
    client.judge("competitor_pricing_review", "sys", [{"role": "user", "content": "go"}])
    assert mock.messages.create.call_args.kwargs["thinking"]["budget_tokens"] == 12_000


def test_logs_to_llm_runs_with_correct_cost(
    make_client: Callable[..., ClientPair], engine: Engine
) -> None:
    response = _make_response(
        input_tokens=10_000, output_tokens=2_000, cache_read=5_000, cache_write=0
    )
    client, _ = make_client(response=response)
    client.judge("synthesize", "sys", [{"role": "user", "content": "go"}])

    with Session(engine) as s:
        row = s.execute(select(LLMRun)).scalar_one()
    assert row.model == JUDGE_MODEL
    assert row.task == "synthesize"
    assert row.input_tokens == 10_000
    assert row.output_tokens == 2_000
    assert row.cache_read_tokens == 5_000
    assert row.attempts == 1
    assert row.ok is True
    assert row.retry_log_json == []
    expected = estimate_cost_usd(
        JUDGE_MODEL,
        input_tokens=10_000,
        output_tokens=2_000,
        cache_read_tokens=5_000,
        cache_write_tokens=0,
    )
    assert row.est_cost_usd == pytest.approx(expected)


def test_cache_blocks_marked_ephemeral(make_client: Callable[..., ClientPair]) -> None:
    client, mock = make_client()
    client.mechanic(
        "dedup",
        "per-call instructions",
        [{"role": "user", "content": "hi"}],
        cache_blocks=["LONG STATIC PREAMBLE"],
    )
    system = mock.messages.create.call_args.kwargs["system"]
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[0]["text"] == "LONG STATIC PREAMBLE"
    assert system[1] == {"type": "text", "text": "per-call instructions"}


def test_retry_then_succeed_logs_attempts_2_and_retry_log(
    make_client: Callable[..., ClientPair],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apfun.llm.client.time.sleep", lambda _: None)
    client, _ = make_client(side_effect=[_rate_limit_error(), _make_response()])
    client.mechanic("dedup", "sys", [{"role": "user", "content": "hi"}])
    with Session(engine) as s:
        row = s.execute(select(LLMRun)).scalar_one()
    assert row.ok is True
    assert row.attempts == 2
    # The first (failed) attempt is in retry_log_json; the successful final
    # attempt's outcome is in the top-level columns.
    assert len(row.retry_log_json) == 1
    assert row.retry_log_json[0]["attempt"] == 1
    assert row.retry_log_json[0]["error_type"] == "RateLimitError"
    assert "latency_ms" in row.retry_log_json[0]


def test_retry_exhausted_logs_failure_and_full_retry_log(
    make_client: Callable[..., ClientPair],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apfun.llm.client.time.sleep", lambda _: None)
    client, _ = make_client(
        side_effect=[_rate_limit_error(), _rate_limit_error(), _rate_limit_error()]
    )
    with pytest.raises(anthropic.RateLimitError):
        client.mechanic("dedup", "sys", [{"role": "user", "content": "hi"}])
    with Session(engine) as s:
        row = s.execute(select(LLMRun)).scalar_one()
    assert row.ok is False
    assert row.attempts == 3
    assert row.error is not None and "RateLimitError" in row.error
    # retry_log_json contains the two failed attempts BEFORE the final one;
    # the final attempt's error is in the top-level `error` column.
    assert len(row.retry_log_json) == 2
    assert [r["attempt"] for r in row.retry_log_json] == [1, 2]


def test_timeout_passed_per_call(make_client: Callable[..., ClientPair]) -> None:
    """mechanic gets 30s, judge gets 120s — verified via with_options call args."""
    client_m, mock_m = make_client()
    client_m.mechanic("dedup", "sys", [{"role": "user", "content": "hi"}])
    assert mock_m.with_options.call_args.kwargs["timeout"] == 30

    client_j, mock_j = make_client()
    client_j.judge("cluster", "sys", [{"role": "user", "content": "go"}])
    assert mock_j.with_options.call_args.kwargs["timeout"] == 120


def test_judge_warns_when_output_approaches_thinking_budget(
    make_client: Callable[..., ClientPair],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """cluster's budget is 4000; output_tokens=3700 (92%) should warn."""
    response = _make_response(output_tokens=3_700)
    client, _ = make_client(response=response)
    with caplog.at_level(logging.WARNING, logger="apfun.llm.client"):
        client.judge("cluster", "sys", [{"role": "user", "content": "x"}])
    assert any("thinking budget" in r.getMessage() for r in caplog.records)


def test_judge_does_not_warn_below_threshold(
    make_client: Callable[..., ClientPair],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Below the 90% threshold, no warning."""
    response = _make_response(output_tokens=100)
    client, _ = make_client(response=response)
    with caplog.at_level(logging.WARNING, logger="apfun.llm.client"):
        client.judge("cluster", "sys", [{"role": "user", "content": "x"}])
    assert not any("thinking budget" in r.getMessage() for r in caplog.records)


def test_mechanic_does_not_warn_about_thinking_budget(
    make_client: Callable[..., ClientPair],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """mechanic() has no thinking — never triggers the budget warning."""
    response = _make_response(output_tokens=2_000)
    client, _ = make_client(response=response)
    with caplog.at_level(logging.WARNING, logger="apfun.llm.client"):
        client.mechanic("dedup", "sys", [{"role": "user", "content": "x"}])
    assert not any("thinking budget" in r.getMessage() for r in caplog.records)
