# 022 — Weekly digest email

**Goal:** Mondays 9am UTC, email the top 5 active opportunities by composite score from the last 7 days. Default provider: **Resend**.

**Complexity:** S

Depends on: 020.

## Deliverables
- Provider: Resend (free tier covers ~3000 emails/month, well above our ~4/month). Mailgun/SES/Postmark stay as alternatives if Resend ever fails verification.
- Dep: `httpx` already in; no SDK needed — Resend is one POST: `POST https://api.resend.com/emails`, bearer auth.
- `apfun/digest/weekly.py`:
  - Query top 5 `opportunities` with `synthesized_at >= now - 7d` and `status='active'`, ordered by `composite DESC`.
  - Render HTML + plaintext via Jinja templates (`templates/digest/weekly.html`, `weekly.txt`).
  - POST to Resend with `from=APFUN_DIGEST_FROM`, `to=APFUN_DIGEST_TO`, subject, html, text. Bearer token from `APFUN_RESEND_API_KEY`.
- Register the APScheduler job in `apfun/scheduler/jobs.py`: Mondays 09:00 UTC.
- If 0 opportunities surfaced this week, still send a short "nothing this week" note so the human knows the system is alive.

## Acceptance
- Unit test renders the template with three fake opportunities and asserts key fields appear.
- Integration test (opt-in, requires `APFUN_RESEND_API_KEY`) sends a real email to a test inbox.
- `scripts/digest_preview.py` writes the HTML to a tmp file and prints the path — for previewing without sending.

## Notes
- One email per week. No daily digests. Resist scope creep here.
- Resend requires the sender domain to be verified (SPF + DKIM via Cloudflare DNS). If DNS isn't set up yet, ship a no-op stub that writes the rendered email to `data/digests/YYYY-MM-DD.html` and document the DNS records the human needs to add.
