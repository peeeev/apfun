"""SDK shape tripwire — locks in the four token-counting attributes on `Usage`.

`apfun/llm/client.py` reads `msg.usage.{input_tokens, output_tokens,
cache_read_input_tokens, cache_creation_input_tokens}` through a `cast(Any, ...)`
because the SDK's `messages.create(**kwargs)` doesn't resolve cleanly under
pyright strict. If a future anthropic SDK renames or removes any of these
attributes, cost accounting would silently log zeros. This test fails loudly
on rename so we notice at CI rather than weeks into untracked spend.

Fixture is a synthetic Message JSON (handcrafted to match the SDK's documented
response shape). Replace with a real captured response when convenient.
"""

from __future__ import annotations

import json
from pathlib import Path

from anthropic.types import Message

_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "opus_4_7_with_cache.json"


def test_message_usage_has_four_token_attrs() -> None:
    data = json.loads(_FIXTURE_PATH.read_text())
    msg = Message.model_validate(data)

    assert isinstance(msg.usage.input_tokens, int)
    assert isinstance(msg.usage.output_tokens, int)
    assert isinstance(msg.usage.cache_creation_input_tokens, int)
    assert isinstance(msg.usage.cache_read_input_tokens, int)

    assert msg.usage.input_tokens == 1234
    assert msg.usage.output_tokens == 56
    assert msg.usage.cache_creation_input_tokens == 7890
    assert msg.usage.cache_read_input_tokens == 12345
