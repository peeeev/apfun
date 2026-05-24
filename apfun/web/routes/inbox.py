"""`/inbox` — HITL triage workspace: listing, detail view, decisions.

Listing (`/inbox`) shows `decision='pending'` candidates ordered by composite
weight (sum of contributing signals' `social_proof_weight`), plus rejected
candidates that accumulated new signals since rejection ("re-review?").
Status-filtered listings live at `/inbox/{approved,rejected,unsure}`. A detail
view at `/inbox/<id>` shows every contributing signal with its text + source
URL for cases the listing can't resolve.

Decisions (approve / reject / unsure) land via HTMX POST and write an
`approvals` row with the operator's free-text notes (reuses the existing
`comment` column). Per orchestrator feedback 016 Q5: decisions are durable —
new evidence never *auto*-flips a decision; the operator can always *explicitly*
re-decide any candidate (that's not an auto-flip). Per task 014 + 014-fix-1
(orchestrator request 028).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from apfun.db import SessionLocal
from apfun.models import (
    Approval,
    ApprovalDecision,
    Candidate,
    CandidateSignal,
    Decision,
    RawSignal,
    SignalText,
    Source,
)
from apfun.pipeline._source_identifier import source_identifier

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# ApprovalDecision (the operator action) → Decision (the candidate's status).
_APPROVAL_TO_DECISION: dict[ApprovalDecision, Decision] = {
    ApprovalDecision.APPROVE: Decision.APPROVED,
    ApprovalDecision.REJECT: Decision.REJECTED,
    ApprovalDecision.UNSURE: Decision.UNSURE,
}

# How many source badges to show inline before collapsing to "+N more".
_MAX_BADGES = 3

# buildability value → (badge label, css class). Per orchestrator request 030.
# A null buildability (unassessed) renders no badge — handled in the template.
_BUILDABILITY_BADGE: dict[str, tuple[str, str]] = {
    "high": ("Buildable", "tag-build-high"),
    "medium": ("Maybe", "tag-build-medium"),
    "low": ("Unlikely", "tag-build-low"),
    "non_software": ("Non-software", "tag-build-none"),
}


def _toggle_url(base: str, *, currently_hidden: bool) -> str:
    """URL that flips the `hide_non_software` filter (bookmarkable query param)."""
    return base if currently_hidden else f"{base}?hide_non_software=true"


def _hide_non_software(request: Request) -> bool:
    return request.query_params.get("hide_non_software") == "true"


def _session() -> Iterator[Session]:
    """FastAPI dependency: yield a sync Session, close on exit."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _rel(dt: datetime | None, *, now: datetime) -> str:
    """Compact relative-time string ("4h ago"). Detail-view captured_at."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    secs = int((now - dt).total_seconds())
    if secs < 0:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _source_badges(session: Session, candidate_id: int) -> tuple[list[str], int]:
    """Distinct source identifiers contributing to a candidate.

    Returns `(first_N_badges, overflow_count)`. Joins via Source for the
    authoritative kind + RawSignal for the payload the identifier is derived
    from. Order is by first appearance (stable for a given candidate).
    """
    rows = session.execute(
        select(Source.kind, RawSignal.payload_json)
        .join(RawSignal, RawSignal.source_id == Source.id)
        .join(CandidateSignal, CandidateSignal.raw_signal_id == RawSignal.id)
        .where(CandidateSignal.candidate_id == candidate_id)
        .order_by(CandidateSignal.raw_signal_id)
    ).all()
    seen: set[str] = set()
    badges: list[str] = []
    for kind, payload in rows:
        ident = source_identifier(kind, payload)
        if ident not in seen:
            seen.add(ident)
            badges.append(ident)
    overflow = max(len(badges) - _MAX_BADGES, 0)
    return badges[:_MAX_BADGES], overflow


def _candidate_view(session: Session, candidate: Candidate) -> dict[str, Any]:
    """Project a Candidate into the dict the inbox templates render against."""
    sig_rows = session.execute(
        select(
            SignalText.text,
            SignalText.social_proof_weight,
            SignalText.source_kind,
            CandidateSignal.created_at,
        )
        .join(CandidateSignal, CandidateSignal.raw_signal_id == SignalText.raw_signal_id)
        .where(CandidateSignal.candidate_id == candidate.id)
        .order_by(SignalText.social_proof_weight.desc())
    ).all()

    weight_total = float(sum(float(r[1] or 0) for r in sig_rows))

    signals_since_rejection = 0
    if candidate.decision == Decision.REJECTED:
        last_decided_at = session.execute(
            select(func.max(Approval.decided_at)).where(
                Approval.candidate_id == candidate.id,
                Approval.decision == ApprovalDecision.REJECT,
            )
        ).scalar()
        if last_decided_at is not None:
            signals_since_rejection = sum(
                1 for r in sig_rows if r[3] is not None and r[3] > last_decided_at
            )

    badges, badges_more = _source_badges(session, candidate.id)

    build_value = candidate.buildability.value if candidate.buildability is not None else None
    build_badge = _BUILDABILITY_BADGE.get(build_value) if build_value else None

    return {
        "id": candidate.id,
        "problem_statement": candidate.problem_statement,
        "suspected_user": candidate.suspected_user,
        "seed_keywords": candidate.seed_keywords_json,
        "vertical": candidate.vertical,
        "decision": candidate.decision.value,
        "pipeline_stage": candidate.pipeline_stage.value,
        "buildability": build_value,
        "buildability_label": build_badge[0] if build_badge else None,
        "buildability_css": build_badge[1] if build_badge else None,
        "buildability_rationale": candidate.buildability_rationale or "",
        "weight_total": weight_total,
        "signals_total": len(sig_rows),
        "signals_since_rejection": signals_since_rejection,
        "source_badges": badges,
        "source_badges_more": badges_more,
        "signals_preview": [
            {"text": r[0], "weight": float(r[1] or 0), "source_kind": r[2]} for r in sig_rows[:3]
        ],
    }


# ─────────────────────────────── listings ──────────────────────────────

# Status-filter listings beyond the default pending view. Each maps a URL
# suffix to the Decision it shows + an empty-state line.
_FILTERS: dict[str, tuple[Decision, str]] = {
    "approved": (Decision.APPROVED, "No approved candidates yet."),
    "rejected": (Decision.REJECTED, "No rejected candidates yet."),
    "unsure": (Decision.UNSURE, "No candidates marked unsure yet."),
}


@router.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request, session: Annotated[Session, Depends(_session)]) -> HTMLResponse:
    """Pending candidates (weight-desc) + rejected-with-new-signals reminders."""
    pending = (
        session.execute(
            select(Candidate).where(Candidate.decision == Decision.PENDING).order_by(Candidate.id)
        )
        .scalars()
        .all()
    )
    rejected = (
        session.execute(
            select(Candidate).where(Candidate.decision == Decision.REJECTED).order_by(Candidate.id)
        )
        .scalars()
        .all()
    )

    hide_ns = _hide_non_software(request)

    pending_views = [_candidate_view(session, c) for c in pending]
    pending_views.sort(key=lambda v: -v["weight_total"])
    rejected_views = [
        v
        for v in (_candidate_view(session, c) for c in rejected)
        if v["signals_since_rejection"] > 0
    ]
    if hide_ns:
        pending_views = [v for v in pending_views if v["buildability"] != "non_software"]
        rejected_views = [v for v in rejected_views if v["buildability"] != "non_software"]

    return templates.TemplateResponse(
        request,
        "inbox.html",
        {
            "active": "inbox",
            "filter": "pending",
            "pending": pending_views,
            "rejected_with_new_signals": rejected_views,
            "hide_non_software": hide_ns,
            "toggle_url": _toggle_url("/inbox", currently_hidden=hide_ns),
        },
    )


@router.get("/inbox/{candidate_id:int}", response_class=HTMLResponse)
def inbox_detail(
    request: Request, candidate_id: int, session: Annotated[Session, Depends(_session)]
) -> HTMLResponse:
    """Detail view: candidate header + every contributing signal with source URL.

    Declared BEFORE the string-param filter route below. The `:int` converter
    only matches integer segments, so `/inbox/5` lands here while
    `/inbox/approved` falls through to `inbox_filtered`. Order matters —
    Starlette matches first-declared-first.
    """
    candidate = session.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"candidate {candidate_id} not found")

    now = datetime.now(UTC)
    rows = session.execute(
        select(
            SignalText.text,
            SignalText.social_proof_weight,
            Source.kind,
            RawSignal.payload_json,
            RawSignal.url,
            RawSignal.captured_at,
        )
        .join(RawSignal, RawSignal.id == SignalText.raw_signal_id)
        .join(Source, Source.id == RawSignal.source_id)
        .join(CandidateSignal, CandidateSignal.raw_signal_id == SignalText.raw_signal_id)
        .where(CandidateSignal.candidate_id == candidate_id)
        .order_by(SignalText.social_proof_weight.desc())
    ).all()

    signals = [
        {
            "text": r[0],
            "weight": float(r[1] or 0),
            "source_identifier": source_identifier(r[2], r[3]),
            "url": r[4],
            "captured_rel": _rel(r[5], now=now),
        }
        for r in rows
    ]

    # Decision history (most-recent first): every approvals row for context.
    history = (
        session.execute(
            select(Approval)
            .where(Approval.candidate_id == candidate_id)
            .order_by(Approval.decided_at.desc())
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request,
        "inbox_detail.html",
        {
            "active": "inbox",
            "c": _candidate_view(session, candidate),
            "signals": signals,
            "history": [
                {
                    "decision": h.decision.value,
                    "comment": h.comment or "",
                    "decided_rel": _rel(h.decided_at, now=now),
                }
                for h in history
            ],
        },
    )


@router.get("/inbox/{status}", response_class=HTMLResponse)
def inbox_filtered(
    request: Request, status: str, session: Annotated[Session, Depends(_session)]
) -> HTMLResponse:
    """Status-filtered listing: /inbox/approved | /inbox/rejected | /inbox/unsure.

    Reached only for non-integer segments (integers match the detail route
    above). Unknown status → 404.
    """
    if status not in _FILTERS:
        raise HTTPException(status_code=404, detail=f"unknown inbox filter: {status}")
    decision, empty_msg = _FILTERS[status]
    hide_ns = _hide_non_software(request)
    candidates = (
        session.execute(
            select(Candidate).where(Candidate.decision == decision).order_by(Candidate.id.desc())
        )
        .scalars()
        .all()
    )
    views = [_candidate_view(session, c) for c in candidates]
    if hide_ns:
        views = [v for v in views if v["buildability"] != "non_software"]
    return templates.TemplateResponse(
        request,
        "inbox.html",
        {
            "active": "inbox",
            "filter": status,
            "filtered": views,
            "filtered_empty_msg": empty_msg,
            "hide_non_software": hide_ns,
            "toggle_url": _toggle_url(f"/inbox/{status}", currently_hidden=hide_ns),
        },
    )


# ─────────────────────────────── decisions ─────────────────────────────


def _decide(
    session: Session,
    candidate_id: int,
    decision: ApprovalDecision,
    comment: str | None,
) -> Candidate:
    """Write an approvals row + set the candidate's decision.

    Operator-initiated; works on any candidate regardless of current state
    (explicit re-decision is allowed — distinct from the auto-flip HITL
    durability forbids). `comment` is the operator's free-text notes.
    """
    candidate = session.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"candidate {candidate_id} not found")
    session.add(
        Approval(
            candidate_id=candidate_id,
            decision=decision,
            comment=(comment or None),
            decided_at=datetime.now(UTC),
        )
    )
    candidate.decision = _APPROVAL_TO_DECISION[decision]
    session.commit()
    session.refresh(candidate)
    return candidate


def _decision_response(request: Request, session: Session, candidate: Candidate) -> HTMLResponse:
    view = _candidate_view(session, candidate)
    return templates.TemplateResponse(
        request, "_candidate_card.html", {"c": view, "show_actions": False}
    )


@router.post("/inbox/{candidate_id:int}/approve", response_class=HTMLResponse)
def approve(
    request: Request,
    candidate_id: int,
    session: Annotated[Session, Depends(_session)],
    comment: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """Approve a candidate (+ optional notes). HTMX swaps in the updated card."""
    candidate = _decide(session, candidate_id, ApprovalDecision.APPROVE, comment)
    return _decision_response(request, session, candidate)


@router.post("/inbox/{candidate_id:int}/reject", response_class=HTMLResponse)
def reject(
    request: Request,
    candidate_id: int,
    session: Annotated[Session, Depends(_session)],
    comment: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """Reject a candidate (+ optional notes)."""
    candidate = _decide(session, candidate_id, ApprovalDecision.REJECT, comment)
    return _decision_response(request, session, candidate)


@router.post("/inbox/{candidate_id:int}/unsure", response_class=HTMLResponse)
def unsure(
    request: Request,
    candidate_id: int,
    session: Annotated[Session, Depends(_session)],
    comment: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """Mark a candidate unsure (+ optional notes) — looked, couldn't decide."""
    candidate = _decide(session, candidate_id, ApprovalDecision.UNSURE, comment)
    return _decision_response(request, session, candidate)
