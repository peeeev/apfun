"""Pipeline stages between ingest and HITL/scoring.

`normalize` is Stage 0 — projects per-source `raw_signals` payloads into a
uniform `signal_text` table that Stage 1 clustering (and beyond) consume.
Source-shape knowledge stays here so downstream stages don't branch on
`source.kind`.
"""
