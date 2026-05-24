# Request 025: task 023-fix-1 — `/ops` scheduler restart button

**Date:** 2026-05-23

**Context.** `/ops` shipped (task 023, PR merged) with read-only visibility. The next natural friction point: when the dashboard surfaces a STALE scheduler job (see the `pipeline.cluster` issue we found during runbook 003), the operator currently has to SSH in to fix it. The `/ops` page already shows the symptom; it should also offer the remedy for the most common operator action — restart the scheduler subsystem without restarting the container.

**Goal.** Add a "Restart scheduler" button to `/ops` that tears down the running APScheduler instance and starts a fresh one, re-reading all jobs from the jobstore. Logged, confirmed, idempotent. No container restart, no credential refresh, no SSH session needed.

## Scope

**In scope:**

- New POST endpoint at `/ops/scheduler/restart` (HTMX-driven, basic-auth already covered by Apache vhost).
- A "Restart scheduler" button in the `/ops` Scheduler section, with inline confirmation (`hx-confirm="..."` or equivalent two-click pattern).
- Implementation: call `scheduler.shutdown(wait=False)` then `scheduler.start()` (or recreate the BackgroundScheduler instance, depending on how it's accessed from the request handler). Jobs persist in the SQLAlchemyJobStore; restart re-reads them.
- Log every manual restart to `scheduler_runs` with `job_id="ops.manual_restart"`, `ok=True/False` per outcome, error message on failure.
- HTMX response is a refreshed `/ops` body fragment showing the new scheduler state (post-restart next_run_times, no STALE warnings if successful).
- A simple debounce: if a restart is in progress, the button is disabled and a "Restart in progress…" indicator shows. Prevents double-fires from accidental double-clicks.

**Out of scope:**

- Other operator actions (force-fire individual jobs, disable sources, reset consecutive_failures). These are future additions; this PR is the one-button minimum.
- Restarting uvicorn or the container. This is *scheduler restart only* — uvicorn keeps running, the FastAPI app stays up, in-flight HTTP handlers are unaffected.
- Fixing the underlying STALE-`next_run_time` bug. A restart should resolve it for now (re-reading the jobstore on startup re-evaluates schedules); the root-cause fix is a separate orchestrator turn if STALE recurs post-restart.
- CSRF protection. Apache basic-auth + same-origin is sufficient for a one-user dashboard. If apfun ever opens up to multiple users, this becomes a real concern.

## Why this is the right shape (vs. alternatives)

The friction problem has three possible solutions; only one is right at this scale.

| Approach | Pros | Cons | Verdict |
|---|---|---|---|
| Touch a sentinel `.py` file to trigger uvicorn `--reload` | Zero new code | Restarts the entire FastAPI process; aborts in-flight HTTP; relies on filesystem watcher in production-mode container | **No** — too blunt |
| Add `/ops/uvicorn/restart` that calls `os.execvp` to replace the process | Reliable; full reset | Worker dies; HTMX response never delivered; user sees a timeout/error that's actually success | **No** — bad UX |
| Add `/ops/scheduler/restart` that shuts down + restarts the APScheduler instance | Targeted; in-flight HTTP unaffected; clean HTMX response; loggable | More code; needs to access scheduler instance from request handler | **Yes** |

The third option is the cleanest because the scheduler is a clearly-scoped subsystem with a published `shutdown()` + `start()` lifecycle. APScheduler's `BackgroundScheduler` explicitly supports this pattern.

## Implementation shape

Suggested approach; deviate if cleaner:

```python
# apfun/web/routes/ops.py (or wherever /ops lives)

@router.post("/ops/scheduler/restart", response_class=HTMLResponse)
def restart_scheduler(request: Request) -> Response:
    scheduler = request.app.state.scheduler  # or wherever it's accessible
    started = datetime.now(UTC)
    error_msg = None
    try:
        scheduler.shutdown(wait=False)
        # Recreate or restart. APScheduler supports calling .start() again on
        # the same instance after .shutdown(), but verify against this codebase's
        # patterns — task 012 may have wired it differently.
        scheduler.start()
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception("manual scheduler restart failed")

    with SessionLocal() as s:
        s.add(SchedulerRun(
            job_id="ops.manual_restart",
            started_at=started,
            finished_at=datetime.now(UTC),
            ok=(error_msg is None),
            error=error_msg,
            items_processed=None,
        ))
        s.commit()

    # Return the refreshed /ops body fragment (same partial used by the
    # 30s auto-refresh) so the user immediately sees the new state.
    return render_ops_body(request)
```

For the button itself in `templates/ops.html`:

```html
<button
    hx-post="/ops/scheduler/restart"
    hx-target="#ops-body"
    hx-swap="outerHTML"
    hx-confirm="Restart the scheduler? In-flight jobs will be aborted."
    hx-disabled-elt="this"
    class="...existing button styles..."
>
    Restart scheduler
</button>
```

`hx-disabled-elt="this"` provides the simplest possible debounce — the button disables itself while the request is in flight. No additional state machine needed.

## Tests

- Unit: POST to `/ops/scheduler/restart` calls `scheduler.shutdown` + `scheduler.start` (mock the scheduler).
- Unit: a `scheduler_runs` row with `job_id="ops.manual_restart"` appears post-call.
- Unit: if `shutdown()` raises, the row's `ok=False` with the error message; no exception propagates to the user.
- Unit: HTMX response is the refreshed `/ops` body fragment (verify the same template renders).
- Optional integration: full end-to-end restart against a real APScheduler — but this is hard to test reliably and probably skip-worthy. The unit tests cover the integration points sufficiently.

## Documentation updates (same PR)

1. **`docs/operator/SETUP.md`** (or wherever) — short note: "The /ops dashboard now includes a 'Restart scheduler' button for the case where a scheduled job appears STALE. Restart logs to `scheduler_runs` with `job_id='ops.manual_restart'`."

2. **`docs/tasks/NNN-ops-dashboard.md`** (whatever number task 023 ended up with) — Notes addition documenting the new endpoint and the design decision to keep mutations narrow ("scheduler restart only; broader actions are future additions").

3. **`docs/orchestrator/INDEX.md`** — row 025 → answered after PR merges.

No CLAUDE.md changes needed. The existing read-only-by-default expectation for `/ops` is being relaxed *minimally* and intentionally; the convention doesn't need restatement.

## What I would do next without intervention

1. Branch `feature/ops-restart-scheduler`.
2. Verify how the scheduler instance is exposed to request handlers (likely `app.state.scheduler` per the lifespan pattern from task 012). Adjust the shape if different.
3. Add the POST endpoint + button per the sketch above.
4. Add tests per the Tests section.
5. Apply doc updates.
6. Verify `grep -r '# TODO verify' apfun/web/` returns zero.
7. Open PR. Verify in browser:
   - Click the button → confirmation appears
   - Confirm → restart fires → `/ops` body refreshes
   - No STALE warnings if scheduler was healthy
   - One new row in scheduler_runs with `job_id="ops.manual_restart"`

## Specific questions or risks

1. **Where is the scheduler instance exposed?** If task 012 used `app.state.scheduler`, fine. If it used a module-global singleton, the endpoint accesses it directly. Either's fine — implementer adapts to the actual code shape.

2. **Does `scheduler.start()` after `scheduler.shutdown()` work cleanly?** APScheduler's docs say yes — the same instance can be shut down and restarted. But if for any reason it doesn't (e.g., the SQLAlchemyJobStore connection gets into a weird state), the fallback is to construct a new `BackgroundScheduler` instance with the same config and re-register jobs from `apfun/scheduler/jobs.py`. Implementer's call based on what works.

3. **Should the restart also catch up past-time jobs?** APScheduler has `misfire_grace_time` for this. If a job's `next_run_time` was 3h in the past and `misfire_grace_time` allows it, the job fires immediately on restart. This might be what fixes the STALE bug we've been seeing. Worth checking the current `misfire_grace_time` config in task 012's scheduler setup; if it's too short or unset, the STALE bug will recur. **Don't fix this in this PR** — surface in the PR description as a flag for a follow-up turn if STALE recurs.

4. **Edge case: scheduler is already stopped when restart is clicked.** Calling `.shutdown()` on an already-stopped scheduler raises. The endpoint should catch this and proceed to `.start()` — net effect "scheduler is now running" is what the user wanted. Log it but don't fail.

5. **Two operators clicking simultaneously.** Apache basic-auth means there's effectively one operator at a time, but technically possible. `hx-disabled-elt="this"` only prevents the same-tab double-click; cross-tab is fine because the second restart on a freshly-restarted scheduler is a no-op (well-defined). Acceptable.

## Relevant files

Code under change:
- `apfun/web/routes/ops.py` — new POST endpoint
- `apfun/web/templates/ops.html` — restart button + section structure
- `tests/unit/test_ops_route.py` — restart endpoint tests

Docs under change:
- `docs/operator/SETUP.md` — button mention
- `docs/tasks/NNN-ops-dashboard.md` — Notes update
- `docs/orchestrator/INDEX.md` — row 025 → answered

## Meta note — /ops is now mutating, deliberately

Request 023 specced `/ops` as read-only. That was right at the time — we didn't know which actions would compound from operator experience.

Now we know one: scheduler restart. The same design pattern applies to future operator actions (force-fire jobs, disable sources, reset counters, etc.) when they earn their place via observed friction. The principle going forward:

- Each `/ops` mutation is **explicit** (button + confirmation, not implicit on view)
- Each is **logged** (writes to `scheduler_runs` or equivalent for audit trail)
- Each is **idempotent or near-enough** (re-firing shouldn't break things)
- Each is **minimal-scope** (restart scheduler, not "restart everything")

Don't preemptively build other mutations. Add them when friction points surface, the same way this one did.
