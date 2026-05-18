# Request 001: gate-2 plan and stack

**Date:** 2026-05-17

**Context**: Just completed `project-brief.md` §13 bootstrap — CLAUDE.md drafted, 22 task files written under `docs/tasks/`, repo structure proposed, brief §5 stack defaults accepted. About to start task 001 code. Want direction-check before I burn cycles on choices that might be wrong.

**What I just did**: Wrote `CLAUDE.md`, `docs/tasks/000-overview.md`, and 001 → 022 individual task files. Each task has goal, deliverables, dependencies, acceptance criteria. Repo structure summarized in chat. Stack §5 accepted as-is: Python 3.11+, FastAPI, HTMX + Tailwind, SQLite, APScheduler in-process, async-first (FastAPI handlers, `anthropic.AsyncAnthropic`, `httpx.AsyncClient`, SQLAlchemy 2 + `aiosqlite`). Data model proposed in chat — encoded across tasks 002 and 003.

**What I would do next without intervention**: Start task 001 immediately — `uv init`, pyproject, ruff + pyright + pytest, FastAPI app on `0.0.0.0:4000` with `/healthz`. Then task 002 with async SQLAlchemy + aiosqlite per the stack I proposed. Walk down the list.

**Specific questions or risks**:
- Brief §5 explicitly invites pushback "with a strong case." Anything you'd flip? The async SQLAlchemy + APScheduler combination over SQLite is the one I'm least sure about — single-writer file plus an event loop plus concurrent scheduled jobs has a history of lock contention.
- `candidates.status` is currently one enum covering both HITL outcome (pending/approved/rejected/auto_killed) and machine progress (competitive/scoring/synthesizing/done/synthesis_failed/budget_blocked). The brief sketched it merged; I kept it merged. Reads busy in task 014 and 019.
- Task 022 weekly digest provider: I defaulted to Postmark. Brief §14 lists Mailgun / Postmark / SES as open. Resend isn't on the list but its free tier is generous.
- Task complexity is implicit from the body — no estimate per task. Worth a tag for planning?

**Relevant files/diffs**:
- `CLAUDE.md`
- `docs/tasks/000-overview.md`
- `docs/tasks/001-scaffold-fastapi.md` through `022-weekly-digest.md`
