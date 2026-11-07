"""Semantic Kernel plugins for SEHAT AI (Microsoft Semantic Kernel & Cognizant TriZetto AI Gateway alignment).

Zero-conflict plugins exposed to Semantic Kernel agents:
1. `TriageRulesPlugin`: Executes deterministic AIIMS, NEWS2, and qSOFA rules; enforces raise-only urgency.
2. `ClinicalHandoffPlugin`: Formats grounded, source-linked handoff notes with mandatory disclaimers.
3. PatientContextPlugin: Safely retrieves and stores case context and intake extractions with Presidio PII redaction.
4. `SafetyGuardPlugin`: Validates generated text against non-diagnostic, non-prescriptive rules (TPG 5.4, ICMR 2023).
"""

import json
from typing import Any

from semantic_kernel.functions import kernel_function

from app.ai import guard
from app.privacy.pii import redact
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
            return json.dumps({"error": str(exc), "status": "evaluation_failed"})

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
            return json.dumps({"error": str(exc), "status": "raise_only_failed"})


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


class PatientContextPlugin:
    """In-memory case context and history plugin with strict case isolation and Presidio PII redaction."""

    def __init__(self) -> None:
        self._case_records: dict[str, list[dict[str, Any]]] = {}

    @kernel_function(
        name="store_case_observation",
        description="Stores an intake observation (symptom, vital, voice extract) under a specific case_id after stripping Indian PII."
    )
    def store_case_observation(self, case_id: str, text: str, source: str) -> str:
        try:
            # Presidio Indian PII redaction gate (Aadhaar, ABHA, Phone, Name)
            redacted = redact(text).text
            record = {
                "source": source,
                "text": redacted,
            }
            if case_id not in self._case_records:
                self._case_records[case_id] = []
            self._case_records[case_id].append(record)
            return json.dumps({"status": "stored", "case_id": case_id, "redacted_length": len(redacted)})
        except Exception as exc:
            return json.dumps({"error": str(exc), "status": "storage_failed"})

    @kernel_function(
        name="recall_case_observations",
        description="Recalls stored observations for a case_id. Enforces zero cross-patient data bleed."
    )
    def recall_case_observations(self, case_id: str) -> str:
        records = self._case_records.get(case_id, [])
        return json.dumps({"case_id": case_id, "count": len(records), "records": records})


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
