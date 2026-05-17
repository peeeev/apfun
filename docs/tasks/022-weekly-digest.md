# 022 — Weekly digest email

**Goal:** Mondays 9am UTC, email the top 5 active opportunities by composite score from the last 7 days.

Depends on: 020.

## Deliverables
- Pick provider at task-start: Postmark (preferred for transactional + good deliverability) unless human directs otherwise. Mailgun and SES are the alternatives.
- Dep: `httpx` already in; no SDK needed — Postmark API is a simple POST.
- `apfun/digest/weekly.py`:
  - Query top 5 `opportunities` with `synthesized_at >= now - 7d` and `status='active'`, ordered by `composite DESC`.
  - Render HTML + plaintext via Jinja templates (`templates/digest/weekly.html`, `weekly.txt`).
  - Send via Postmark to `APFUN_DIGEST_TO` from `APFUN_DIGEST_FROM`.
- Register the APScheduler job in `apfun/scheduler/jobs.py`: Mondays 09:00 UTC.
- If 0 opportunities surfaced this week, still send a short "nothing this week" note so the human knows the system is alive.

## Acceptance
- Unit test renders the template with three fake opportunities and asserts key fields appear.
- Integration test (opt-in, requires `APFUN_POSTMARK_TOKEN`) sends a real email to a test inbox.
- `scripts/digest_preview.py` writes the HTML to a tmp file and prints the path — for previewing without sending.

## Notes
- One email per week. No daily digests. Resist scope creep here.
- If Postmark setup turns out to need DNS work (DKIM/SPF), document it and ship a no-op stub until the human completes setup.
