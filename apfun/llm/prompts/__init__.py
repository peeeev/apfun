"""Jinja templates for LLM stages.

Templates are loaded via `apfun.llm.prompts.render(name, **vars)` which uses a
filesystem-rooted Jinja `Environment` against this directory. Keeping templates
out of Python strings makes them legible to non-coders and reviewable as text.

Per CLAUDE.md → File layout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_TEMPLATES_DIR = Path(__file__).resolve().parent

# StrictUndefined: referencing an undefined variable in a template raises
# rather than rendering silently to "". Prompt bugs should fail loudly.
_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    undefined=StrictUndefined,
    autoescape=False,  # LLM prompts aren't HTML; escaping breaks JSON examples
    trim_blocks=True,
    lstrip_blocks=True,
)


def render(name: str, **variables: Any) -> str:
    """Render a Jinja template by name (`cluster.j2`, `cluster_merge.j2`, ...)."""
    template = _ENV.get_template(name)
    return template.render(**variables)


__all__ = ["render"]
