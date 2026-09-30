"""Privacy boundary (Phase 3): normalization, PII redaction, LLM adapter contract, AI-assist gateway.

`gateway.submit_for_ai_assist` is the only approved path from case text to an LLM adapter.
Redaction is a heuristic risk-reduction control, not anonymization (docs/11).
"""
