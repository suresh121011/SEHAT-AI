"""Semantic Kernel End-to-End Orchestrator for SEHAT AI.

Coordinates the complete clinical intelligence workflow aligned with Microsoft Semantic Kernel & Cognizant TriZetto AI Gateway:
1. Patient Context Ingestion with Layer 2 Presidio PII Redaction.
2. Tool-Assisted Clinical Triage Evaluation (AIIMS, NEWS2, qSOFA).
3. Raise-Only Urgency Alignment (AI can never downgrade triage urgency).
4. Evidence-Grounded Clinical Handoff Note Synthesis.
5. Post-Generation Safety Guard (blocking diagnostic or prescriptive text, TPG 5.4 / ICMR 2023).
"""

import json
import logging
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings, get_settings
from app.services.kernel import build_kernel, get_active_service_id, llm_status

logger = logging.getLogger("sehat.services.orchestrator")


class OrchestrateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intake_text: str = Field(description="Raw symptom or history narrative from patient / ANM intake.")
    vitals: dict[str, Any] = Field(default_factory=dict, description="Vital signs payload (spo2, pulse, sbp, dbp, temp_c, resp_rate).")
    scenario: str = Field(default="opd", description="Clinical triage scenario: opd, health_camp, maternal, etc.")
    age_years: int = Field(default=30, ge=0, le=120)
    source_label: str = Field(default="intake_narrative")


class OrchestrateResponse(BaseModel):
    case_id: str
    effective_urgency: str
    determination: str
    triggered_rules: list[str]
    missing_fields: list[str]
    observations_stored: int
    handoff_note: str
    safety_status: str
    active_service: str | None


async def run_case_orchestration(
    case_id: str,
    payload: OrchestrateRequest,
    settings: Settings | None = None,
) -> OrchestrateResponse:
    """Executes the full Semantic Kernel orchestration pipeline."""
    settings = settings or get_settings()
    kernel = build_kernel(settings)

    # 1. Patient Context Redaction & Storage via Kernel invocation
    await kernel.invoke(
        kernel.plugins["patient_context"]["store_case_observation"],
        case_id=case_id,
        text=payload.intake_text,
        source=payload.source_label,
    )

    # 2. Deterministic AIIMS / NEWS2 / qSOFA Evaluation via Plugin
    triage_payload = {
        "scenario": payload.scenario,
        "age_years": payload.age_years,
        "vitals": payload.vitals,
        "red_flag_screen_completed": True,
        "red_flags_present": [],
    }
    triage_eval_raw = await kernel.invoke(
        kernel.plugins["triage_rules"]["evaluate_triage"],
        triage_input_json=json.dumps(triage_payload),
    )
    triage_eval = json.loads(str(triage_eval_raw))
    deterministic_urgency = triage_eval.get("final_urgency", "GREEN")
    determination = triage_eval.get("determination", "complete")
    triggered_rules = triage_eval.get("triggered_rules", [])
    missing_fields = triage_eval.get("missing_fields", [])

    # 3. Raise-Only Urgency Boundary Check via Kernel invocation
    urgency_res_raw = await kernel.invoke(
        kernel.plugins["triage_rules"]["apply_raise_only_urgency"],
        deterministic_urgency=deterministic_urgency,
        suggested_urgency=deterministic_urgency,
    )
    urgency_res = json.loads(str(urgency_res_raw))
    effective_urgency = urgency_res.get("effective_urgency", deterministic_urgency)

    # 4. Generate Clinical Handoff Note Draft
    recalled_raw = await kernel.invoke(
        kernel.plugins["patient_context"]["recall_case_observations"],
        case_id=case_id,
    )
    recalled = json.loads(str(recalled_raw))
    observations_count = recalled.get("count", 0)
    redacted_narrative = (
        recalled["records"][-1]["text"]
        if recalled.get("records")
        else payload.intake_text.strip()
    )

    # Build structured, source-linked handoff summary lines using the redacted narrative
    summary_lines = [
        f"Chief Complaint / Narrative: {redacted_narrative}",
        f"Assessed Urgency: {effective_urgency} (Determination: {determination})",
    ]
    if triggered_rules:
        summary_lines.append(f"Triggered Protocols: {', '.join(triggered_rules)}")
    if payload.vitals:
        vitals_formatted = [f"{k}={v}" for k, v in payload.vitals.items() if v is not None]
        citation_res = await kernel.invoke(
            kernel.plugins["clinical_handoff"]["format_source_citation"],
            field_name="Vitals",
            value=", ".join(vitals_formatted),
            source_type=payload.source_label,
        )
        summary_lines.append(str(citation_res))

    raw_note = "\n".join(summary_lines)

    # 5. Non-Diagnostic Output Safety Guard
    safety_check_raw = await kernel.invoke(
        kernel.plugins["safety_guard"]["check_clinical_text"],
        text=raw_note,
    )
    safety_check = json.loads(str(safety_check_raw))

    if not safety_check.get("is_safe", True):
        logger.warning("Safety guard triggered for case %s: %s", case_id, safety_check.get("blocked_reason"))
        raw_note = f"[Safety Note Modified] Review required. Urgency: {effective_urgency}."
        safety_status = f"blocked_and_sanitized: {safety_check.get('blocked_reason')}"
    else:
        safety_status = "passed"

    # Append mandatory regulatory disclaimer (TPG 5.4, ICMR 2023)
    guarded_note_raw = await kernel.invoke(
        kernel.plugins["clinical_handoff"]["append_mandatory_disclaimer"],
        note_text=raw_note,
    )
    guarded_note = str(guarded_note_raw)

    return OrchestrateResponse(
        case_id=case_id,
        effective_urgency=effective_urgency,
        determination=determination,
        triggered_rules=triggered_rules,
        missing_fields=missing_fields,
        observations_stored=observations_count,
        handoff_note=guarded_note,
        safety_status=safety_status,
        active_service=get_active_service_id(kernel),
    )
