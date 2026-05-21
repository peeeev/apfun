"""Stage 0 — normalize `raw_signals` rows into uniform `signal_text` rows.

Idempotent ETL: re-running over the same `raw_signals` set is safe.

- Rows with no existing `signal_text` partner get inserted.
- Rows with an existing partner get **updated** (UPSERT semantics, enforced by
  the `UNIQUE(raw_signal_id)` constraint on `signal_text`).
- Sources whose `source.kind` has no registered extractor are skipped (logged
  at WARNING; counted as `skipped`).

Runs as an explicit ETL step — **not** as a database trigger or SQLAlchemy
event listener (per orchestrator feedback 015 Q1). That keeps the ingester
decoupled from clustering's data-shape needs and makes schema changes
tractable.

Writes one `scheduler_runs` row per invocation (`job_id="pipeline.normalize"`)
so this stage is observable from the same operator dashboard as the
ingesters.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import RawSignal, SchedulerRun, SignalText, Source
from apfun.pipeline._extractors import ExtractedText, get_extractor

logger = logging.getLogger(__name__)


@dataclass
class NormalizeResult:
    processed: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    latency_ms: int = 0


def normalize_raw_signals(
    session: Session,
    *,
    batch_size: int = 500,
    only_new: bool = False,
) -> NormalizeResult:
    """Project `raw_signals` rows into `signal_text`.

    `only_new=True` skips rows that already have a `signal_text` partner —
    fast incremental path for hot loops. The default (False) re-extracts and
    updates every row, which is the right shape after extractor logic
    changes (re-run all, get fresh weights everywhere).
    """
    started = time.monotonic()
    started_at = datetime.now(UTC)
    result = NormalizeResult()
    batch_error: str | None = None

    try:
        # Cache source.kind by source_id so we don't re-query per raw_signal.
        # 5 source kinds × handful of rows each fit in memory comfortably.
        kind_by_source: dict[int, str] = {
            row[0]: row[1] for row in session.execute(select(Source.id, Source.kind)).all()
        }

        # Cache existing signal_text raw_signal_ids when only_new=True.
        existing_raw_ids: set[int] = set()
        if only_new:
            existing_raw_ids = set(
                session.execute(select(SignalText.raw_signal_id)).scalars().all()
            )

        offset = 0
        while True:
            rows = (
                session.execute(
                    select(RawSignal).order_by(RawSignal.id).offset(offset).limit(batch_size)
                )
                .scalars()
                .all()
            )
            if not rows:
                break

            for raw in rows:
                result.processed += 1
                if only_new and raw.id in existing_raw_ids:
                    result.skipped += 1
                    continue

                source_kind = kind_by_source.get(raw.source_id)
                if source_kind is None:
                    logger.warning("normalize.unknown_source", extra={"raw_signal_id": raw.id})
                    result.skipped += 1
                    continue
                extractor = get_extractor(source_kind)
                if extractor is None:
                    logger.warning(
                        "normalize.no_extractor",
                        extra={"source_kind": source_kind, "raw_signal_id": raw.id},
                    )
                    result.skipped += 1
                    continue

                payload: dict[str, Any] = raw.payload_json or {}
                extracted = extractor(payload)
                _upsert_signal_text(
                    session,
                    raw_signal_id=raw.id,
                    source_kind=source_kind,
                    extracted=extracted,
                    counters=result,
                )

            session.commit()
            offset += len(rows)
            if len(rows) < batch_size:
                break

    except Exception as exc:  # noqa: BLE001 — capture for scheduler_runs row
        logger.exception("normalize.failed")
        batch_error = type(exc).__name__
        session.rollback()
        raise
    finally:
        finished_at = datetime.now(UTC)
        result.latency_ms = int((time.monotonic() - started) * 1000)
        session.add(
            SchedulerRun(
                job_id="pipeline.normalize",
                started_at=started_at,
                finished_at=finished_at,
                ok=batch_error is None,
                error=batch_error,
                items_processed=result.processed,
            )
        )
        session.commit()

    return result


def _upsert_signal_text(
    session: Session,
    *,
    raw_signal_id: int,
    source_kind: str,
    extracted: ExtractedText,
    counters: NormalizeResult,
) -> None:
    """Insert or update the `signal_text` row for `raw_signal_id`."""
    existing = session.execute(
        select(SignalText).where(SignalText.raw_signal_id == raw_signal_id)
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if existing is None:
        session.add(
            SignalText(
                raw_signal_id=raw_signal_id,
                source_kind=source_kind,
                text=extracted.text,
                social_proof_weight=extracted.social_proof_weight,
                is_low_signal=extracted.is_low_signal,
                extracted_at=now,
            )
        )
        counters.inserted += 1
        return
    existing.source_kind = source_kind
    existing.text = extracted.text
    existing.social_proof_weight = extracted.social_proof_weight
    existing.is_low_signal = extracted.is_low_signal
    existing.extracted_at = now
    counters.updated += 1


__all__ = [
    "NormalizeResult",
    "normalize_raw_signals",
]


# typing hint for pyright on the dict-cast inside the loop
_PayloadShape = dict[str, Any]
