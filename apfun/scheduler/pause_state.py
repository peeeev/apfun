"""Cross-restart persistence of the operator's scheduler pause (task 014-fix-2).

APScheduler's `pause()` only flips in-memory `scheduler.state`; it never touches
the SQLAlchemyJobStore. So a container restart (fresh `start_scheduler()`) would
silently resume firing jobs even though the operator paused. These helpers
persist the intent in the `runtime_state` table; the lifespan handler re-applies
`pause()` on startup when the flag is set. Per orchestrator request 031 §1.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from apfun.models import SCHEDULER_PAUSED_KEY, RuntimeState

_TRUE = "true"


def set_scheduler_paused(session: Session, paused: bool) -> None:
    """Persist (or clear) the paused flag. Commits."""
    row = session.get(RuntimeState, SCHEDULER_PAUSED_KEY)
    if paused:
        if row is None:
            session.add(RuntimeState(key=SCHEDULER_PAUSED_KEY, value=_TRUE))
        else:
            row.value = _TRUE
    elif row is not None:
        session.delete(row)
    session.commit()


def is_scheduler_paused(session: Session) -> bool:
    """True if the operator paused the scheduler and hasn't resumed."""
    row = session.get(RuntimeState, SCHEDULER_PAUSED_KEY)
    return row is not None and row.value == _TRUE
