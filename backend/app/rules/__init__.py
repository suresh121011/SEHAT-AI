"""Deterministic safety rules engine (Phase 2). No LLM, network or I/O. See docs/10_Safety_Rules_Engine.md."""

from app.rules.engine import (
    ENGINE_VERSION,
    RULESET_VERSION,
    enforce_raise_only,
    evaluate_triage,
)
from app.rules.models import TriageInput, TriageResult
from app.rules.urgency import Urgency

__all__ = ["ENGINE_VERSION", "RULESET_VERSION", "TriageInput", "TriageResult", "Urgency", "enforce_raise_only", "evaluate_triage"]
