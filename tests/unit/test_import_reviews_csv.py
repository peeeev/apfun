"""Unit tests for `scripts/import_reviews.py` — CSV fallback path.

Imports the sample CSV fixture, then asserts row count + source-row creation +
dedup-on-reimport. The CSV path shares its dedup key (`review_content_hash`)
with the scraping path, so re-importing the same CSV writes zero new rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from apfun.models import RawSignal, Source

_FIXTURE_CSV = Path(__file__).parents[1] / "fixtures" / "review_sites" / "sample_reviews.csv"


@pytest.fixture
def patched_session_local(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> sessionmaker:
    """Make `scripts.import_reviews.SessionLocal` point at the test engine."""
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    class _SessionLocalShim:
        def __call__(self) -> Any:
            return factory()

    monkeypatch.setattr("scripts.import_reviews.SessionLocal", _SessionLocalShim())
    return factory


def test_csv_import_inserts_three_reviews(
    patched_session_local: sessionmaker, engine: Engine
) -> None:
    from scripts.import_reviews import import_csv

    inserted, skipped = import_csv(_FIXTURE_CSV)
    assert inserted == 3
    assert skipped == 0

    with Session(engine) as s:
        rows = s.execute(select(RawSignal)).scalars().all()
        assert len(rows) == 3
        # Each review payload tagged with `_apfun_origin=manual_csv` via the source.
        sources = s.execute(select(Source).where(Source.kind == "review_sites")).scalars().all()
        # Two sources expected: g2:zendesk-manual and capterra:zendesk-manual.
        names = {src.name for src in sources}
        assert "g2:zendesk-manual" in names
        assert "capterra:zendesk-manual" in names


def test_csv_reimport_dedups(patched_session_local: sessionmaker, engine: Engine) -> None:
    from scripts.import_reviews import import_csv

    inserted_a, _ = import_csv(_FIXTURE_CSV)
    inserted_b, skipped_b = import_csv(_FIXTURE_CSV)
    assert inserted_a == 3
    assert inserted_b == 0
    assert skipped_b == 3
    with Session(engine) as s:
        rows = s.execute(select(RawSignal)).scalars().all()
        assert len(rows) == 3


def test_csv_with_missing_required_column_raises(
    patched_session_local: sessionmaker, tmp_path: Path
) -> None:
    from scripts.import_reviews import import_csv

    bad = tmp_path / "bad.csv"
    bad.write_text("site,product_slug,product_name,body\ng2,x,X,hello\n")
    # Missing 'rating' (required) — should fail with ValueError mentioning it.
    with pytest.raises(ValueError, match="rating"):
        import_csv(bad)


def test_csv_skips_rows_with_empty_body(
    patched_session_local: sessionmaker, engine: Engine, tmp_path: Path
) -> None:
    from scripts.import_reviews import import_csv

    csv_path = tmp_path / "with_empty.csv"
    csv_path.write_text(
        "site,product_slug,product_name,review_id,title,body,rating,author,posted_at,helpful_count,permalink\n"
        "g2,zendesk,Zendesk,csv-empty-1,Title,,2,Author,2026-05-01T00:00:00Z,1,\n"
        "g2,zendesk,Zendesk,csv-good-1,Title,Body present,3,Author,2026-05-01T00:00:00Z,1,\n"
    )
    inserted, skipped = import_csv(csv_path)
    assert inserted == 1
    assert skipped == 1
