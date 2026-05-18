"""Integration tests for the LLM client — real Anthropic API.

Each test costs a few cents. Marked @pytest.mark.integration so `make test`
skips them by default; run via `make test-all` with APFUN_ANTHROPIC_API_KEY set.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from apfun.llm.client import JUDGE_MODEL, MECHANIC_MODEL, LLMClient
from apfun.models import LLMRun

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("APFUN_ANTHROPIC_API_KEY"),
        reason="APFUN_ANTHROPIC_API_KEY not set",
    ),
]


@pytest.fixture
def client(engine: Engine) -> LLMClient:
    factory = sessionmaker(bind=engine)
    return LLMClient(session_factory=lambda: factory())


def test_judge_smoke(client: LLMClient, engine: Engine) -> None:
    """Real call to claude-opus-4-7 — response must contain a thinking block."""
    msg = client.judge(
        "cluster",
        'Respond with a one-key JSON object: {"ok": true}. Nothing else.',
        [{"role": "user", "content": "echo"}],
        thinking_budget_tokens=4_000,
        max_tokens=512,
    )
    types = [getattr(b, "type", None) for b in msg.content]
    assert "thinking" in types, f"expected a thinking block, got types={types!r}"

    with Session(engine) as s:
        row = s.execute(select(LLMRun)).scalar_one()
    assert row.model == JUDGE_MODEL
    assert row.task == "cluster"
    assert row.ok is True
    assert row.attempts >= 1
    assert row.input_tokens > 0
    assert row.output_tokens > 0


def test_mechanic_smoke(client: LLMClient, engine: Engine) -> None:
    """Real call to claude-haiku-4-5 — short reply, no thinking block."""
    msg = client.mechanic(
        "dedup",
        "Reply with the single word 'ok'. No punctuation.",
        [{"role": "user", "content": "go"}],
        max_tokens=10,
    )
    text = "".join(
        getattr(b, "text", "") for b in msg.content if getattr(b, "type", None) == "text"
    )
    assert "ok" in text.lower()

    with Session(engine) as s:
        row = s.execute(select(LLMRun)).scalar_one()
    assert row.model == MECHANIC_MODEL
    assert row.task == "dedup"
    assert row.ok is True
