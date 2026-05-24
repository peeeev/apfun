# Feedback 026 — first real triage session

**Date:** 2026-05-23
**Outcome:** Routing decided for two parallel tasks (027 null-rate diagnosis + 028 inbox UX). One personal filter noted but not built. One convention update bundled into 028.

## On the observations

The triage session produced exactly the data we needed — the orchestrator pattern is working as designed. A few patterns worth recording before getting to action items:

### The 81% null rate is the most important finding

Request 024 set the escalation threshold at 50%. You're at 81%. This isn't "Stage 1 is broken" but it might be "Stage 1's null-threshold is calibrated wrong for Reddit content." Diagnosis required before any prompt change.

Two possible explanations:

- **Reddit content really is 81% non-complaint.** Lots of success stories, "I just launched X" announcements, generic help requests, off-topic chatter. Haiku is judging accurately.
- **Haiku's "complaint" bar is too high.** It's missing *implicit* complaints — "What's the best way to do X?" is structurally a question but semantically a complaint about no existing solution. Same for "Looking for tools that..." and similar phrasings.

These are testable. The runbook (see request 027) will surface which one is dominating.

### The payment-processing preference

This is a personal filter, not a code change. Reject payment-processing candidates manually for now. If they keep recurring in your queue and the manual-reject overhead becomes meaningful, that's evidence to revisit. Possible future shapes:

- Add a "deprioritize payment/billing infrastructure" clause to Stage 1 prompts
- Build a small "operator blocklist" feature (categories or keywords the operator wants to skip)

But that's N-many-rejections future, not N-one now. Note in your triage notes, reject when seen.

### Inbox UX improvements bundle naturally

Source visibility, detail view, "Unsure" state, notes field, source URLs — these are all symptoms of the same root problem: *the inbox doesn't surface enough information to make confident decisions*. One PR addresses all of them coherently. Don't fragment into five small PRs.

### CLAUDE.md hallucination convention

Small enough to bundle with the inbox UX PR. Worth landing while it's fresh.

## Parallel work

Two task specs handed to Claude Code:

1. **Request 027 — Runbook 004: Stage 1 null-rate diagnosis.** Empirical. Claude Code writes the runbook + supporting script; you (operator) run it, capture artifacts, send back. Probably half a day of Claude Code time + 30 min of operator time. Output drives a follow-up orchestrator turn that decides whether to tune the dedup prompt, accept the high null rate, or some hybrid.

2. **Request 028 — Task 014-fix-1: inbox UX improvements bundle.** Implementation. Source visibility in listing, detail view at /inbox/&lt;id&gt;, "Unsure" decision state, notes field per decision, source URL display. Plus CLAUDE.md hallucination convention. M-complexity; one PR. Probably 1-2 days of Claude Code time.

These don't depend on each other. Either order works; can ship in parallel branches.

**Order I'd suggest mentioning to Claude Code:** 028 first (unblocks better triage tonight), then 027 (diagnostic informs future tuning). Reverse is also fine if Claude Code prefers diagnostic-before-feature-work.

## Action items

For Claude Code:

1. **Request 027 saved separately** — runbook 004 + supporting script.
2. **Request 028 saved separately** — task 014-fix-1 inbox UX bundle.

For the operator (you):

3. Continue triage on existing candidates *while* Claude Code works on 028. Imperfect tools but real signal.
4. When 028 ships: `git pull` inside workspace, uvicorn `--reload` picks up changes, refresh browser. Re-triage candidates you couldn't confidently judge before; the detail view should help.
5. When 027's runbook ships: execute it (30 min), capture the artifacts (which signals nulled, their text, your judgment on whether Haiku's call was right), open follow-up orchestrator turn with findings.

## Meta note — the orchestrator pattern matures here

Three observations worth recording:

**You're now generating empirical input faster than the orchestrator can pre-spec for.** The triage session produced four distinct task-shaped findings in one turn. That's healthy and expected. Don't try to bundle them all preemptively into orchestrator turns — just send them as they come, and I'll route into appropriate task shapes (sometimes one task, sometimes parallel tasks, sometimes a runbook + a task).

**Personal preferences are valid signal but not always tasks.** The payment-processing exclusion is the first case where you've expressed a *taste* filter rather than a *system* finding. Both are valid; only one becomes code. Discipline: name the preference, reject manually, escalate to code only if volume warrants it.

**Friction patterns reveal feature priorities.** Every UX item in this turn (source visibility, detail view, notes, Unsure, links) came from you *trying* to triage and hitting a wall. That's the right priority order — *what blocked the current triage session*, not *what we think a good inbox should have*. Keep this discipline; resist building speculative features that haven't blocked you yet.
