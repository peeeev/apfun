"""Capture a real Claude Opus 4.7 response and save it as the SDK shape tripwire fixture.

Two API calls: the first writes the prompt cache; the second reads it. The second
response has both `cache_creation_input_tokens` (from the original write event) and
`cache_read_input_tokens` populated — which is what
`tests/unit/test_anthropic_response_shape.py` checks.

Costs a few cents. Run once when convenient with a valid `APFUN_ANTHROPIC_API_KEY`
to replace the synthetic fixture at `tests/fixtures/opus_4_7_with_cache.json`.
Re-run after major anthropic SDK bumps if the response schema might have changed.

Usage::

    APFUN_ANTHROPIC_API_KEY=sk-... uv run python scripts/capture_response_fixture.py
"""

from __future__ import annotations

import json
from pathlib import Path

from anthropic import Anthropic

from apfun.config import settings

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "opus_4_7_with_cache.json"
)


# Long enough to clear Opus's ~1024-token minimum cache size.
_LONG_PREAMBLE = (
    "You are apfun's analyst. apfun is a SaaS opportunity funnel that mines signal "
    "from review sites, subreddits, Hacker News, and competitor comparisons, clusters "
    "complaints into candidate idea cards, scores them by demand and unmet pain, and "
    "synthesizes differentiation angles. Be precise, cite evidence, and never invent "
    "facts that aren't in the input. "
) * 30


def main() -> None:
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "APFUN_ANTHROPIC_API_KEY is not set — required to make the real API call."
        )

    client = Anthropic(api_key=settings.anthropic_api_key, max_retries=0)

    system = [
        {
            "type": "text",
            "text": _LONG_PREAMBLE,
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": "Reply with the single word 'ok'."},
    ]

    # First call: writes the cache.
    client.messages.create(
        model="claude-opus-4-7",
        max_tokens=20,
        thinking={"type": "enabled", "budget_tokens": 2_000},
        system=system,  # type: ignore[arg-type]
        messages=[{"role": "user", "content": "first call"}],
    )

    # Second call: cache read should populate cache_read_input_tokens.
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=20,
        thinking={"type": "enabled", "budget_tokens": 2_000},
        system=system,  # type: ignore[arg-type]
        messages=[{"role": "user", "content": "second call"}],
    )

    # Guard against a misleading fixture: if the second call missed cache,
    # cache_read_input_tokens would be 0 and the tripwire test would lock in
    # a synthetic-shaped capture instead of a real cache-hit capture. Better
    # to fail loudly and retry than save the wrong shape.
    cache_read = getattr(msg.usage, "cache_read_input_tokens", None) or 0
    if cache_read <= 0:
        raise RuntimeError(
            f"second call did not hit cache (cache_read_input_tokens={cache_read}). "
            "Anthropic's cache may have evicted between calls; re-run."
        )

    _FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _FIXTURE_PATH.write_text(json.dumps(msg.model_dump(mode="json"), indent=2))
    print(f"wrote {_FIXTURE_PATH}")
    print(f"usage: {json.dumps(msg.usage.model_dump(mode='json'))}")


if __name__ == "__main__":
    main()
