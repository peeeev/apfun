"""Review-site miner: G2 / Capterra / Trustpilot.

The umbrella module per orchestrator feedback 014 Q2. Per-site adapters live
in sibling modules (`g2.py`, `capterra.py`, `trustpilot.py`); shared dedup,
content-hash, and the `ingest()` / `ingest_batch()` entrypoints live in
`_common.py`.

Source configuration discriminates the site via `source.config_json["site"]`;
`_common.ingest()` dispatches to the matching adapter. See
`docs/tasks/009-review-miner.md` for the full spec.
"""

from apfun.sourcing.review_sites._common import (
    CLOUDFLARE_BLOCK_MARKERS,
    ReviewDict,
    ingest,
    ingest_batch,
    review_content_hash,
)

__all__ = [
    "CLOUDFLARE_BLOCK_MARKERS",
    "ReviewDict",
    "ingest",
    "ingest_batch",
    "review_content_hash",
]
