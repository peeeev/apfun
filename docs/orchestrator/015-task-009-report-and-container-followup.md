# Request 015: task 009 report + container-rebuild regressions to fix

**Date:** 2026-05-19

**Context**: Task 009 (review miner) implementation is complete and PR #6 is open. All six design questions from feedback 014 are applied. Two regressions introduced by the host Dockerfile rebuild need attention before they bite a future session. Surfacing both the task report and the container issues together, plus two follow-up design questions worth flagging now that the funnel has five sources feeding `raw_signals`.

## Task 009 — what landed (PR #6, commit `1f1d4e3`)

### Shared scaffolding (`apfun/sourcing/_base.py`)

- **`IngestClient` Protocol** — minimal `close()` lifecycle contract. `httpx.Client` satisfies implicitly. Per feedback 014 Q3.
- **`BrowserBatchClient`** dataclass holding one `Playwright` + one `Browser` per batch. Per-source `ingest()` creates one `BrowserContext` per source so cookies/storage stay scoped. Browser launch (~1-2s) amortized across all sources in a batch (Q1).
- Test-only diff for the existing four ingesters; 24/24 batch tests pass unchanged.

### Review-sites package (`apfun/sourcing/review_sites/`)

- **`_common.py`** — `ReviewDict` TypedDict, `review_content_hash` with body-prefix fallback when `review_id` is absent (Q5), `CLOUDFLARE_BLOCK_MARKERS` list + `detect_block()` / `raise_if_blocked()` helpers (Q6), umbrella `ingest()` / `ingest_batch()` that dispatches to the per-site adapter named by `source.config_json["site"]`. Operator-config errors (`bad_config_site`, `unknown_site`) don't increment `consecutive_failures`.
- **`g2.py` / `capterra.py` / `trustpilot.py`** — per-site adapters with `fetch_reviews(context, product, *, max_pages, min_star, max_star) -> list[ReviewDict]`. Selectolax-based card parsing; per-page jitter (1-3s) on top of the 0.5 req/s + burst 2 global bucket.
- **Playwright Chromium UA** (not `apfun-funnel/X`) — deliberate exception per feedback 014 Q6.

### CSV manual-import fallback (`scripts/import_reviews.py`)

Shares the dedup key (`review_content_hash`) with scraping so re-importing the same CSV writes zero new rows. Source rows auto-created on first encounter (`<site>:<slug>-manual`). Same PR as scraping per Q4. **A small assertion to flag**: I shipped it as a single commit rather than the optional `009a/009b` split — the CSV path turned out small enough (~190 lines) that a split felt like ceremony.

### Capture + setup scripts

- **`scripts/capture_review_fixture.py`** — single Playwright capture script across all three sites; surfaces "use CSV import" hint on block.
- **`scripts/setup_playwright.py`** — idempotent install verification (launches Chromium briefly, exits 0).

### Seeds + fixtures + tests

- `scripts/seed_sources.py` extended with `g2:asana`, `capterra:asana`, `trustpilot:example`.
- Synthetic HTML fixtures for each site + a CSV fixture for the importer.
- **24 new unit tests** (4 schema contract + 6 adapters + 11 ingester + 4 CSV-import). **140 total unit tests pass**, 0 pyright errors, ruff clean, zero `# TODO verify`.

### Risk-profile alignment

The "don't fight blocks; capture fixtures aggressively; plan to revisit" framing from feedback 014 is wired into the code: the integration test's failure message tells the operator to fall back to CSV rather than escalate to stealth tactics. The CSV importer is fully tested. Synthetic fixtures pin every selector so unit tests don't depend on the live sites cooperating.

## Container-rebuild regressions (the side issues from this session)

The Dockerfile update for playwright+chromium worked (chromium-1223 is present, `playwright --version` returns 1.60.0), but two pre-existing things broke during the rebuild:

### Regression 1 — `/workspace/.venv` root-owned named-volume mount

```
$ mount | grep .venv
/dev/md2 on /workspace/.venv type ext4 (rw,relatime,stripe=32)

$ ls -ld /workspace/.venv
drwxr-xr-x 2 root root 4096 May 17 21:15 /workspace/.venv

$ uv run python -c "import sys"
Creating virtual environment at: .venv
error: failed to open file `/workspace/.venv/CACHEDIR.TAG`: Permission denied (os error 13)
```

`.venv` is its own mounted device, root-owned and unwritable by the `node` user. Every default `uv run` invocation fails. I worked around in this session with `UV_PROJECT_ENVIRONMENT=/tmp/apfun-venv` but that's per-session and not persistent.

**Proposed fixes (you pick):**

- **(a)** Drop the `.venv` named-volume declaration in `/srv/claude/apfun.online/docker-compose.yml` if there is one. The `/workspace` bind-mount handles persistence; the `.venv`-specific named volume is extra layering that's now in the way. Cleanest.
- **(b)** Add an entrypoint shim in the Dockerfile that runs `chown -R node:node /workspace/.venv` before dropping to `USER node`. Keeps the named volume but fixes the perms.

**My lean**: (a). The bind-mount already gives the venv durable storage if uv creates it fresh.

### Regression 2 — `gh auth` credentials wiped

The rebuild also lost the `gh auth` token I'd set up earlier in the project. I needed you to re-run:

```
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git --hostname github.com
```

after the rebuild before I could push PR #6. Not a blocker now — you did the auth step — but worth knowing: future container rebuilds will lose `gh auth` again unless we either (i) persist `~/.config/gh/` via a bind/volume mount in docker-compose, or (ii) accept "re-authenticate after rebuild" as part of the rebuild ritual.

**My lean**: (ii) accept the ritual. `gh auth login` is one command, container rebuilds are rare, persisting credentials across rebuilds widens the credential blast radius. But (i) is reasonable if you'd prefer one less manual step.

## Follow-up design questions

Two genuinely worth raising now that we're done producing `raw_signals` and about to start consuming them.

### Q1 — `raw_signals` schema check before Stage 1 clustering

With five sources writing into `raw_signals` (Reddit, HN, ProductHunt, IndieHackers, the three review-site adapters), the table has heterogeneous `payload_json` shapes. Each source tags its payload differently:

| Source | Payload-tag key | What it tells you |
|---|---|---|
| Reddit | `is_deleted`, `deletion_marker` | post was removed |
| HN | `_apfun_query` | which configured query surfaced it |
| ProductHunt | `_apfun_surface` | topic vs leaderboard |
| IndieHackers | `_apfun_group`, `_apfun_url` | source group + canonical URL |
| Review sites | `site`, `product_slug`, `product_name`, `rating`, `helpful_count`, `permalink` | the whole review object |

Stage 1 clustering (task 010) will need to extract the *underlying text* (title + body) plus relevant signals (vote/comment counts, helpful_count, deletion markers) into a uniform shape. There are two ways to handle this:

- **(a)** Add a Stage-1-prep step that reads `raw_signals` and writes a normalized `signal_text` table with `(raw_signal_id, source_kind, text, weight)`. Clustering operates on `signal_text` only.
- **(b)** Have the clustering code branch on `source.kind` and pull the right fields out of each payload variant inline.

(a) is more work upfront but pays off: the normalization rules are auditable in one place, clustering stays source-agnostic, and a future re-cluster doesn't have to re-derive the text from raw payloads. (b) is faster to ship but spreads source-shape knowledge through clustering code.

**Lean (a).** Confirm or push back.

### Q2 — Helpful_count weighting in Stage 4

Task 009 spec explicitly called out `helpful_count` as the strongest "this matters" signal for review-site data. Reddit upvotes, HN points, and PH votes_count play similar roles for their respective sources. When task 014 (Stage 4 scoring) lands, we'll need a uniform "social proof weight" extracted per `raw_signal`.

Question: should the normalization step in Q1(a) above also extract this into a `signal_text.social_proof_weight` column, or wait until Stage 4 forces the question?

**Lean: yes, extract it during normalization.** Same argument as Q1(a) — source-shape knowledge lives in one place.

## Action items

For you:

1. **Container `.venv` regression** — drop the named-volume declaration in `docker-compose.yml` (or pick option (b) if you'd rather keep it). Confirm fix lands and `uv run python -c 'print(1)'` works without the env-var workaround.
2. **`gh auth` ritual** — decide between persistent credential mount vs accept-rebuild-re-auth. My lean: latter. Add a one-line note to operator docs either way.

For the orchestrator turn:

3. **Confirm/push back on Q1** (normalized `signal_text` table vs inline branching in clustering).
4. **Confirm/push back on Q2** (extract `social_proof_weight` during normalization).
5. Any concerns with the single-commit shipping shape for task 009 (vs the optional 009a/009b split)? Won't bake it into the next task unless you flag it.

## Relevant files

- PR #6 at commit `1f1d4e3` — task 009 implementation
- `apfun/sourcing/_base.py` — Protocol + BrowserBatchClient additions
- `apfun/sourcing/review_sites/` — the new package (5 files, ~700 lines)
- `scripts/import_reviews.py` — CSV fallback (~190 lines)
- `scripts/capture_review_fixture.py`, `scripts/setup_playwright.py` — operational scripts
- `tests/fixtures/review_sites/` — 4 synthetic fixtures + 1 CSV
- `docs/orchestrator/INDEX.md` — row 015 → open after this commit
