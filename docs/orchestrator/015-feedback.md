# Feedback 015 — container regressions + signal normalization design

**Date:** 2026-05-19
**Request:** 015-task-009-report-and-container-followup.md
**Outcome:** Two operational fixes (container `.venv` perms, gh-auth ritual). Confirmed both design Q1 and Q2 — adds new task 010a (signal_text normalization) ahead of clustering proper. Task 009 single-commit ship was the right call.

## Container regressions

### Regression 1 — `.venv` permissions: drop the named volume

**Option (a) — remove the `.venv` named-volume declaration.** Your reasoning is correct: the bind-mount of `./workspace` → `/workspace` already gives the venv durable storage; a separate named volume for `.venv` is over-layered and reliably produces the root-owned-mount-point footgun whenever container UIDs and named-volume creation interact.

Update `/srv/claude/apfun.online/docker-compose.yml`:

```yaml
volumes:
  - ./workspace:/workspace
  - claude-config:/home/node/.claude
  - claude-state:/home/node/.local
  # REMOVE if present:
  # - funnel-venv:/workspace/.venv
```

Also remove `funnel-venv` from the top-level `volumes:` declaration.

On the host:

```bash
cd /srv/claude/apfun.online
# Edit docker-compose.yml per above
docker compose down
docker volume rm apfunonline_funnel-venv   # remove orphaned volume
docker compose up -d
docker exec -it apfun-funnel bash
cd /workspace
uv run python -c "print(1)"   # should create .venv as node user
ls -ld .venv                  # node:node ownership
```

Option (b) — entrypoint chown shim — would work but papers over the underlying issue. The named volume isn't earning its keep.

### Regression 2 — `gh auth`: accept the ritual

Confirm option (ii). `gh auth login` is one command; container rebuilds are rare; persisting auth credentials across rebuilds widens the credential blast radius. Re-authenticating is the right operational shape.

Add a post-rebuild checklist to `/srv/claude/apfun.online/README.md`:

```markdown
## After container rebuild

- `gh auth login --hostname github.com --git-protocol https --web` then `gh auth setup-git`
- Verify Claude Code login persists (no welcome screen) — credentials live in
  the claude-config Docker volume, which survives rebuilds
- Verify uv works as node user: `cd /workspace && uv run python -c "print(1)"`
```

Three items, runs in < 2 minutes when needed.

## Task 009 single-commit shipping: was the right call

No flag. The 009a/009b split was offered as optionality for when reviewability was at risk; at ~190 lines, the CSV path didn't warrant the ceremony. **Don't bake the split into future tasks as a rule** — it's a tool for when PRs grow large, not a default discipline.

The meta-pattern worth internalizing: *offer optionality in feedback when uncertain; let the implementer use judgment on whether to take it.* You considered and rejected the split, and noted the rejection — that's exactly the right behavior.

## Design questions

### Q1 — Normalized `signal_text` table: confirmed (a)

Your reasoning is correct on all three counts (auditability, source-agnostic clustering, re-cluster-without-re-deriving). One refinement:

**The normalization step is an ETL stage, not a database trigger.** Implement as an idempotent function reading from `raw_signals` and writing/updating `signal_text` rows. Run on a schedule (or after each ingester batch), tracked in `scheduler_runs` like any other pipeline stage. **Don't tempt yourself with SQLAlchemy event listeners or post-insert hooks** — those couple the ingester to clustering's needs and make schema changes harder.

Schema sketch:

```python
class SignalText(Base):
    __tablename__ = "signal_text"
    id = Column(Integer, primary_key=True)
    raw_signal_id = Column(Integer, ForeignKey("raw_signals.id"), unique=True, nullable=False)
    source_kind = Column(String, nullable=False)        # 'reddit', 'hn', 'ph', 'ih', 'review'
    text = Column(Text, nullable=False)                 # combined title + body, normalized
    social_proof_weight = Column(Float, nullable=False) # see Q2
    is_low_signal = Column(Boolean, nullable=False, default=False)   # e.g. Reddit [deleted]
    extracted_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (Index("ix_signal_text_source_kind", "source_kind"),)
```

`unique=True` on `raw_signal_id` → re-running the normalizer updates instead of duplicates. `extracted_at` lets you spot stale rows when re-normalizing a slice.

**Open this as a new task: `010a-signal-text-normalization.md`.** Splits cleanly from clustering itself (task 010): upstream is data shape, downstream is the clustering algorithm. Cleaner PRs, separable test scope, the schema work doesn't get tangled with LLM-based clustering decisions.

### Q2 — `social_proof_weight` extracted during normalization: confirmed yes

Same argument as Q1 — source-shape knowledge in one place.

Starting weight map (tune in task 010a, refine after Stage 4 has data):

```python
def social_proof_weight(source_kind: str, payload: dict) -> float:
    """Normalize per-source social signals into a comparable scalar.

    Non-negative float. Higher = more attention this signal has received.
    Caller should use this multiplicatively with content quality, not as a sole ranker.
    """
    if source_kind == "reddit":
        score = payload.get("score", 0)
        num_comments = payload.get("num_comments", 0)
        return float(max(score, 0) + 2 * num_comments)
    if source_kind == "hn":
        return float(payload.get("points", 0) + 2 * payload.get("num_comments", 0))
    if source_kind == "ph":
        return float(payload.get("votes_count", 0))
    if source_kind == "ih":
        return float(payload.get("likes", 0) + 2 * payload.get("comments_count", 0))
    if source_kind == "review":
        return float(payload.get("helpful_count", 0))
    return 0.0
```

Constants (`2 *` weights, integer maps) carry `# heuristic 2026-05-19 — initial weights, to be tuned via Stage 4 calibration data`. The retune-trigger discipline from feedback 005 applies: flag for review when Stage 4 has enough data to inform the tuning.

**Important property:** keep raw weighted values; **don't normalize to [0,1] at this stage**. Bounded-range normalization loses magnitude information; Stage 4 (saturation scoring) is where the right bucketing happens with full context.

## Aside on container hygiene

The `.venv` regression is part of a useful pattern worth being deliberate about:

> **Named volumes are for state you want to survive container destruction; bind mounts are for state you want to edit from the host.**
>
> Things that should NOT be in named volumes:
> - State derived from project files (`.venv`, `__pycache__`, build artifacts) — get recreated, no need to persist.
> - State that should be regenerated on rebuild for security reasons (`gh auth`, anything secret-shaped that rotates).
>
> The credential volumes `claude-config` and `claude-state` are correct — Claude Code login isn't derived from project files, and persistence saves real time.

Recovery shortcut if `.venv` ever gets wrong ownership again: `rm -rf .venv && uv sync`. 5-second fix.

## Action items

### For you (operator)

1. Edit `docker-compose.yml` — remove `.venv` named-volume mount + top-level declaration.
2. `docker compose down && docker volume rm apfunonline_funnel-venv && docker compose up -d`.
3. Verify: `docker exec -it apfun-funnel bash`, then in `/workspace`, `uv run python -c "print(1)"` succeeds without the `UV_PROJECT_ENVIRONMENT` workaround.
4. Add the 3-item post-rebuild checklist to `/srv/claude/apfun.online/README.md`.
5. **(Optional but recommended)** Pin Claude Code version in the Dockerfile (`npm install -g @anthropic-ai/claude-code@2.1.145`). Prevents future rebuild surprises.

### For Claude Code

6. **Open task 010a: signal text normalization** with the SignalText schema and `social_proof_weight` function as the starting design. Spec, then implement, ahead of task 010 clustering.
7. **Idempotency check:** `signal_text` writes must be re-run-safe (update on existing `raw_signal_id`, not duplicate insert). Cover this with an explicit test.
8. **Tag `# heuristic` weights** with the retune trigger language so they show up in future audits.

## Next step

Container fixes → task 010a (normalization) → task 010 (clustering). Task 010 was originally specced; the normalization split is a clean addition, not a re-scope.

## Meta note

The pattern of task 009 — a high-risk task that landed cleanly because the spec absorbed all the design surprises before implementation — validates the orchestrator turn discipline. The most important moments in this loop have been pre-implementation, not post-PR-review. Keep this shape going into Stages 1-5; the LLM-heavy stages will have more genuine design surprises than the ingester pattern did.
