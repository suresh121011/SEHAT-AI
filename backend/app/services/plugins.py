"""Semantic Kernel plugins for SEHAT AI (Microsoft Semantic Kernel; Cognizant TriZetto AI Gateway alignment).

`SehatPlugin` is the only plugin registered at runtime (`services/kernel.py`, a smoke test; decision D6). The others,
contributed in 69071c4, are a TEST AND EVALUATION HARNESS only (remediation plan §4): typed wrappers over the
deterministic rules engine and the output guard, exercised by tests/services/test_kernel.py and
tests/privacy/test_remediation_guards.py. They are never registered on the application kernel, never driven by an
agent at runtime, and never set urgency: the rules engine via POST /cases/{id}/triage stays the only authority.
1. `TriageRulesPlugin`: runs the deterministic AIIMS / NEWS2 / qSOFA engine; raise-only merge of a suggestion.
2. `ClinicalHandoffPlugin`: source-citation tag and the mandatory non-diagnostic disclaimer.
3. `SafetyGuardPlugin`: the non-diagnostic / non-prescriptive output guard (TPG 5.4, ICMR 2023).
Failures return a reason code only, never exception text (it can echo input), and never a default urgency.
"""

import json

from semantic_kernel.functions import kernel_function

from app.ai import guard
from app.rules import TriageInput, Urgency
from app.rules.engine import evaluate_triage as run_triage_engine
from app.rules.urgency import max_urgency


class SehatPlugin:
    """Core SEHAT status plugin."""

    @kernel_function(name="plugin_status", description="Reports that the SEHAT plugin is registered.")
    def plugin_status(self) -> str:
        return "sehat plugin registered"


class TriageRulesPlugin:
    """Deterministic clinical triage plugin.

    Invariants:
    - AIIMS, NEWS2, and qSOFA rules are purely deterministic.
    - Urgency cannot be lowered by LLMs; enforce_raise_only protects patient safety.
    """

    @kernel_function(
        name="evaluate_triage",
        description="Calculates deterministic urgency (RED/YELLOW/GREEN) from patient vitals and symptoms using AIIMS protocols."
    )
    def evaluate_triage(self, triage_input_json: str) -> str:
        try:
            data = json.loads(triage_input_json)
            t_input = TriageInput.model_validate(data)
            result = run_triage_engine(t_input)
            return json.dumps({
                "final_urgency": result.urgency.value,
                "urgency_label": result.urgency.value,
                "determination": result.determination,
                "triggered_rules": [r.rule_id for r in result.triggered_rules],
                "missing_fields": result.missing_fields,
                "scores": {
                    "news2": result.scores.news2.model_dump() if result.scores and result.scores.news2 else None,
                    "qsofa": result.scores.qsofa.model_dump() if result.scores and result.scores.qsofa else None,
                }
            })
        except Exception as exc:
            return json.dumps({"status": "evaluation_failed", "reason": type(exc).__name__})  # never an urgency, never exception text

    @kernel_function(
        name="apply_raise_only_urgency",
        description="Safely raises triage urgency if an LLM suggestion is higher than deterministic rule output. Cannot lower urgency."
    )
    def apply_raise_only_urgency(self, deterministic_urgency: str, suggested_urgency: str) -> str:
        try:
            det = Urgency(deterministic_urgency.strip().upper())
            sug = Urgency(suggested_urgency.strip().upper()) if suggested_urgency else None
            effective = max_urgency(det, sug)
            was_raised = sug is not None and sug.rank > det.rank
            downgrade_refused = sug is not None and sug.rank < det.rank
            return json.dumps({
                "effective_urgency": effective.value,
                "was_raised": was_raised,
                "downgrade_refused": downgrade_refused,
                "deterministic": det.value,
                "suggested": sug.value if sug else None,
            })
        except Exception as exc:
            return json.dumps({"status": "raise_only_failed", "reason": type(exc).__name__})


class ClinicalHandoffPlugin:
    """Clinical handoff note summarization plugin with source citation and non-diagnostic guard."""

    @kernel_function(
        name="format_source_citation",
        description="Formats a verifiable clinical source citation tag for audit tracking."
    )
    def format_source_citation(self, field_name: str, value: str, source_type: str, segment_id: str = "") -> str:
        seg_str = f"#{segment_id}" if segment_id else ""
        return f"{field_name}: {value} [src:{source_type}{seg_str}]"

    @kernel_function(
        name="append_mandatory_disclaimer",
        description="Appends the ICMR 2023 & TPG 5.4 mandated non-diagnostic research prototype disclaimer."
    )
    def append_mandatory_disclaimer(self, note_text: str) -> str:
        disclaimer = (
            "\n\n---\n"
            "AI-drafted, pending review by a qualified clinician. "
            "Not a diagnosis. Research prototype, not a clinically validated device."
        )
        return f"{note_text.strip()}{disclaimer}"



class SafetyGuardPlugin:
    """Validates LLM outputs against non-diagnostic and non-prescriptive rules."""

    @kernel_function(
        name="check_clinical_text",
        description="Checks if text violates non-diagnostic, prescriptive, or injection safety rules."
    )
    def check_clinical_text(self, text: str) -> str:
        blocked_reason = guard.check(text)
        return json.dumps({
            "is_safe": blocked_reason is None,
            "blocked_reason": blocked_reason,
        })
