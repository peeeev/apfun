"""`/inbox` — HITL inbox endpoint listing candidates and accepting decisions.

Lists `decision='pending'` candidates ordered by composite weight (sum of
contributing signals' `social_proof_weight`), plus rejected candidates that
have accumulated new signals since rejection ("re-review?" surface).

Approve / reject mutations land via HTMX POST and write an `approvals` row.
Per orchestrator feedback 016 Q5: decisions are durable — the `decision`
field on a candidate stays whatever the operator last set. Re-review prompts
are surfaced via UI, not via auto-flipping the decision.

Per `docs/tasks/013-admin-ui-base.md` + the bundled task 014 implementation
agreed in feedback 018 (orchestrator routing).
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
    SignalText,
)

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _session() -> Iterator[Session]:
    """FastAPI dependency: yield a sync Session, close on exit."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _candidate_view(session: Session, candidate: Candidate) -> dict[str, Any]:
    """Project a Candidate into the dict the inbox templates render against.

    Joins contributing signals to compute the composite social-proof weight
    and the `signals_since_rejection` count (per feedback 016 Q5 — surfaced
    in the UI for rejected candidates that accumulate new evidence).
    """
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
    last_decided_at: datetime | None = None
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

    return {
        "id": candidate.id,
        "problem_statement": candidate.problem_statement,
        "suspected_user": candidate.suspected_user,
        "seed_keywords": candidate.seed_keywords_json,
        "vertical": candidate.vertical,
        "decision": candidate.decision.value,
        "pipeline_stage": candidate.pipeline_stage.value,
        "weight_total": weight_total,
        "signals_total": len(sig_rows),
        "signals_since_rejection": signals_since_rejection,
        "signals_preview": [
            {
                "text": r[0],
                "weight": float(r[1] or 0),
                "source_kind": r[2],
            }
            for r in sig_rows[:3]
        ],
    }


@router.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request, session: Annotated[Session, Depends(_session)]) -> HTMLResponse:
    """List pending candidates + rejected-with-new-signals reminders.

    Ordering: pending candidates first, ordered by composite signal weight
    descending. Then rejected candidates with `signals_since_rejection > 0`
    so operators see the re-review surface.
    """
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

    pending_views = [_candidate_view(session, c) for c in pending]
    pending_views.sort(key=lambda v: -v["weight_total"])

    rejected_views = [
        v
        for v in (_candidate_view(session, c) for c in rejected)
        if v["signals_since_rejection"] > 0
    ]

    return templates.TemplateResponse(
        request,
        "inbox.html",
        {
            "active": "inbox",
            "pending": pending_views,
            "rejected_with_new_signals": rejected_views,
        },
    )


def _decide(
    session: Session,
    candidate_id: int,
    decision: ApprovalDecision,
    comment: str | None,
) -> Candidate:
    candidate = session.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"candidate {candidate_id} not found")
    session.add(
        Approval(
            candidate_id=candidate_id,
            decision=decision,
            comment=comment,
            decided_at=datetime.now(UTC),
        )
    )
    candidate.decision = (
        Decision.APPROVED if decision == ApprovalDecision.APPROVE else Decision.REJECTED
    )
    session.commit()
    session.refresh(candidate)
    return candidate


@router.post("/inbox/{candidate_id}/approve", response_class=HTMLResponse)
def approve(
    request: Request,
    candidate_id: int,
    session: Annotated[Session, Depends(_session)],
    comment: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """Approve a candidate. HTMX swaps in the updated card.

    Writes one `approvals` row + flips `candidates.decision` to 'approved'.
    """
    candidate = _decide(session, candidate_id, ApprovalDecision.APPROVE, comment)
    view = _candidate_view(session, candidate)
    return templates.TemplateResponse(
        request, "_candidate_card.html", {"c": view, "show_actions": False}
    )


@router.post("/inbox/{candidate_id}/reject", response_class=HTMLResponse)
def reject(
    request: Request,
    candidate_id: int,
    session: Annotated[Session, Depends(_session)],
    comment: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """Reject a candidate. HTMX swaps in the updated card."""
    candidate = _decide(session, candidate_id, ApprovalDecision.REJECT, comment)
    view = _candidate_view(session, candidate)
    return templates.TemplateResponse(
        request, "_candidate_card.html", {"c": view, "show_actions": False}
    )
