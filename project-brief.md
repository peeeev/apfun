# Project Brief: apfun — SaaS Opportunity Funnel

A constantly-running pipeline that surfaces SaaS/software opportunities, scores them, and feeds approved ones into a build pipeline. Human is in the loop at one specific gate; everything else runs on a schedule.

This document is the **source of truth** for the project. When in doubt, refer back here. Update this file as decisions get made.

---

## 0. Directory Boundaries — READ FIRST

Claude Code: this section governs what you can and cannot touch. Re-read it any time you're tempted to author infrastructure-level files.

### What you can see and modify
You are running inside a Docker container. Your current working directory `/workspace` is bind-mounted from `/srv/claude/apfun.online/workspace/` on the host. **Everything you can read or write lives at or below `/workspace`.** This is where the funnel application code, admin UI, tests, docs, and project artifacts (PRDs, task lists, CLAUDE.md) go.

### What exists outside your sandbox (you cannot see it, you do NOT author it)

```
/srv/claude/                                    ← host, you cannot see
├── PORTS.md                                    ← host infrastructure
├── README.md                                   ← host infrastructure
├── apache/                                     ← host infrastructure (Apache vhosts)
├── apfun.online/                               ← host directory
│   ├── Dockerfile                              ← INFRA: defines THIS dev container
│   ├── docker-compose.yml                      ← INFRA: defines THIS dev container
│   ├── README.md                               ← INFRA
│   └── workspace/                              ← MOUNTED to /workspace in your view
│       └── (everything you see lives here)
├── bin/                                         ← INFRA (project-scaffolding scripts)
└── projects/                                    ← host: subdomain projects
```

**Rules:**
1. The `Dockerfile` and `docker-compose.yml` you might be tempted to create at the root of `/workspace` would **not be the container you're running in**. The dev container is governed by files OUTSIDE your visibility, on the host. Do not write a `Dockerfile` or `docker-compose.yml` inside `/workspace` for the purpose of "fixing" or "replacing" the dev container — that won't work, and the human will have to clean up after you.
2. If the funnel needs the dev container changed (new system package, new port, different CMD), **say so in chat** and ask the human to update `/srv/claude/apfun.online/Dockerfile`. Don't try to author a replacement.
3. **You may** later create a `Dockerfile`/`compose.yml` INSIDE `/workspace` if the admin UI gets a separate production deployment elsewhere — but only after explicit discussion with the human. Not in v1.
4. The admin UI process runs inside this dev container, started via the host's `docker-compose.yml` `command:` directive once the code exists. You write the application; the human flips the switch.

### Network and ports

You are inside a container; your "localhost" is not the host's. Apache on the host reverse-proxies `https://apfun.online` → `127.0.0.1:4000` on the host → port 4000 inside this container. So:

- **Bind ASGI/HTTP servers to `0.0.0.0:4000`, NEVER `127.0.0.1`.** Localhost-only binding is unreachable from the host and the request will never arrive.
- The only port that matters is 4000. Don't randomly pick others.

---

## 1. Goal

Produce, every week, a small number of high-quality SaaS opportunity candidates — each backed by demand data, competitive analysis, and a proposed differentiation angle — for the human (Alex) to decide whether to build.

Implicit corollary: **the funnel produces hypotheses, not validated businesses.** Validation still requires talking to humans. The system's job is to make the candidate list dramatically better than what staring at a blank page produces.

---

## 2. The Funnel Pipeline

Six stages. Stages 1–2 run automatically; Stages 3–5 only fire on human-approved candidates.

### Stage 1 — Idea Sourcing
Pull raw signal from places where pain shows up naturally:
- Subreddit firehoses: `r/SaaS`, `r/Entrepreneur`, `r/SmallBusiness`, plus 10–20 vertical subs (e.g. `r/Etsy`, `r/REI`, `r/dentistry`)
- Hacker News: "Ask HN: what tool do you wish existed" threads via Algolia search API
- IndieHackers posts
- ProductHunt launches (what's hot + by negative space, what's *missing*)
- **Review mining on G2 / Capterra / Trustpilot** — 1–3★ reviews on incumbents. Where paying customers complain about real products is gold.

Cluster complaints into candidate "idea cards" with: problem statement, suspected user, seed keywords, source links. Expected: 50–200 raw cards per day pre-filter.

### Stage 2 — Demand Check (cheap filter)
For each clustered candidate:
- Google Trends via `pytrends` (free, rate-limited) for trajectory shape
- Autosuggest scrape for related queries
- Kill candidates with flat/declining trends unless there's a strong "why now"

Survivors carry into the inbox. **Stage 2 is where the auto-pipeline ends.**

### 🚪 HITL Gate — between Stage 2 and Stage 3
The human reviews the inbox of survivors in the admin UI (apfun.online). Thumbs-up = approve for Stage 3+. Thumbs-down = drop. Optional comment field for "investigate angle X."

**Why here?** Stages 1–2 are nearly free; Stages 3–5 use paid APIs (DataForSEO) and significant LLM tokens. The HITL gate is what keeps the cost curve sane while preserving most of the upside.

### Stage 3 — Competitive Scrape (paid APIs start here)
For approved candidates:
- DataForSEO for keyword volume, CPC, paid difficulty, "alternatives to X" volume, top-10 organic + paid SERP
- Scrape competitor sites for: pricing pages, feature lists, recent funding news
- Mine reviews from G2 / Capterra / Trustpilot for the top 3 competitors — this is where the differentiation signal lives

### Stage 4 — Saturation Scoring
Composite score, not a single number:
- **Demand** = volume × CPC × growth_rate
- **Supply** = #established_competitors × avg_DA × estimated_ad_spend
- **UnmetPain** = % negative review sentiment on top competitors, weighted by common complaint themes
- **MoatPotential** = technical complexity, integration depth, regulatory angles

Rough formula: `Opportunity = (Demand × UnmetPain) / IncumbentStrength`

The weights start as rough guesses and are recalibrated over time as scored ideas turn out to be real or not. Score outputs persist so we can learn from them.

### Stage 5 — Differentiation Synthesis
The expensive thinking step. Inputs: full review corpus, feature matrix, pricing data, SERP analysis. Outputs:
- Top 5 unmet complaints
- Feature gaps in top 3 incumbents
- Pricing-tier gaps (e.g. "missing $19/mo tier between free and $99")
- Vertical wedge suggestion (which underserved vertical is the entry point — verticalization usually beats head-on competition)

### Stage 6 — Output
Each surviving opportunity becomes a structured record in the admin UI with: problem statement, market size, competitors, score breakdown, differentiation angle, sources. Weekly digest email of the top 5.

---

## 3. Model Selection Policy

**Default model: Claude Opus 4.7 with extended thinking ("xhigh"/high reasoning effort).** Use it for:
- Stage 1: clustering raw signals into coherent candidate ideas
- Stage 4: saturation scoring and weighing
- Stage 5: differentiation synthesis (THE most important step quality-wise)
- Any decision involving niche evaluation, competitor analysis, prioritization, or "is this opportunity real"
- PRD generation (Gate 1 of the dev handoff)
- Architecture proposals (Gate 2 of the dev handoff)

**Cheap-and-fast: Claude Haiku 4.5.** Use it ONLY for trivial mechanical tasks:
- Deduplication checks ("is this raw post about the same problem as that one?")
- Single-field classification (e.g. "what vertical does this belong to: dev / fintech / health / other?")
- JSON validation and reformatting
- "Is this on-topic" filtering of raw scraped content
- Stage 1 first-pass dedup before clustering

**Never default to Haiku when judgment matters.** A wrong call in Stage 5 wastes a real human decision; a wrong call in dedup just costs a few cents. The Pro/Max subscription covers Opus usage — don't optimize for token cost at the expense of opportunity quality.

If a task feels in-between (e.g. summarizing a single competitor's reviews), use Opus. The Max plan absorbs the cost; the quality difference is real.

---

## 4. Infrastructure (Already In Place — Don't Re-Architect)

Already built, working. Just use it.

- **Host:** Hetzner Ubuntu 24.04 server, hostname `traffictools`
- **Domain:** apfun.online (Cloudflare-managed DNS, wildcard TLS via Let's Encrypt + certbot, auto-renews)
- **Apache:** reverse-proxies `https://apfun.online` → `127.0.0.1:4000` with basic auth (htpasswd-funnel)
- **This container:** runs from `/srv/claude/apfun.online/`, mounts `./workspace/` → `/workspace`. See §0.
- **New project subdomains:** spun up by `/srv/claude/bin/new-project.sh <slug>` on the host. Don't reinvent this.

**Critical constraint (repeating from §0):** bind to `0.0.0.0:4000` inside the container.

---

## 5. Tech Stack (Opinionated Defaults)

These are the defaults. You may push back with a strong case, but in absence of one:

- **Language:** Python 3.11+ (already in the container; `uv` is also pre-installed)
- **Admin UI backend:** FastAPI
- **Admin UI frontend:** HTMX + minimal Tailwind (no SPA, no React unless there's a specific reason)
- **Storage:** SQLite to start. Migrate to Postgres only if size or concurrency requires it.
- **LLM client:** Anthropic Python SDK (`anthropic`)
- **HTTP scraping:** `httpx` for simple GETs, `playwright` for review sites that need JS rendering
- **Scheduling:** APScheduler running inside the same FastAPI process (simpler than separate cron). If load grows, split out.
- **Queue:** in-process initially; introduce Redis/RQ only if needed
- **Testing:** pytest, with a small fixtures library for cached SERP/Reddit responses
- **Dependency management:** `uv` with a checked-in `uv.lock`

Rationale for SQLite + HTMX + in-process scheduling: single-user, low-traffic admin tool. Adding Postgres + Celery + React is premature complexity that delays getting to the first real opportunity. We graduate later.

---

## 6. Data Model (Rough Sketch)

Propose a refined version in Gate 2. Starting point:

- `sources` — Reddit subs, HN, ProductHunt, G2 categories etc., with last-fetched timestamps
- `raw_signals` — individual posts, threads, reviews; deduped by content hash
- `candidates` — clustered idea cards (output of Stage 1)
- `demand_checks` — Trends/autosuggest results (Stage 2)
- `approvals` — HITL decisions with timestamps and optional comments
- `competitive_analyses` — Stage 3 output, one row per competitor per approved candidate
- `scores` — Stage 4 output, full breakdown (not just composite)
- `opportunities` — final Stage 5 records (the things shown in the digest)
- `projects` — subset of opportunities that progressed to actual builds, linked to subdomain slug

JSON columns are fine for the messier blobs (e.g. competitive analysis details). Don't over-normalize early.

---

## 7. Schedules

- Reddit sourcing: every 6 hours
- HN Algolia search: every 6 hours, staggered offset
- ProductHunt: daily
- G2/Capterra review scraping for tracked competitors: weekly
- Google Trends enrichment of pending candidates: daily
- Stage 3–5 pipeline: triggered async by HITL approval, not on a schedule
- Weekly digest email: Mondays 9am

All schedule-driven jobs idempotent — if one is killed mid-run, the next firing should be able to resume cleanly.

---

## 8. The Admin UI

Single user (the human, behind basic auth at the Apache layer — your app does NOT need its own auth). What it shows:

- **Inbox** (homepage): cards for Stage 2 survivors awaiting HITL decision. Each card: problem statement, source link, trend chart sparkline, seed keywords. Buttons: approve, reject, comment.
- **Opportunities**: list of Stage 5 outputs. Sortable by composite score, demand, recency. Click → full record.
- **Sources health**: which scrapers ran last when, error rates, queue depth.
- **Projects**: list of subdomains spun up via `new-project.sh`, with quick links + status.

UX bar: "I'd actually use this daily." Fast (server-rendered HTMX, no spinner), no useless animations, dense information layout, keyboard shortcuts for approve/reject in the inbox.

---

## 9. The Four-Gate Dev Handoff

When an opportunity is approved for build, this is the process for handing off to Claude Code:

### Gate 1 — PRD Generation
Claude Code (Opus 4.7 xhigh) takes the opportunity record and produces a structured PRD: user personas, core user stories, MVP feature list (ruthlessly cut), out-of-scope list, success metrics, tech constraints. Human edits and approves.

### Gate 2 — Architecture + Task Breakdown
Claude Code produces: tech stack proposal with rationale, system architecture (mermaid diagram), data model, API surface, sequenced task list broken into PR-sized chunks (each ~half-day, independently testable). **No code yet.** Human reviews and catches wrong tech choices here.

### Gate 3 — Per-Task Execution
For each task: Claude Code writes code + tests, runs tests, runs lint/typecheck, opens a PR with description. Human reviews PR, merges. CLAUDE.md is updated whenever the human corrects something so the lesson persists.

### Gate 4 — Deploy + Validate
Auto-deploy to staging on PR merge. Human-gated prod deploy for the first month. Smoke tests + poke-around session before each prod push.

---

## 10. Constraints and Gotchas

Pre-recorded so you don't suggest the wrong solutions:

- **Directory boundaries (§0).** This is the rule you're most likely to break. Re-read §0 before writing any Docker or compose files.
- **Bind to `0.0.0.0`, not `127.0.0.1`.** Localhost inside the container is unreachable from the host.
- **Don't go shallow on sourcing.** If we only watch the obvious subreddits we'll only generate the obvious ideas. Vertical subs + review mining are where the non-obvious opportunities are.
- **Saturated ≠ skip.** Saturated niches have proven demand. The skip signal is saturated + low unmet pain.
- **LLMs hallucinate competitor features from SERP snippets.** Always scrape-then-summarize.
- **Reviews skew negative across the board.** Normalize against the category baseline.
- **Don't over-engineer.** Single user, low traffic, low concurrency. SQLite + in-process scheduling + HTMX is plenty.
- **Cost discipline.** DataForSEO and LLM calls have real costs. The HITL gate exists specifically to gate expensive operations. Don't accidentally fire Stage 3+ on raw Stage 1 candidates.

---

## 11. What Success Looks Like

Not "the system generates 200 ideas a day" — that's vanity. Success is:

- **Hit rate**: of the opportunities approved at the HITL gate, what fraction look genuinely interesting after Stage 5 deep-dive? Aim for >50% after 4 weeks of calibration.
- **Time-to-build**: from HITL approval to a live placeholder subdomain — under 1 day end-to-end including PRD generation.
- **Surprise rate**: how often does the system surface something the human wouldn't have thought of? Track qualitatively in a notes column.

---

## 12. Out of Scope (for v1)

Explicitly NOT building in v1:

- Multi-user support
- A public API
- Mobile-responsive design
- Notion/Airtable bidirectional sync (the native admin UI IS the inbox)
- ML scoring (LLM-based scoring is plenty)
- A11y compliance beyond basic semantic HTML
- i18n
- Real-time updates (server-rendered with manual refresh is fine)
- Authentication inside the app (Apache basic auth handles it)
- A separate production Docker image (the dev container IS the runtime for v1)

If you want to add any of these, push back hard.

---

## 13. Bootstrap Process — START HERE

This is Gate 2 of the four-gate process, applied to the funnel itself.

When you read this for the first time:

1. **Re-read §0 (Directory Boundaries) carefully.** Then re-read §10. These are the two sections you're most likely to violate.
2. **Don't write code yet.** Don't create Dockerfile or docker-compose.yml anywhere. Don't run anything that would launch a server.
3. **Summarize this brief back to the human** in your own words to confirm understanding. Be explicit that you understand: (a) what's INSIDE `/workspace` is yours, (b) what's outside is infrastructure you don't touch, (c) the admin UI binds to `0.0.0.0:4000`.
4. **Propose the repo structure** inside `/workspace/`. Use a tree diagram. One-line rationale per directory.
5. **Confirm or push back on the tech stack** in §5 with rationale.
6. **Propose the data model** — refined version of §6, with column types and table descriptions.
7. **Draft `/workspace/CLAUDE.md`** capturing:
   - The model selection policy (§3) — Claude Code itself must follow this when the code makes LLM calls
   - The `0.0.0.0` binding rule
   - The directory-boundaries rule (§0)
   - Project conventions (Python style, test layout, commit conventions)
   - A "lessons learned" section, initially empty, to be appended whenever the human corrects you
8. **Produce a sequenced task list** in `/workspace/docs/tasks/`, one file per task, each PR-sized. Numbered: `001-...`, `002-...`, etc.
9. **WAIT for explicit approval** before writing the first task's code.

When the human approves, work through tasks one PR at a time. After each merged PR, refresh CLAUDE.md if anything was learned.

---

## 14. Open Questions

Things we haven't decided yet, to surface for discussion:

- **Email delivery for the weekly digest** — Mailgun? Postmark? Self-hosted? (Low priority.)
- **DataForSEO budget cap** — defaulting to $25/mo with hard stop.
- **Reddit auth** — using a Reddit app or pulling via public JSON endpoints? (Apps give higher rate limits but require setup.)
- **Long-term storage growth** — at what record count do we plan the SQLite → Postgres migration? Let's pick a number, e.g. 100k raw_signals.

These don't block v1. Park them, decide as they become real.
