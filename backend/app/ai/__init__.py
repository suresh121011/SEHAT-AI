"""Phase 6 AI extraction layer (docs/16). The LLM only extracts and summarises; it never sets urgency.

Model output is untrusted: it is schema-validated, grounded against the redacted source text, voted
across passes (MAKER) and reviewed field by field by a human. Nothing here writes `triage_runs`.
"""
