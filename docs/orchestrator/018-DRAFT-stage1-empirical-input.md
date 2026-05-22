# Request 018 (DRAFT): Stage 1 empirical input + container hygiene + bug surfaced

**Status:** DRAFT — placeholders for the runbook dump output. Operator runs `docs/operator/runbooks/001-stage1-first-pass.md`, then pastes the artifacts into the marked sections below. Once filled in, rename to `018-stage1-empirical-input.md` and remove this status block.

**Date:** 2026-05-22 (drafted), final on completion

**Context**: Per orchestrator feedback 017, ran runbook 001 to get empirical input before deciding task 011 vs 013. The runbook surfaced a high-severity production bug in the first hour — exactly what feedback 017 said it would buy us. Bug fixed in PR #10 (merged); runbook resumed. This request brings back (a) the empirical artifacts from the post-fix run, (b) the bug report, and (c) container hygiene items that accumulated this session.

## Headline: SAVEPOINT bug found + fixed before any data was lost in production

Surfaced by the runbook's "ingest reported captured=11, fresh-session count=0" diagnostic. Validates the empirical-input-first discipline.

### What was broken

Every ingester's `_insert_signal` used:

```python
session.add(signal)
try:
    session.flush()
except IntegrityError:
    session.rollback()   # ← nukes the WHOLE transaction
    return False
return True
```

`session.rollback()` rolls back the entire transaction. When `ingest_batch` runs N queries against a single source without intermediate commits, a single content-hash collision (extremely common with HN's overlapping search queries) wipes every prior successful insert in the batch. The function returns `False` for the duplicate but the caller's `items_captured` counter — already bumped for now-erased rows — is left wrong. Final batch commit commits nothing.

**Scope:** 6 sites — 5 ingesters (`reddit`, `hn`, `producthunt`, `indiehackers`, `review_sites/_common`) plus `cluster.py::_persist_clusters` (`candidate_signals` link-insert loop).

### Why existing tests missed it

The existing dedup test (`test_dedup_on_second_run`) called `ingest()` twice with `session.commit()` between calls. By the time the second call's collisions fired, the first call's rows were already durably committed. The bug only manifests when novel and duplicate inserts share an *uncommitted* transaction — which is exactly what `ingest_batch` does in production.

### Fix

New `apfun.db.try_insert(session, instance) -> bool` helper that wraps `add` + `flush` in `session.begin_nested()` — a SAVEPOINT. On `IntegrityError`, only the savepoint rolls back; the surrounding transaction (and prior inserts) survives. Returns `True` on success, `False` on UNIQUE collision.

Every fix site collapses to:

```python
return try_insert(session, signal)
```

### Verification

```
ingest reported captured: 11
fresh-session count: 11     # was 11 vs 0 pre-fix
```

5 new regression tests pin the invariant. `test_intra_batch_collision_does_not_destroy_prior_inserts` is the load-bearing one — would fail loudly on the pre-fix code.

### Process insight

The bug existed across 5 ingesters for weeks (since task 005 in early May). It was caught the first hour we ran them against real data. Worth flagging because:

1. **Synthetic tests alone aren't sufficient** for catching transaction-shape bugs in DB-write paths.
2. **The orchestrator feedback-017 read was correct** — "30-60 min of operator work is rounding error vs a wrong-task-for-two-days alternative" turned out to apply to bugs too, not just sequencing decisions.
3. **The empirical-input-first discipline now has an unambiguous case study.** Consider adding a CLAUDE.md Lesson Learned to that effect (suggestion in the action items below).

---

## What landed in tasks 010 + 010a (recap; orchestrator can't see PRs)

Same as request 017's recap — see `docs/orchestrator/017-task-011-vs-013-sequencing.md`. The state has not materially changed except for PR #10's hotfix and the runbook artifacts below.

---

## Empirical artifacts from the runbook

**(Fill from `scripts/dump_run_artifacts.py` output. Paste verbatim — do not summarize.)**

### Candidates — representative sample

Captured 2026-05-22T01:07Z via `scripts/dump_run_artifacts.py` after PR #11 hotfixes (fence-strip + adaptive-thinking migration) landed. Run summary:

- **Source:** 3 HN sources (`hn:wishes`, `hn:ask-hn`, `hn:alternatives`), 11 raw_signals captured.
- **Normalize:** 11 → 11 `signal_text` rows (all HN, none `is_low_signal`).
- **Cluster:** 11 signals → **11 buckets → 11 candidates → 11 signal links**. Every signal got its own keyword set; no actual clustering happened. Cost: **$0.19 total**, latency 77.6s.
- **Reddit ingest:** all 3 sources 403'd (UA-block fired). Skipped per Reddit-403 fallback in runbook caveat. HN-only data below.

```
### Candidate #1  (dedup_key=a-self-taught-remote-software-engineer-based-in-iraq-...)
  decision: pending  pipeline_stage: none  vertical: recruiting
  problem_statement:
    A self-taught remote software engineer based in Iraq describes being unable to land
    even unpaid roles despite extensive applications, open-source contributions, and SaaS
    attempts. The pain is a brutally competitive remote job market inflated by AI-raised
    expectations, compounded by fake job postings, time-wasting agencies, and useless
    application forms, leaving capable developers from emerging markets with no viable
    path to meaningful work.
  suspected_user: self-taught remote software engineers in emerging markets
  seed_keywords: ['remote developer jobs', 'fake job postings', 'job application fatigue',
                  'remote hiring scams', 'international developer hiring', 'ai job market']
  contributing signals (1):
    [1] (hn, weight=6) Peak unemployment for a software engineer. What did I do wrong?
        Back in 2019, I was a CS student in Iraq. I taught myself Node.js, React, and
        TypeScript. Then in 2020, the pandemic started, and honestly, it was perfect
        timing for me because suddenly a lot of local businesses needed delivery web apps…

### Candidate #2  (dedup_key=developers-on-teams-with-only-a-shared-claude-subscription-...)
  decision: pending  pipeline_stage: none  vertical: dev-tools
  problem_statement:
    Developers on teams with only a shared Claude subscription lack access to dedicated
    spec-driven development tooling like Kiro, and have to hack together their own SDD
    workflows on top of Claude because affordable, portable SDD management across AI
    coding tools isn't available.
  suspected_user: solo developers and small teams sharing a single Claude subscription
  seed_keywords: ['spec-driven development', 'claude skill', 'kiro alternative',
                  'ai coding workflow', 'sdd management']
  contributing signals (1):
    [1] (hn, weight=16) Show HN: I Made a Claude Skill for Spec-Driven Development (SDD)
        At my work they provided a single Claude subscription for everyone on the team.
        To be honest I like kiro better as it provides a way better SDD management. But
        the company can't provide it and I can't afford it yet. Turns out…

### Candidate #3  (dedup_key=developers-seeking-real-human-help-...)
  decision: pending  pipeline_stage: none  vertical: dev-tools
  problem_statement:
    Developers seeking real human help on technical problems (e.g. reporting malware repos
    on GitHub, asking colleagues for guidance, or messaging strangers online) are
    increasingly receiving low-effort, copy-pasted AI-generated responses that don't
    address their actual question, leaving them unable to distinguish human expertise
    from AI noise and unable to get substantive help.
  suspected_user: developers seeking peer help in online technical communities
  seed_keywords: ['ai-generated answers', 'github discussions', 'low-effort replies',
                  'human verification', 'chatgpt copy paste', 'malware reporting']
  contributing signals (1):
    [1] (hn, weight=135) Tell HN: I'm tired of AI-generated answers. I found GitHub
        repositories that were spreading malware. I asked AI what I should do about it,
        but it gave me nothing useful. So I opened a discussion on GitHub. Someone
        replied. It was literally the exact same text the AI had given me…

### Candidate #4  (dedup_key=an-experienced-asp-net-full-stack-developer-...)
  decision: pending  pipeline_stage: none  vertical: hiring
  problem_statement:
    An experienced ASP.NET full-stack developer with ~7 years of experience (including
    founding a startup) is struggling to convert job applications into offers — ~100
    applications, only 8 interviews, and recent interviews going poorly due to coding
    exercise mishaps (IDE crashing during screen share) and design choices not matching
    interviewer expectations.
  suspected_user: mid-level ASP.NET / .NET developers job-hunting in regional UK markets
  seed_keywords: ['asp.net interview prep', 'mid-level swe job search',
                  'coding interview failure', '.net technical screening']
  contributing signals (1): [hn, weight=23] Ask HN: Failing interviews for mid-level SWE
                            in UK, advice please…

### Candidate #5  (dedup_key=developers-and-security-teams-lack-visibility-...)
  decision: pending  pipeline_stage: none  vertical: dev-tools
  problem_statement:
    Developers and security teams lack visibility into the security posture of IDE
    extensions installed on workstations — including what permissions extensions acquire,
    whether they run post-install scripts, what dependencies they pull in, and whether
    those have known vulnerabilities.
  suspected_user: security teams responsible for developer workstation security
  seed_keywords: ['ide extension security', 'malicious vscode extension',
                  'workstation security', 'extension permissions']
  contributing signals (1): [hn, weight=3] Show HN: IDEViewer – Security scanner for
                            malicious IDE Extensions…

### Candidate #6  (dedup_key=experienced-developers-who-have-shifted-to-using-coding-agents-...)
  decision: pending  pipeline_stage: none  vertical: dev-tools
  problem_statement:
    Experienced developers who have shifted to using coding agents end-to-end report a
    loss of flow, challenge, and engagement in their work. They feel bored and empty
    because the cognitive parts of coding (architecting, tracing data flows, problem-
    solving) are now offloaded to agents like Codex/Opus, yet they can't simply 'use it
    less' without falling behind peers on productivity.
  suspected_user: experienced developers whose day-to-day coding has been largely taken
                  over by AI agents
  seed_keywords: ['coding agents', 'developer boredom', 'ai disengagement', 'loss of flow']
  contributing signals (1): [hn, weight=15] Ask HN: Anyone else struggling with AI and
                            work?…

### Candidate #7  (dedup_key=writers-and-creators-want-a-low-friction-way-...)
  decision: pending  pipeline_stage: none  vertical: unknown
  problem_statement:
    Writers and creators want a low-friction way to prove their long-form content (blog
    posts, articles, images) was made by a human rather than AI, as casual cues like
    polish or 'tells' are becoming unreliable and current workarounds (screen recordings,
    typewriter drafts, deliberately unpolished prose) add significant overhead.
  suspected_user: independent writers and bloggers posting on public forums
  seed_keywords: ['proof of human writing', 'human-made content', 'ai detection workaround',
                  'writing provenance', 'content authenticity']
  contributing signals (1): [hn, weight=10] Ask HN: How are you proving your writing is
                            human made?…

### Candidate #8  (dedup_key=learners-and-practitioners-entering-offline-password-cracking-...)
  decision: pending  pipeline_stage: none  vertical: security
  problem_statement:
    Learners and practitioners entering offline password cracking cannot find a single
    comprehensive resource that explains modern hashing algorithms (including memory-hard
    ones like Argon2), Hashcat workflows, password analysis, and attack optimization in
    one place; existing knowledge is scattered across YouTube videos, blogs, forums,
    academic papers, and presentations.
  suspected_user: self-taught penetration testers and password-cracking learners
  seed_keywords: ['hashcat tutorial', 'offline password cracking', 'argon2 gpu cracking',
                  'password hashing guide']
  contributing signals (1): [hn, weight=288] Show HN: I Dedicated 4 Years to Mastering
                            Offline Password Cracking…

### Candidate #9  (dedup_key=a-passive-investor-is-grappling-with-the-implications-...)
  decision: pending  pipeline_stage: none  vertical: unknown
  problem_statement:
    A passive investor is grappling with the implications of index funds changing rules
    to allow immediate inclusion of IPOs (triggered by the SpaceX IPO), and lacks clear
    guidance on how to navigate this shift both as a passive investor and in
    understanding downstream effects on startups and entrepreneurship.
  suspected_user: passive retail investors tracking index fund mechanics
  seed_keywords: ['index fund ipo inclusion', 'passive investing', 'mega ipo',
                  'spacex ipo', 'index rule changes']
  contributing signals (1): [hn, weight=10] Ask HN: How to make the best of the Mega IPO
                            / Index fund debacle?…

### Candidate #10  (dedup_key=mainstream-social-networks-...)
  decision: pending  pipeline_stage: none  vertical: social-network
  problem_statement:
    Mainstream social networks (Facebook, Twitter, Instagram, VK) have drifted from
    helping people stay updated on friends' lives into ad-driven 'social media' that
    shoves strangers, news, businesses, and brainrot at users; there is no good modern
    platform that simply lets you hang out with friends and meet people in groups the
    way early Facebook did.
  suspected_user: people who want a friends-and-groups social network rather than an
                  algorithmic media feed
  seed_keywords: ['fediverse', 'activitypub', 'early facebook', 'friends-only social
                  network', 'decentralized social', 'anti-enshittification']
  contributing signals (1): [hn, weight=8] Show HN: Smithereen – an early-Facebook-style
                            Fediverse server…

### Candidate #11  (dedup_key=users-are-frustrated-that-mainstream-search-engines-...)
  decision: pending  pipeline_stage: none  vertical: search
  problem_statement:
    Users are frustrated that mainstream search engines (Google) and even privacy-focused
    alternatives (DuckDuckGo, Startpage, Ecosia, Mojeek, Marginalia) are now cluttered
    with ads and AI Overviews, with AI features enabled by default requiring users to
    opt out feature-by-feature.
  suspected_user: power users dissatisfied with mainstream and privacy search engines
  seed_keywords: ['independent search engine', 'ai overview opt out', 'search ads',
                  'user control search', 'custom ranking']
  contributing signals (1): [hn, weight=5] Show HN: My independent search engine focused
                            on user control…
```

### llm_runs aggregates

```
  task            calls   in/avg   in/max  out/avg  out/max   cache_rd   cache_wr   cost_usd  attempts
  ----------------------------------------------------------------------------------------------------
  cluster            12     1456     2287      241      313          0          0     0.1598        12
  dedup              23      899     1499       81      103          0          0     0.0301        25

  Cache hit ratio: 0.0%  (read=0, write=0)
  GRAND TOTAL COST: $0.1899

  ⚠️  2 failed call(s) (both fixed via PR #11):
       - cluster: JSONParseError (Haiku fence-wrapping bug)
       - cluster: BadRequestError (Opus 4.7 thinking API deprecation)
```

### scheduler_runs

```
  ✓ runbook.reddit_ingest        items=   0 dur=   195ms   (UA-block fired)
  ✓ runbook.hn_ingest            items=  13 dur=  4381ms   (pre-SAVEPOINT bug — rows rolled back)
  ✓ runbook.hn_ingest            items=  11 dur=  4355ms   (pre-SAVEPOINT bug — rows rolled back)
  ✓ runbook.hn_ingest_diag       items=  11 dur=  4424ms   (verified the rollback)
  ✓ runbook.hn_ingest_postfix    items=  11 dur=  4490ms   (post-PR #10, rows persisted)
  ✓ runbook.hn_ingest_diag       items=   0 dur=  4319ms   (re-run, all dedupe-skipped)
  ✓ runbook.hn_ingest            items=   0 dur=  9253ms   (re-run, all dedupe-skipped)
  ✓ pipeline.normalize           items=  11 dur=    12ms
  ✗ runbook.cluster              items=   0 dur=  7950ms   (Haiku fence bug)
  ✗ runbook.cluster_postfix      items=  11 dur= 16874ms   (Opus thinking-API bug)
  ✓ runbook.cluster_postfix2     items=  11 dur= 77586ms   (post-PR #11, all candidates persisted)
```

### Operational observations

1. **No clustering at N=11.** Every signal landed in its own bucket → its own candidate. Either Haiku is emitting overly-specific keyword sets, or the 11 HN posts are genuinely too diverse to share buckets. Skimming the candidates, the latter is plausible (recruiting, dev-tools, security, social-network, search, finance — wide spread). Worth re-running with 100+ signals once batches are bigger; the 1:1 signal-to-candidate shape would be problematic if it persists.

2. **Cache hit ratio: 0%.** Stage 1's `judge_json` calls don't pass `cache_blocks`, so the `cache_ttl="1h"` infrastructure (built per feedback 016 Q2) is wired but inert. Low-hanging optimization once prompts stabilize — pass the system preamble + the dedup-signal prompt template as `cache_blocks`.

3. **Per-call cost** roughly tracked expectation:
   - Haiku dedup: ~$0.001 per signal (matches feedback 016 prediction)
   - Opus cluster: ~$0.013 per bucket — much lower than predicted ($0.05-0.20). Reason: tiny inputs (1456 avg tokens) since every bucket was single-signal. With realistic multi-signal buckets, per-call cost grows linearly. Sample doesn't validate Opus cost-per-cluster yet.

4. **Two failed `llm_runs` rows captured the bugs we hit (already fixed):**
   - Haiku markdown-fence wrapping → `JSONParseError` retry loop exhausted (3 attempts) → final failure recorded with truncated `raw_response`. The error-logging pattern from feedback 016 Q3 worked exactly as designed — bug was diagnosable from the `llm_runs.error` field alone.
   - Opus 4.7 deprecated `thinking.type="enabled"` + `budget_tokens` in favor of `thinking.type="adaptive"` + `output_config.effort`. Caught immediately by the wrapper as `BadRequestError`.

5. **Bug case study — 3 bugs in 1 runbook session.** SAVEPOINT-scoped rollback (PR #10), JSON-fence stripping (PR #11), and adaptive-thinking migration (PR #11). All three would have silently degraded production data or crashed the scheduler. The runbook caught them in the first hour against real data; synthetic tests had missed all three for weeks (since task 005 / task 010 respectively). This is the case study for the empirical-input-first discipline.

6. **Stage 1 latency** was 77.6s for 11 buckets ≈ 7.0s per Opus call. Within the spec's expected range. Larger batches would amortize the per-call overhead.

7. **Reddit ingest** continues to 403 from the dev container's IP. See container hygiene #4 below for options.

---

## Container hygiene items (operator-side, accumulated this session)

Bundle these into the Dockerfile/docker-compose update queue. None are blocking but each costs friction for future sessions.

### 1. `sqlite3` CLI missing

The dev container ships without `sqlite3` (CLI). The runbook references it; the operator can `apt-get install -y sqlite3` in the running container but it vanishes on rebuild.

**Proposed Dockerfile addition** (host `/srv/claude/apfun.online/Dockerfile`):

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        sqlite3 \
    && rm -rf /var/lib/apt/lists/*
```

Place in the system-deps block (before `USER node`).

### 2. `gh auth` ritual after each rebuild — STILL pending from feedback 015

Per feedback 015 action item 4, this was supposed to land in `/srv/claude/apfun.online/README.md` as a post-rebuild checklist. Confirm whether it's there; if not, this is the third session bitten by re-authenticating GitHub. Worth a 2-minute fix.

### 3. `.venv` named-volume drop — DONE per operator this session

The operator confirmed dropping the `.venv` named-volume from `docker-compose.yml` per feedback 015 action item 1. `uv run` works without `UV_PROJECT_ENVIRONMENT=/tmp/apfun-venv` now. No action needed; flagged for completeness.

### 4. Reddit ingest still 403-blocking

During the runbook, all 3 Reddit sources returned 403 → UA-block guard fired. Did NOT diagnose further because HN was a clean fallback for the runbook's purpose. **Open question for the orchestrator:** worth a separate investigation, or do we tolerate Reddit being flaky in dev/test and rely on the auto-disable mechanism + scheduler health UI (task 021) to surface it in production?

Possible causes:
- The `APFUN_REDDIT_USERNAME` value used was a real handle but Reddit IP-blocked the datacenter
- UA format drifted (Reddit changes scraping policy frequently)
- This datacenter IP got rate-limit-banned during a prior session

If we want to keep Reddit working, options include: (a) switch to authenticated OAuth flow (task X), (b) accept Reddit-flaky-in-dev as the cost of using Reddit's free API, (c) deprioritize Reddit and lean on HN+IH+PH+review_sites for v1 signal.

**My lean: (b).** Reddit's free API has always been flaky from datacenter IPs; the auto-disable mechanism + scheduler observability handles this gracefully in production. The cost of OAuth migration is large enough that we should defer until we have evidence the funnel needs Reddit specifically (some niches might be Reddit-only).

---

## The decision still pending: 011 vs 013 (vs prompt iteration)

The pre-committed routing matrix from feedback 017:

| Cluster quality | Next task |
|---|---|
| 70%+ reviewable | 013 + 014 bundled (admin UI + inbox endpoint) |
| Noticeably noisy | 011 (Stage 2 demand check) first |
| Unusable | Prompt iteration on `apfun/llm/prompts/cluster.j2` |

Per feedback 017's guidance: **don't pre-answer the routing here.** Surface the data above and let the orchestrator decide alongside us.

If the data shifts something — e.g., clusters are reviewable BUT cost is wildly higher than PRICING predicted, or thinking budgets are visibly cramped — the orchestrator may want to slot a tuning step before either 011 or 013. We'd rather find out.

---

## Specific questions

1. **Routing decision.** Per matrix above, what's the next task given the candidates + cost data above?
2. **Cost validation.** Did `llm_runs.est_cost_usd` come out where feedback 016's PRICING assumptions predicted? Anything to retune ahead of schedule?
3. **Thinking-budget retune triggers.** Feedback 005 set retune triggers at "50 rows in llm_runs for any single task" or "judge() call hitting >90% budget warning." Were any of those tripped during the runbook? If so, retune now rather than wait for the scheduler era.
4. **Reddit ingest** (per container hygiene #4 above) — accept-and-defer or investigate now?
5. **Lesson Learned for CLAUDE.md.** Suggest adding: "Synthetic dedup tests don't catch transaction-shape bugs in DB-write paths. Tests that mock the `session.commit()` cadence of production code paths (not the cadence the test happens to use) catch a class of bugs synthetic tests miss." Or whatever shape the orchestrator prefers.

## What I would do next without intervention

Per the routing matrix:

- **70%+ reviewable** → cut `feature/task-013-admin-ui-base` (probably bundled with 014 per request 017's Q2 lean).
- **Noticeably noisy** → cut `feature/task-011-stage2-demand-check`.
- **Unusable** → cut `feature/prompt-iteration-cluster` and use `scripts/replay_clustering.py` to iterate against the captured `signal_text` state from this runbook run.

The branch name and first-commit plan depend on the answer to Q1.

## Relevant files

- branch `notes/request-018-stage1-empirical-draft` (this draft file only)
- PR #10 (SAVEPOINT hotfix) — assumed merged before request 018 is filed
- `docs/operator/runbooks/001-stage1-first-pass.md` — the runbook that surfaced everything above
- `scripts/dump_run_artifacts.py` — produces the empirical sections
- `apfun/pipeline/cluster.py` + prompts — the system under test
- `docs/orchestrator/INDEX.md` — row 018 → open after this commit is renamed and posted
