# Feedback 019 — scheduler next; small chore PR alongside

**Date:** 2026-05-22
**Request:** 019-next-direction.md
**Outcome:** Task 012 (scheduler) confirmed next. Task 011 (Stage 2) deferred to a routing decision after 3-5 scheduler cycles. Container hygiene fixed via bootstrap script (not Dockerfile edit yet). Nav placeholders bundled as a parallel chore PR. Cadence intervals unchanged for v1.

## Q1 — 012 scheduler first: confirmed

**Stage 2 has nothing meaningful to filter yet.** Three reasons:

1. **Cadence at task 012's prescribed intervals isn't "flooding."** Stage 1 every 2h produces maybe 10-20 candidates/day at current ingest volume — manageable inbox load and exactly the scale where unfiltered output reveals what the candidate distribution actually looks like.
2. **Auto-kill rate is empirically unknown.** Runbook 001's sample looked grounded and reviewable. If that pattern holds at 10×, Stage 2's contribution is marginal. If it doesn't, the empirical evidence tells us *what kind* of Stage 2 filter we actually need.
3. **Inbox is already self-correcting.** "Rejected with new signals → re-review?" plumbing (feedback 016 Q5) means systematic noise surfaces naturally — operator rejects fast, signals accumulate, the surface re-prompts.

**Routing trigger for the 011 vs 015 decision:** when scheduler-driven Stage 1 has run ~3-5 cycles and you have ~30-50 candidates in the inbox, open a short orchestrator turn with:

- Candidate count
- Rough %-judged-as-noise by operator triage
- Visible patterns in what's being rejected

That empirical input routes 011 (build the filter) vs 015 (proceed to Stage 3 competitive scrape, lean on HITL alone to filter).

## Q2 — Container hygiene: bootstrap script now, Dockerfile fix bundled later

**Option (c) with deliberate sequencing.**

**Now (in this PR cycle):** write `scripts/post-rebuild-bootstrap.sh` that installs sqlite3 + walks the gh auth check. Lives in the workspace; no Dockerfile edit; no operator interruption. Idempotent.

```bash
#!/usr/bin/env bash
# scripts/post-rebuild-bootstrap.sh
# Run inside the dev container after a rebuild. Idempotent.
set -euo pipefail

if ! command -v sqlite3 >/dev/null; then
    sudo apt-get update && sudo apt-get install -y --no-install-recommends sqlite3
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "GitHub auth missing. Run:"
    echo "  gh auth login --hostname github.com --git-protocol https --web"
    echo "  gh auth setup-git --hostname github.com"
    exit 1
fi

echo "Post-rebuild bootstrap: OK"
```

Operator runs `docker exec -it apfun-funnel /workspace/scripts/post-rebuild-bootstrap.sh` after `docker compose up -d --build`.

**Update `/srv/claude/apfun.online/README.md`** (operator-side) as the canonical post-rebuild action.

**Bundled later, not now:** the `sqlite3` apt entry in the host Dockerfile. Roll into whatever next Dockerfile edit happens naturally — likely a new dep for task 011 (pytrends might pull something fun) or an upstream Chromium upgrade. Don't synthesize a Dockerfile-only PR for one apt entry.

**Never:** option (a). Interrupting task 012 for one apt line is cost-benefit-unfavorable when the bootstrap script gets you 90% of the value.

The `gh auth` portion doesn't belong in the Dockerfile regardless — it's interactive and stateful. The bootstrap script is the right shape for that piece permanently.

## Q3 — Cadence intervals: keep prescribed for v1

**Don't tighten yet.** Two reasons to resist the cost-is-low temptation:

1. **Stage 1's $0.013 was the single-signal-bucket artifact** (feedback 018 Q2). Multi-signal buckets at higher N will scale per-call cost up by an unknown factor. Locking tighter cadence now commits to whatever cost emerges. 2h is the conservative default; tighten *after* the cost re-validation gate.
2. **Ingest cadence is upstream-rate-limited, not us.** HN/ProductHunt/IndieHackers don't generate enough new content per hour to justify hourly polling. Most polls would return zero new rows. The 6h/daily intervals approximate "wake when there's plausibly new content"; tightening just adds polite-throttling pressure without proportional yield.

**Retune mechanism:** after 2-3 days of running, look at `scheduler_runs.items_returned` distribution per job.

- Most runs come back empty → cadence too tight.
- Most come back near per-run cap → too loose.

5-minute commit, not a design question.

Same answer for Stage 1 cadence (2h) and ingest cadences (6h/daily): trust the prior, validate empirically, retune from data.

## Q4 — Stage 2 runbook: pre-acknowledged

The runbook shape you sketched is right (~5 approved candidates through demand check, capture verdicts + rate-limit observations).

**One specific note for when task 011 lands:** pytrends has a long history of unpredictable rate-limit behavior — bounces between "works fine" and "Google flat-blocks the IP for 24h" with no published quota. The runbook should specifically probe this: 5-10 trends-fetches in rapid succession during the session, document whether anything 429s. If it does, implementation needs much more aggressive backoff than the docs suggest.

Build the runbook with that probe baked in. No other action now.

## Q5 — Nav placeholders: small chore PR alongside

**Bundle a tiny placeholder PR.** Cost ~20 min; benefit is "the UI doesn't look broken to anyone clicking around." Not just polish — the 404s undermine confidence in demos.

Three stub routes returning a "Coming in task 020/021" page with standard chrome. One template, three routes, ~50 lines. `chore/inbox-nav-placeholders` branch, single small PR.

**Don't pre-commit task 020/021's designs.** Placeholders just remove 404 noise.

If 20 minutes feels disruptive: bundle into task 012's PR. Zero coupling with the scheduler; they coexist without entanglement.

## Aside — use the inbox

Now that you can look at the 11 candidates in a browser: **use it.** Approve some, reject some, leave some pending. The act of triage is itself empirical input — you'll learn things about clustering quality that no data dump reveals.

If during triage you notice patterns like "suspected_user is always vague" / "every dev-tools candidate gets approved" / "I keep rejecting AI-related things" — those are inputs that should inform Stage 1 prompt iteration or downstream stage design. Worth a short "human-in-the-loop observations" note added to runbook 001 (or a successor) when you've reviewed enough to have an opinion.

This is the kind of feedback only you can provide; the orchestrator can reason about clustering quality but can't substitute for actually reviewing the cards.

## Action items

### Task 012 PR (the meat)

1. Implement scheduler per task 012 spec.
2. **Prescribed intervals unchanged** (Reddit/HN 6h, PH/IH daily, Stage 1 2h).
3. Empty job slot for Stage 2; comment indicating "wired in task 011 PR."
4. Don't bundle nav placeholders or bootstrap script — keep this PR focused.

### Small chore PR (parallel or before)

5. `scripts/post-rebuild-bootstrap.sh` per the sketch above.
6. Nav placeholder routes for `/opportunities`, `/sources`, `/projects`.
7. Update `/srv/claude/apfun.online/README.md` (operator-side) referencing the bootstrap script.

### Future-tracked (don't act yet)

8. After 3-5 scheduler cycles: short orchestrator turn with candidate count + operator-triage observations. Decides 011 vs 015 sequencing.
9. After ingest runs a few days: tune `scheduler_runs.items_returned` distribution per job for cadence retuning.
10. When task 011 starts: build pytrends rate-limit probe into its runbook.

## Next step

Two small things in parallel:

- **Chore PR** (bootstrap script + nav placeholders) — low ceremony
- **Task 012 PR** — the scheduler proper

Once scheduler is live and Stage 1 runs on its own, the project crosses into operational mode. The next several turns will likely be smaller and tighter — observation, retune, integrate Stage 2, repeat. Task 015 (Stage 3 — competitive scrape with DataForSEO) is the next big design surface, probably 1-2 weeks out at this pace.

## Meta note

The shift from "build the funnel" to "use the funnel" happens *when scheduler ships*, not when the inbox shipped. Right now the inbox shows a static snapshot from runbook 001. After 012, it shows a living pipeline.

Worth budgeting some attention to actually reviewing what the funnel produces, not just shipping the next task. The orchestrator pattern works best when the operator's lived experience of the system informs the next round of design decisions — and that experience requires actually using the inbox you just built.
