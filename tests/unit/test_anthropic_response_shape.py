"""SDK shape tripwire + synthetic-fixture guard.

`apfun/llm/client.py` reads `msg.usage.{input_tokens, output_tokens,
cache_read_input_tokens, cache_creation_input_tokens}` through a `cast(Any, ...)`
because the SDK's `messages.create(**kwargs)` doesn't resolve cleanly under
pyright strict. If a future anthropic SDK renames or removes any of these
attributes, cost accounting would silently log zeros. The first test below
fails loudly on rename so we notice at CI rather than weeks into untracked
spend.

The second test guards against the synthetic fixture shipping indefinitely —
it fails until someone runs `scripts/capture_response_fixture.py` with a
real `APFUN_ANTHROPIC_API_KEY` and replaces the fixture with a captured
Opus 4.7 response.
"""

from __future__ import annotations

import json
from pathlib import Path

from anthropic.types import Message

_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "opus_4_7_with_cache.json"


def test_message_usage_has_four_token_attrs() -> None:
    fixture_dict = json.loads(_FIXTURE_PATH.read_text())
    # _meta_note is metadata about the fixture, not part of the SDK schema —
    # strip it before validation so a future SDK `extra="forbid"` switch
    # doesn't break this test.
    fixture_dict.pop("_meta_note", None)
    msg = Message.model_validate(fixture_dict)

    assert isinstance(msg.usage.input_tokens, int)
    assert isinstance(msg.usage.output_tokens, int)
    assert isinstance(msg.usage.cache_creation_input_tokens, int)
    assert isinstance(msg.usage.cache_read_input_tokens, int)

    assert msg.usage.input_tokens == 1234
    assert msg.usage.output_tokens == 56
    assert msg.usage.cache_creation_input_tokens == 7890
    assert msg.usage.cache_read_input_tokens == 12345


def test_fixture_is_real_capture() -> None:
    """Forcing function: fail until the synthetic fixture is replaced with a real capture.

    Per orchestrator feedback 007: a failing test is a clearer prompt than an
    xfail that becomes background noise. Resolution: run
    `scripts/capture_response_fixture.py` with `APFUN_ANTHROPIC_API_KEY` set,
    then commit the regenerated `tests/fixtures/opus_4_7_with_cache.json`.
    """
    fixture = json.loads(_FIXTURE_PATH.read_text())
    assert "_meta_note" not in fixture, (
        "Fixture is still synthetic. Run scripts/capture_response_fixture.py "
        "with APFUN_ANTHROPIC_API_KEY set to capture a real Opus 4.7 response "
        "(with cache hits) and replace tests/fixtures/opus_4_7_with_cache.json."
    )
