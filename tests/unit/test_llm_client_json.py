"""Tests for `judge_json` / `mechanic_json` and JSONParseError retry semantics.

Pins the feedback-016 Q3 invariants:
- Wrapper retries JSONParseError inside the same retry budget as API errors.
- Final-attempt parse-failure logs the truncated raw response into llm_runs.error.
- Schema-valid responses are returned as the schema instance, not Message.
- cache_ttl is plumbed through (5m default; 1h opt-in for batch reuse).
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from apfun.llm.client import (
    JSONParseError,
    LLMClient,
    PolicyViolation,
)
from apfun.models import LLMRun


class DemoSchema(BaseModel):
    title: str
    score: int


def _make_response(*, text_body: str = '{"title":"hello","score":1}') -> MagicMock:
    msg = MagicMock()
    msg.usage.input_tokens = 100
    msg.usage.output_tokens = 50
    msg.usage.cache_read_input_tokens = 0
    msg.usage.cache_creation_input_tokens = 0
    text_block = MagicMock(type="text")
    text_block.text = text_body
    msg.content = [text_block]
    return msg


ClientPair = tuple[LLMClient, MagicMock]


@pytest.fixture
def make_client(engine: Engine) -> Callable[..., ClientPair]:
    factory = sessionmaker(bind=engine)

    def _make(*, side_effect: object = None, response: object = None) -> ClientPair:
        mock = MagicMock()
        mock.with_options.return_value = mock
        if side_effect is not None:
            mock.messages.create.side_effect = side_effect
        else:
            mock.messages.create.return_value = (
                response if response is not None else _make_response()
            )
        client = LLMClient(client=mock, _session_factory=lambda: factory())
        return client, mock

    return _make


def test_judge_json_returns_validated_instance(make_client: Callable[..., ClientPair]) -> None:
    client, mock = make_client()
    out = client.judge_json(
        "cluster",
        "sys",
        [{"role": "user", "content": "go"}],
        schema=DemoSchema,
    )
    assert isinstance(out, DemoSchema)
    assert out.title == "hello"
    assert out.score == 1
    # cache_ttl default is 5m
    kwargs = mock.messages.create.call_args.kwargs
    # System is the raw string (no cache_blocks passed), so we just confirm presence.
    assert kwargs["model"] == "claude-opus-4-7"


def test_judge_json_passes_cache_ttl_1h(make_client: Callable[..., ClientPair]) -> None:
    """1h tier opts into the longer TTL cache_control marker on cache_blocks."""
    client, mock = make_client()
    client.judge_json(
        "cluster",
        "sys",
        [{"role": "user", "content": "go"}],
        schema=DemoSchema,
        cache_blocks=["block-A", "block-B"],
        cache_ttl="1h",
    )
    kwargs = mock.messages.create.call_args.kwargs
    system_blocks = kwargs["system"]
    assert isinstance(system_blocks, list)
    # Find one of the cache_blocks — its cache_control should carry ttl="1h".
    cache_marked = [b for b in system_blocks if b.get("text") == "block-A"]
    assert cache_marked, "block-A should be present as a system block"
    assert cache_marked[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_jsonparseerror_retries_within_budget_then_succeeds(
    make_client: Callable[..., ClientPair], engine: Engine
) -> None:
    """Bad JSON on attempts 1+2, good JSON on attempt 3 → success."""
    client, mock = make_client(
        side_effect=[
            _make_response(text_body="not json at all"),
            _make_response(text_body='{"title":"ok"}'),  # missing 'score' field
            _make_response(text_body='{"title":"good","score":42}'),
        ]
    )
    out = client.judge_json(
        "cluster",
        "sys",
        [{"role": "user", "content": "go"}],
        schema=DemoSchema,
    )
    assert isinstance(out, DemoSchema)
    assert out.title == "good"
    assert out.score == 42
    assert mock.messages.create.call_count == 3

    # llm_runs row records attempts=3 with empty error (final attempt succeeded)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        row = s.execute(select(LLMRun)).scalar_one()
    assert row.attempts == 3
    assert row.ok is True
    assert row.error is None
    # retry_log_json should have entries for the first two failed attempts
    assert len(row.retry_log_json) == 2
    assert all(e["error_type"] == "JSONParseError" for e in row.retry_log_json)


def test_jsonparseerror_final_failure_logs_truncated_response(
    make_client: Callable[..., ClientPair], engine: Engine
) -> None:
    """All attempts return bad JSON → JSONParseError surfaces; raw is logged."""
    bad_body = "x" * 3000  # exceeds 2k truncation
    client, _mock = make_client(
        side_effect=[
            _make_response(text_body=bad_body),
            _make_response(text_body=bad_body),
            _make_response(text_body=bad_body),
        ]
    )
    with pytest.raises(JSONParseError):
        client.judge_json(
            "cluster",
            "sys",
            [{"role": "user", "content": "go"}],
            schema=DemoSchema,
        )

    factory = sessionmaker(bind=engine)
    with factory() as s:
        row = s.execute(select(LLMRun)).scalar_one()
    assert row.ok is False
    assert row.error is not None
    assert "JSONParseError" in row.error
    # Truncated to 2000 chars max (inside the JSONParseError class itself).
    assert "x" * 2000 in row.error
    assert "x" * 2001 not in row.error


def test_mechanic_json_refuses_judgment_tasks(make_client: Callable[..., ClientPair]) -> None:
    client, _ = make_client()
    with pytest.raises(PolicyViolation, match="cluster"):
        client.mechanic_json(
            "cluster",
            "sys",
            [{"role": "user", "content": "go"}],
            schema=DemoSchema,
        )


def test_mechanic_json_happy_path(make_client: Callable[..., ClientPair]) -> None:
    client, mock = make_client()
    out = client.mechanic_json(
        "dedup",
        "sys",
        [{"role": "user", "content": "go"}],
        schema=DemoSchema,
    )
    assert isinstance(out, DemoSchema)
    kwargs = mock.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-4-5"
    # Haiku never sets thinking.
    assert "thinking" not in kwargs


def test_estimate_cost_usd_1h_tier(make_client: Callable[..., ClientPair]) -> None:
    """Cost calc picks cache_write_1h rate when cache_ttl='1h' is set."""
    from apfun.llm.client import estimate_cost_usd

    cost_5m = estimate_cost_usd(
        "claude-opus-4-7",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=1_000_000,
        cache_ttl="5m",
    )
    cost_1h = estimate_cost_usd(
        "claude-opus-4-7",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=1_000_000,
        cache_ttl="1h",
    )
    # PRICING: cache_write_5m=6.25, cache_write_1h=10.00 per MTok
    assert cost_5m == pytest.approx(6.25)
    assert cost_1h == pytest.approx(10.00)
    assert cost_1h > cost_5m


def test_haiku_no_cache_write_1h_rate_falls_back_to_5m(
    make_client: Callable[..., ClientPair],
) -> None:
    """Haiku has no cache_write_1h key; passing cache_ttl='1h' falls back
    to the 5m rate rather than raising or returning 0."""
    from apfun.llm.client import estimate_cost_usd

    cost = estimate_cost_usd(
        "claude-haiku-4-5",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=1_000_000,
        cache_ttl="1h",
    )
    # Falls back to cache_write_5m = 1.25
    assert cost == pytest.approx(1.25)


def test_jsonparseerror_truncates_at_2k() -> None:
    e = JSONParseError("bad", raw_response="x" * 5000)
    assert len(e.raw_response) == 2000


# Mechanic JSON retry semantics — same shape as judge but with Haiku.
def test_mechanic_json_retries_jsonparseerror(
    make_client: Callable[..., ClientPair],
) -> None:
    client, mock = make_client(
        side_effect=[
            _make_response(text_body="garbage"),
            _make_response(text_body='{"title":"ok","score":7}'),
        ]
    )
    out = client.mechanic_json(
        "dedup",
        "sys",
        [{"role": "user", "content": "go"}],
        schema=DemoSchema,
    )
    assert out.score == 7
    assert mock.messages.create.call_count == 2
