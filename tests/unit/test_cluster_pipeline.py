"""Unit tests for `apfun.pipeline.cluster.cluster_signals`.

The LLMClient is stubbed; tests assert call counts, persistence shape,
dedup-to-rejected behavior, idempotency, cap behavior, JSONParseError retry,
and deterministic keyword-set bucketing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.llm.client import JSONParseError
from apfun.models import (
    Candidate,
    CandidateSignal,
    Decision,
    PipelineStage,
    RawSignal,
    SchedulerRun,
    SignalText,
    Source,
)
from apfun.pipeline import cluster as cluster_mod
from apfun.pipeline.cluster import (
    ClusterMergeOutput,
    ClusterOutput,
    IdeaCard,
    SignalCoreComplaint,
    _bucket_key,
    _slugify,
    cluster_signals,
)

# ─────────────────────────────── helpers ──────────────────────────────


def _make_source(session: Session, kind: str = "reddit", name: str = "r/SaaS") -> Source:
    s = Source(kind=kind, name=name, config_json={})
    session.add(s)
    session.flush()
    return s


def _make_signal(
    session: Session,
    source: Source,
    *,
    text: str,
    weight: float = 5.0,
    is_low_signal: bool = False,
    external_id: str | None = None,
    content_hash: str | None = None,
) -> tuple[RawSignal, SignalText]:
    raw = RawSignal(
        source_id=source.id,
        external_id=external_id or f"ext-{id(text)}",
        url="https://example.com",
        captured_at=datetime.now(UTC),
        content_hash=content_hash or f"h-{id(text)}",
        payload_json={"text": text},
    )
    session.add(raw)
    session.flush()
    st = SignalText(
        raw_signal_id=raw.id,
        source_kind=source.kind,
        text=text,
        social_proof_weight=weight,
        is_low_signal=is_low_signal,
        extracted_at=datetime.now(UTC),
    )
    session.add(st)
    session.flush()
    return raw, st


class _StubLLM:
    """Stub LLMClient with scriptable responses.

    `dedup_responses[i]` → SignalCoreComplaint for i-th `mechanic_json` call.
    `cluster_responses[i]` → ClusterOutput for i-th `judge_json` call.
    """

    def __init__(
        self,
        *,
        dedup_responses: list[SignalCoreComplaint] | None = None,
        cluster_responses: list[ClusterOutput] | None = None,
    ) -> None:
        self._dedup = list(dedup_responses or [])
        self._cluster = list(cluster_responses or [])
        self.mechanic_calls = 0
        self.judge_calls = 0
        self.last_judge_kwargs: dict[str, Any] = {}
        self.last_mechanic_kwargs: dict[str, Any] = {}

    def mechanic_json(
        self,
        task: str,
        system: str,  # noqa: ARG002 — captured to mirror real signature
        messages: list[dict[str, Any]],  # noqa: ARG002
        *,
        schema: type[Any],  # noqa: ARG002
        **kwargs: Any,
    ) -> Any:
        self.mechanic_calls += 1
        self.last_mechanic_kwargs = {"task": task, **kwargs}
        if not self._dedup:
            raise RuntimeError("stub: out of dedup responses")
        return self._dedup.pop(0)

    def judge_json(
        self,
        task: str,
        system: str,  # noqa: ARG002
        messages: list[dict[str, Any]],  # noqa: ARG002
        *,
        schema: type[Any],  # noqa: ARG002
        cache_ttl: str = "5m",
        **kwargs: Any,
    ) -> Any:
        self.judge_calls += 1
        self.last_judge_kwargs = {"task": task, "cache_ttl": cache_ttl, **kwargs}
        if not self._cluster:
            raise RuntimeError("stub: out of cluster responses")
        return self._cluster.pop(0)


# ──────────────────────── bucketing determinism ──────────────────────


def test_bucket_key_deterministic_across_input_order() -> None:
    a = _bucket_key("dev-tools", ["billing", "stripe", "proration"])
    b = _bucket_key("dev-tools", ["stripe", "proration", "billing"])
    assert a == b


def test_bucket_key_normalizes_case_and_whitespace() -> None:
    a = _bucket_key("Dev-Tools", ["Billing", " stripe ", "stripe"])
    b = _bucket_key("dev-tools", ["billing", "stripe"])
    assert a == b


def test_bucket_key_blank_vertical_becomes_unknown() -> None:
    key = _bucket_key("", ["x"])
    assert key[0] == "unknown"


# ───────────────────────────── slugify ───────────────────────────────


def test_slugify_is_url_safe_and_short() -> None:
    s = _slugify("Stripe billing — proration, dunning, & refunds are a NIGHTMARE!!!")
    assert " " not in s
    assert s.startswith("stripe-billing-proration-dunning-refunds")


def test_slugify_handles_empty_input() -> None:
    assert _slugify("") == "unspecified"


# ────────────────────── end-to-end happy path ────────────────────────


def test_cluster_signals_persists_candidate_and_links_signals(session: Session) -> None:
    src = _make_source(session)
    raw_a, st_a = _make_signal(session, src, text="stripe proration is a mess", weight=10)
    raw_b, st_b = _make_signal(session, src, text="dunning emails dont work", weight=4)
    session.commit()

    stub = _StubLLM(
        dedup_responses=[
            SignalCoreComplaint(
                core_complaint="Stripe proration is broken",
                vertical="billing",
                keywords=["stripe", "proration", "billing"],
            ),
            SignalCoreComplaint(
                core_complaint="Dunning emails fail",
                vertical="billing",
                keywords=["stripe", "billing", "proration"],
            ),
        ],
        cluster_responses=[
            ClusterOutput(
                clusters=[
                    IdeaCard(
                        problem_statement="Founders struggle with Stripe edge cases",
                        suspected_user="solo SaaS founders",
                        seed_keywords=["stripe", "proration", "dunning"],
                        contributing_signal_ids=[raw_a.id, raw_b.id],
                    )
                ]
            )
        ],
    )

    result = cluster_signals(session, llm_client=stub)  # type: ignore[arg-type]
    session.commit()

    assert stub.mechanic_calls == 2, "one Haiku call per signal"
    assert stub.judge_calls == 1, "one Opus call per bucket"
    assert stub.last_judge_kwargs["cache_ttl"] == "1h"
    assert result.processed_signals == 2
    assert result.buckets == 1
    assert result.candidates_inserted == 1
    assert result.signals_linked == 2

    cands = session.execute(select(Candidate)).scalars().all()
    assert len(cands) == 1
    cand = cands[0]
    assert cand.decision == Decision.PENDING
    assert cand.pipeline_stage == PipelineStage.NONE
    assert cand.dedup_key.startswith("founders-struggle-with-stripe")

    links = session.execute(select(CandidateSignal)).scalars().all()
    assert {link.raw_signal_id for link in links} == {raw_a.id, raw_b.id}


# ─────────────────── skip already-clustered + low-signal ─────────────


def test_idempotency_skips_already_clustered_signals(session: Session) -> None:
    src = _make_source(session)
    raw, _ = _make_signal(session, src, text="alpha", weight=1)
    session.commit()

    stub = _StubLLM(
        dedup_responses=[
            SignalCoreComplaint(core_complaint="x", vertical="v", keywords=["k"]),
        ],
        cluster_responses=[
            ClusterOutput(
                clusters=[
                    IdeaCard(
                        problem_statement="alpha problem",
                        seed_keywords=["k"],
                        contributing_signal_ids=[raw.id],
                    )
                ]
            )
        ],
    )

    first = cluster_signals(session, llm_client=stub)  # type: ignore[arg-type]
    session.commit()
    assert first.candidates_inserted == 1

    # Re-run with a fresh stub (no responses) — the signal should be skipped
    # entirely, so no LLM calls should be made.
    stub2 = _StubLLM()
    second = cluster_signals(session, llm_client=stub2)  # type: ignore[arg-type]
    session.commit()
    assert second.processed_signals == 0
    assert second.candidates_inserted == 0
    assert stub2.mechanic_calls == 0
    assert stub2.judge_calls == 0


def test_skips_is_low_signal_rows(session: Session) -> None:
    src = _make_source(session)
    _make_signal(session, src, text="alpha", is_low_signal=True)
    _make_signal(session, src, text="beta", is_low_signal=False, external_id="b", content_hash="b")
    session.commit()

    stub = _StubLLM(
        dedup_responses=[
            SignalCoreComplaint(core_complaint="x", vertical="v", keywords=["k"]),
        ],
        cluster_responses=[ClusterOutput(clusters=[])],
    )
    result = cluster_signals(session, llm_client=stub)  # type: ignore[arg-type]
    session.commit()

    # Only the non-low-signal row processed.
    assert result.processed_signals == 1
    assert stub.mechanic_calls == 1


# ───────────────────────── dedup to rejected ─────────────────────────


def test_dedup_key_match_links_to_rejected_without_flipping_decision(
    session: Session,
) -> None:
    """The HITL-durable convention: linking new signals to a rejected card
    does NOT change its decision. Per feedback 016 Q5."""
    src = _make_source(session)
    raw, _ = _make_signal(session, src, text="alpha signal")
    session.commit()

    # Seed a rejected candidate with the dedup_key our test cluster will produce.
    problem = "Founders need better billing tools"
    rejected = Candidate(
        problem_statement=problem,
        suspected_user="founders",
        seed_keywords_json=["billing"],
        vertical="billing",
        dedup_key=_slugify(problem),
        decision=Decision.REJECTED,
        pipeline_stage=PipelineStage.NONE,
    )
    session.add(rejected)
    session.flush()
    rejected_id = rejected.id
    session.commit()

    stub = _StubLLM(
        dedup_responses=[
            SignalCoreComplaint(core_complaint="x", vertical="billing", keywords=["billing"])
        ],
        cluster_responses=[
            ClusterOutput(
                clusters=[
                    IdeaCard(
                        problem_statement=problem,
                        seed_keywords=["billing"],
                        contributing_signal_ids=[raw.id],
                    )
                ]
            )
        ],
    )
    result = cluster_signals(session, llm_client=stub)  # type: ignore[arg-type]
    session.commit()

    assert result.candidates_inserted == 0, "should reuse the existing rejected candidate"
    assert result.signals_linked == 1

    refreshed = session.execute(select(Candidate).where(Candidate.id == rejected_id)).scalar_one()
    assert refreshed.decision == Decision.REJECTED, "HITL decision MUST stay rejected"

    links = (
        session.execute(select(CandidateSignal).where(CandidateSignal.candidate_id == rejected_id))
        .scalars()
        .all()
    )
    assert {link.raw_signal_id for link in links} == {raw.id}


# ───────────────────────────── caps ─────────────────────────────────


def test_cap_on_buckets_processes_largest_first_and_marks_capped(session: Session) -> None:
    src = _make_source(session)
    # Five signals → five distinct buckets (different keyword sets).
    raws: list[RawSignal] = []
    for i in range(5):
        r, _ = _make_signal(
            session,
            src,
            text=f"signal {i}",
            external_id=f"ext-{i}",
            content_hash=f"h-{i}",
        )
        raws.append(r)
    session.commit()

    stub = _StubLLM(
        dedup_responses=[
            SignalCoreComplaint(
                core_complaint=f"complaint {i}",
                vertical="v",
                keywords=[f"k{i}"],
            )
            for i in range(5)
        ],
        cluster_responses=[
            ClusterOutput(
                clusters=[
                    IdeaCard(
                        problem_statement=f"problem {i}",
                        seed_keywords=[f"k{i}"],
                        contributing_signal_ids=[raws[i].id],
                    )
                ]
            )
            for i in range(2)  # only 2 buckets allowed → 2 Opus responses
        ],
    )
    result = cluster_signals(
        session,
        llm_client=stub,  # type: ignore[arg-type]
        max_buckets=2,
    )
    session.commit()

    assert result.buckets == 2
    assert result.capped is True
    assert stub.judge_calls == 2
    # All 5 Haiku calls still happen (Haiku is the prepass, before bucketing).
    assert stub.mechanic_calls == 5


def test_cap_on_signals_truncates_input(session: Session) -> None:
    src = _make_source(session)
    for i in range(5):
        _make_signal(session, src, text=f"s{i}", external_id=f"e-{i}", content_hash=f"h-{i}")
    session.commit()

    stub = _StubLLM(
        dedup_responses=[
            SignalCoreComplaint(core_complaint=f"c{i}", vertical="v", keywords=[f"k{i}"])
            for i in range(3)
        ],
        cluster_responses=[ClusterOutput(clusters=[]) for _ in range(3)],
    )
    result = cluster_signals(session, llm_client=stub, max_signals=3)  # type: ignore[arg-type]
    session.commit()

    assert result.processed_signals == 3
    assert result.capped is True
    assert stub.mechanic_calls == 3


# ─────────────────── scheduler_runs + no-op behavior ─────────────────


def test_empty_input_is_a_clean_noop_with_scheduler_run_row(session: Session) -> None:
    stub = _StubLLM()
    result = cluster_signals(session, llm_client=stub)  # type: ignore[arg-type]
    session.commit()

    assert result.processed_signals == 0
    assert result.buckets == 0
    assert stub.mechanic_calls == 0
    assert stub.judge_calls == 0

    rows = (
        session.execute(select(SchedulerRun).where(SchedulerRun.job_id == "pipeline.cluster"))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].items_processed == 0


# ──────────────────── card-without-evidence handling ─────────────────


def test_card_with_hallucinated_signal_ids_is_dropped(session: Session) -> None:
    """If Opus emits a contributing_signal_ids list that doesn't match any
    input signal id, the card is logged and dropped — not persisted."""
    src = _make_source(session)
    raw, _ = _make_signal(session, src, text="alpha")
    session.commit()

    stub = _StubLLM(
        dedup_responses=[SignalCoreComplaint(core_complaint="x", vertical="v", keywords=["k"])],
        cluster_responses=[
            ClusterOutput(
                clusters=[
                    IdeaCard(
                        problem_statement="hallucinated",
                        seed_keywords=["k"],
                        contributing_signal_ids=[raw.id + 9999],  # invalid id
                    ),
                    IdeaCard(
                        problem_statement="valid card",
                        seed_keywords=["k"],
                        contributing_signal_ids=[raw.id],
                    ),
                ]
            )
        ],
    )
    result = cluster_signals(session, llm_client=stub)  # type: ignore[arg-type]
    session.commit()

    # Only the valid card persisted.
    assert result.candidates_inserted == 1
    cands = session.execute(select(Candidate)).scalars().all()
    assert len(cands) == 1
    assert cands[0].problem_statement == "valid card"


# ─────────────────────── cluster_merge sanity ────────────────────────


def test_cluster_merge_filters_invalid_canonical_ids() -> None:
    """_run_pass_2_merge drops merge_map entries whose canonical id wasn't
    among the pass-1 cluster ids (defensive against LLM hallucination)."""
    stub = _StubLLM(
        cluster_responses=[],
    )
    # We're not actually calling judge_json — call _run_pass_2_merge directly
    # with a stubbed result via monkey-patching.
    stub._cluster = []  # noqa: SLF001 — empty cluster_responses

    class _MockJudgeStub:
        def __init__(self, merge_map: dict[str, str]) -> None:
            self.merge_map = merge_map
            self.judge_calls = 0

        def judge_json(
            self, task: str, system: str, messages: list[Any], *, schema: Any, **kwargs: Any
        ) -> ClusterMergeOutput:
            self.judge_calls += 1
            return ClusterMergeOutput(merge_map=self.merge_map)

    pass1 = {
        "c1": IdeaCard(problem_statement="A"),
        "c2": IdeaCard(problem_statement="B"),
    }
    mock_client = _MockJudgeStub({"c1": "c1", "c2": "c1", "ghost": "c1"})
    cleaned = cluster_mod._run_pass_2_merge(mock_client, pass1)  # type: ignore[arg-type]
    assert "ghost" not in cleaned
    assert cleaned == {"c1": "c1", "c2": "c1"}


# ───────────────────── cache_ttl plumbing assertion ──────────────────


def test_judge_called_with_cache_ttl_1h(session: Session) -> None:
    """Stage 1 passes cache_ttl='1h' to judge_json (per feedback 016 Q2)."""
    src = _make_source(session)
    raw, _ = _make_signal(session, src, text="alpha")
    session.commit()

    stub = _StubLLM(
        dedup_responses=[SignalCoreComplaint(core_complaint="x", vertical="v", keywords=["k"])],
        cluster_responses=[
            ClusterOutput(
                clusters=[
                    IdeaCard(
                        problem_statement="p",
                        seed_keywords=["k"],
                        contributing_signal_ids=[raw.id],
                    )
                ]
            )
        ],
    )
    cluster_signals(session, llm_client=stub)  # type: ignore[arg-type]
    session.commit()
    assert stub.last_judge_kwargs.get("cache_ttl") == "1h"


# ───────────────────── JSONParseError integration ────────────────────


def test_jsonparseerror_class_truncates_raw_response() -> None:
    """JSONParseError caps raw_response at 2k chars — pin the contract."""
    long_body = "x" * 3000
    e = JSONParseError("bad", raw_response=long_body)
    assert len(e.raw_response) == 2000
