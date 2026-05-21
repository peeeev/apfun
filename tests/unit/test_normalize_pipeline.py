"""End-to-end tests for `apfun.pipeline.normalize.normalize_raw_signals`.

Pins the idempotency contract from feedback 015 action item 7 explicitly:
re-running the normalizer over the same `raw_signals` set must NOT duplicate
rows, must update existing rows in place, and must be safe to interrupt
between batches.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import RawSignal, SchedulerRun, SignalText, Source
from apfun.pipeline.normalize import normalize_raw_signals


def _seed_reddit_source(session: Session) -> Source:
    src = Source(kind="reddit", name="r/SaaS", config_json={"subreddits": ["SaaS"]})
    session.add(src)
    session.flush()
    return src


def _add_raw_signal(
    session: Session,
    source: Source,
    *,
    title: str = "a title",
    selftext: str = "a body",
    score: int = 10,
    num_comments: int = 5,
    external_id: str = "t3_abc",
    content_hash: str = "hash-1",
    is_deleted: bool = False,
) -> RawSignal:
    payload = {
        "title": title,
        "selftext": "[deleted]" if is_deleted else selftext,
        "score": score,
        "num_comments": num_comments,
        "is_deleted": is_deleted,
    }
    if is_deleted:
        payload["deletion_marker"] = "[deleted]"
    signal = RawSignal(
        source_id=source.id,
        external_id=external_id,
        url=f"https://reddit.com/{external_id}",
        captured_at=datetime.now(UTC),
        content_hash=content_hash,
        payload_json=payload,
    )
    session.add(signal)
    session.flush()
    return signal


def test_empty_raw_signals_is_a_clean_noop(session: Session) -> None:
    result = normalize_raw_signals(session)
    assert result.processed == 0
    assert result.inserted == 0
    assert result.updated == 0
    rows = session.execute(select(SignalText)).scalars().all()
    assert rows == []
    # scheduler_runs row still written for observability
    scheduler_rows = (
        session.execute(select(SchedulerRun).where(SchedulerRun.job_id == "pipeline.normalize"))
        .scalars()
        .all()
    )
    assert len(scheduler_rows) == 1
    assert scheduler_rows[0].ok is True
    assert scheduler_rows[0].items_processed == 0


def test_normalizes_one_reddit_row_to_signal_text(session: Session) -> None:
    src = _seed_reddit_source(session)
    _add_raw_signal(session, src, title="What tool", selftext="Stripe is fine")
    session.commit()

    result = normalize_raw_signals(session)

    assert result.processed == 1
    assert result.inserted == 1
    assert result.updated == 0
    rows = session.execute(select(SignalText)).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.source_kind == "reddit"
    assert "What tool" in row.text
    assert "Stripe is fine" in row.text
    # score=10, num_comments=5 → 10 + 2*5 = 20
    assert row.social_proof_weight == 20.0
    assert row.is_low_signal is False


def test_idempotency_re_run_writes_zero_new_rows(session: Session) -> None:
    """The load-bearing test: re-running must update, not duplicate."""
    src = _seed_reddit_source(session)
    _add_raw_signal(session, src, title="Original title")
    session.commit()

    first = normalize_raw_signals(session)
    assert first.inserted == 1
    assert first.updated == 0
    first_extracted_at = session.execute(select(SignalText)).scalar_one().extracted_at

    # Second run over the same raw_signals — no schema or payload change.
    second = normalize_raw_signals(session)
    assert second.processed == 1
    assert second.inserted == 0  # zero new rows on re-run
    assert second.updated == 1
    rows = session.execute(select(SignalText)).scalars().all()
    assert len(rows) == 1, "UNIQUE(raw_signal_id) constraint must prevent duplicates"

    # extracted_at advanced on the update.
    assert rows[0].extracted_at > first_extracted_at


def test_payload_change_updates_text_and_weight(session: Session) -> None:
    """If a raw_signal payload changes (rare; would mean an ingester edited
    it), a re-run picks up the new content."""
    src = _seed_reddit_source(session)
    raw = _add_raw_signal(
        session, src, title="Original", selftext="old body", score=5, num_comments=1
    )
    session.commit()

    normalize_raw_signals(session)
    row_before = session.execute(select(SignalText)).scalar_one()
    assert "Original" in row_before.text
    assert row_before.social_proof_weight == 5.0 + 2.0  # 7

    # Mutate raw payload (reassign whole dict per CLAUDE.md JSON-column rule).
    raw.payload_json = {**raw.payload_json, "score": 100, "num_comments": 50}
    session.commit()

    second = normalize_raw_signals(session)
    assert second.updated == 1
    row_after = session.execute(select(SignalText)).scalar_one()
    assert row_after.id == row_before.id  # same row, updated in place
    assert row_after.social_proof_weight == 100.0 + 2 * 50.0  # 200


def test_deleted_reddit_row_flagged_as_low_signal(session: Session) -> None:
    src = _seed_reddit_source(session)
    _add_raw_signal(
        session,
        src,
        title="Cancelling — burned out",
        is_deleted=True,
    )
    session.commit()

    normalize_raw_signals(session)
    row = session.execute(select(SignalText)).scalar_one()
    assert row.is_low_signal is True
    assert "Cancelling" in row.text  # title preserved
    assert "[deleted]" not in row.text  # body markers stripped


def test_unknown_source_kind_skips_with_warning(session: Session) -> None:
    """A source with kind 'yelp' (no extractor registered) is skipped, not crashed."""
    src = Source(kind="yelp", name="yelp:something", config_json={})
    session.add(src)
    session.flush()
    _add_raw_signal(session, src, external_id="yelp-1", content_hash="yelp-hash-1")
    session.commit()

    result = normalize_raw_signals(session)
    assert result.processed == 1
    assert result.inserted == 0
    assert result.skipped == 1
    assert session.execute(select(SignalText)).scalars().all() == []


def test_only_new_skips_existing_rows(session: Session) -> None:
    """only_new=True is the fast incremental path — existing rows aren't touched."""
    src = _seed_reddit_source(session)
    _add_raw_signal(session, src, title="First", external_id="t3_first", content_hash="h-first")
    session.commit()
    normalize_raw_signals(session)
    first_row = session.execute(select(SignalText)).scalar_one()
    first_extracted_at = first_row.extracted_at

    # Add a second raw_signal.
    _add_raw_signal(
        session,
        src,
        title="Second",
        external_id="t3_second",
        content_hash="h-second",
    )
    session.commit()

    result = normalize_raw_signals(session, only_new=True)
    assert result.processed == 2
    assert result.inserted == 1
    assert result.updated == 0
    assert result.skipped == 1  # the existing row

    # The first row's extracted_at should NOT have advanced.
    refreshed = session.execute(
        select(SignalText).where(SignalText.id == first_row.id)
    ).scalar_one()
    assert refreshed.extracted_at == first_extracted_at


def test_batch_processing_handles_more_than_batch_size_rows(session: Session) -> None:
    """Cover the multi-batch loop with batch_size smaller than the row count."""
    src = _seed_reddit_source(session)
    for i in range(7):
        _add_raw_signal(
            session,
            src,
            title=f"Row {i}",
            external_id=f"t3_{i}",
            content_hash=f"h-{i}",
        )
    session.commit()

    result = normalize_raw_signals(session, batch_size=3)
    assert result.processed == 7
    assert result.inserted == 7
    rows = session.execute(select(SignalText)).scalars().all()
    assert len(rows) == 7


def test_normalize_writes_scheduler_run_row(session: Session) -> None:
    src = _seed_reddit_source(session)
    _add_raw_signal(session, src)
    session.commit()

    normalize_raw_signals(session)

    rows = (
        session.execute(select(SchedulerRun).where(SchedulerRun.job_id == "pipeline.normalize"))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].items_processed == 1


def test_normalize_across_multiple_source_kinds(session: Session) -> None:
    """End-to-end: every source kind that has an extractor produces a signal_text row."""
    sources_payloads: list[tuple[Source, dict, str]] = []

    reddit_src = Source(kind="reddit", name="r/SaaS", config_json={})
    session.add(reddit_src)
    session.flush()
    sources_payloads.append(
        (
            reddit_src,
            {"title": "reddit post", "selftext": "body", "score": 1, "num_comments": 0},
            "h-r",
        )
    )

    hn_src = Source(kind="hn", name="hn:ask", config_json={})
    session.add(hn_src)
    session.flush()
    sources_payloads.append(
        (
            hn_src,
            {"title": "Ask HN: tool", "story_text": "details", "points": 5, "_tags": ["story"]},
            "h-h",
        )
    )

    ph_src = Source(kind="producthunt", name="ph:topic", config_json={})
    session.add(ph_src)
    session.flush()
    sources_payloads.append(
        (
            ph_src,
            {"name": "Thing", "tagline": "Tagline", "description": "Desc", "votesCount": 10},
            "h-p",
        )
    )

    ih_src = Source(kind="indiehackers", name="ih:main", config_json={})
    session.add(ih_src)
    session.flush()
    sources_payloads.append(
        (
            ih_src,
            {"title": "IH post", "rawBody": "body", "upvoteCount": 3, "replyCount": 1},
            "h-i",
        )
    )

    rs_src = Source(kind="review_sites", name="g2:asana", config_json={})
    session.add(rs_src)
    session.flush()
    sources_payloads.append(
        (
            rs_src,
            {
                "site": "g2",
                "product_slug": "asana",
                "product_name": "Asana",
                "title": "Onboarding",
                "body": "rough",
                "rating": 2,
                "helpful_count": 4,
            },
            "h-rs",
        )
    )

    for src, payload, h in sources_payloads:
        session.add(
            RawSignal(
                source_id=src.id,
                external_id=h,
                captured_at=datetime.now(UTC),
                content_hash=h,
                payload_json=payload,
            )
        )
    session.commit()

    result = normalize_raw_signals(session)
    assert result.processed == 5
    assert result.inserted == 5
    rows = session.execute(select(SignalText)).scalars().all()
    kinds = {r.source_kind for r in rows}
    assert kinds == {"reddit", "hn", "producthunt", "indiehackers", "review_sites"}


@pytest.mark.parametrize(
    "score, num_comments, expected_weight",
    [
        (10, 0, 10.0),
        (10, 5, 20.0),
        (-5, 3, 6.0),  # negative score floors
        (0, 0, 0.0),
    ],
)
def test_reddit_weight_formula(
    session: Session, score: int, num_comments: int, expected_weight: float
) -> None:
    src = _seed_reddit_source(session)
    _add_raw_signal(
        session,
        src,
        score=score,
        num_comments=num_comments,
        external_id=f"t3_{score}_{num_comments}",
        content_hash=f"h-{score}-{num_comments}",
    )
    session.commit()
    normalize_raw_signals(session)
    row = session.execute(select(SignalText)).scalar_one()
    assert row.social_proof_weight == expected_weight
