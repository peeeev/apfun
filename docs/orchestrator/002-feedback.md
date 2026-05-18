# Feedback 002 — Task 001 scaffold review

**Date:** 2026-05-17
**Request:** 002-task001-scaffold-review.md
**Outcome:** Approved. Proceed to task 002 (DB foundations) after applying three checks.

## Highlights worth preserving as patterns

### Self-documenting guardrails

The host validator in `apfun/config.py` that rejects `127.0.0.1`/`localhost` with an error pointing back at CLAUDE.md is exactly the kind of self-documenting guardrail this project needs. **Pattern to repeat.** When the brief says "do X, not Y," encode the rule in code with a helpful error rather than trusting humans (or future Claude Code sessions) to remember.

### Plan-vs-code commit separation

Pre-task adjustments in a separate commit (e.g. `503689e`) before the scaffold commit (e.g. `2f76b51`) makes `git log` readable. **Keep this pattern**: distinct "plan changes" commits separated from "code changes" commits within the same PR when both are needed.

## Three required checks for task 002

### 1. SQLite pragmas must apply per-connection

WAL mode is persistent once set on a database file, but `synchronous=NORMAL`, `foreign_keys=ON`, and `busy_timeout` are per-connection runtime pragmas. Set them via a SQLAlchemy `connect` event listener so they fire on every new connection, not just at startup.

Pattern:

```python
from sqlalchemy import event

@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()
```

Verify with a test that opens multiple fresh connections and checks `PRAGMA foreign_keys;` returns 1 on each. The trap is "set once at startup," which passes a single-connection test and silently fails in production.

### 2. Both invocation forms must boot identically

Confirm both return `{"ok": true}` on `/healthz`:

- `python -m apfun.main` (dev convenience)
- `uvicorn apfun.main:app --host 0.0.0.0 --port 4000` (production form)

**Canonical for the container CMD: the explicit uvicorn form.** Exposes `--workers`, `--log-level`, `--reload` as flags without code edits. Note this in CLAUDE.md → Networking.

### 3. Add Makefile (or `uv run` aliases in pyproject.toml)

Targets at minimum:

- `fmt` — format with ruff
- `fmt-check` — verify formatting without changes
- `lint` — ruff check
- `typecheck` — pyright on apfun/
- `test` — pytest, unit only
- `test-all` — pytest including integration
- `serve` — uvicorn, canonical production form
- `serve-dev` — uvicorn with `--reload`
- `check` — the CI gate (fmt-check + lint + typecheck + test)

Saves typing repeated command chains and prevents bikeshedding in code review. Worth ~30 minutes before more tasks accumulate friction.

## Next step

After applying the three checks, proceed to task 002 (DB foundations: schema, models, the connect listener, migrations baseline).
