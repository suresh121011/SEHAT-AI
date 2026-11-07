"""Unit and integration tests for Microsoft Semantic Kernel and Clinical Plugins."""

import dataclasses
import json
import pytest

from app.config import get_settings
from app.services.kernel import build_kernel, llm_status, get_active_service_id, GEMINI_SERVICE_ID
from app.services.plugins import (
    ClinicalHandoffPlugin,
    PatientContextPlugin,
    SafetyGuardPlugin,
    SehatPlugin,
    TriageRulesPlugin,
)


def test_build_kernel_unconfigured_loads_all_plugins():
    base = get_settings()
    settings = dataclasses.replace(
        base,
        azure_openai_api_key="",
        azure_openai_endpoint="",
        azure_openai_deployment_name="",
        google_ai_api_key="",
    )
    kernel = build_kernel(settings)
    assert llm_status(kernel) == "not_configured"
    assert get_active_service_id(kernel) is None

    expected_plugins = {"sehat", "triage_rules", "clinical_handoff", "patient_context", "safety_guard"}
    assert expected_plugins.issubset(set(kernel.plugins.keys()))


def test_build_kernel_with_gemini_key():
    base = get_settings()
    settings = dataclasses.replace(
        base,
        azure_openai_api_key="",
        azure_openai_endpoint="",
        azure_openai_deployment_name="",
        google_ai_api_key="valid_dummy_gemini_api_key",
    )
    kernel = build_kernel(settings)
    assert llm_status(kernel) == "configured"
    assert get_active_service_id(kernel) == GEMINI_SERVICE_ID
    assert kernel.get_service(GEMINI_SERVICE_ID) is not None


def test_triage_rules_plugin_evaluation():
    plugin = TriageRulesPlugin()
    vitals_payload = json.dumps({
        "scenario": "opd",
        "age_years": 35,
        "vitals": {
            "spo2": 88,  # Triggers critical hypoxia rule -> RED
            "pulse": 90,
            "sbp": 120,
            "dbp": 80,
            "temp_c": 37.0,
            "resp_rate": 20,
        },
        "red_flag_screen_completed": True,
        "red_flags_present": [],
    })
    res_raw = plugin.evaluate_triage(vitals_payload)
    res = json.loads(res_raw)

    assert "final_urgency" in res
    assert res["final_urgency"] == "RED"
    assert len(res["triggered_rules"]) > 0


def test_triage_rules_plugin_raise_only():
    plugin = TriageRulesPlugin()

    # GREEN -> RED should raise
    raised_raw = plugin.apply_raise_only_urgency("GREEN", "RED")
    raised = json.loads(raised_raw)
    assert raised["effective_urgency"] == "RED"
    assert raised["was_raised"] is True
    assert raised["downgrade_refused"] is False

    # RED -> GREEN cannot lower
    lowered_raw = plugin.apply_raise_only_urgency("RED", "GREEN")
    lowered = json.loads(lowered_raw)
    assert lowered["effective_urgency"] == "RED"
    assert lowered["was_raised"] is False
    assert lowered["downgrade_refused"] is True


def test_patient_context_plugin_pii_redaction_and_isolation():
    plugin = PatientContextPlugin()

    case_1 = "case_patient_001"
    case_2 = "case_patient_002"

    raw_text_1 = "Patient Rajesh Kumar, Aadhaar 2345 6789 0123, phone 9876543210 complains of severe chest pain."
    store_res = json.loads(plugin.store_case_observation(case_1, raw_text_1, "voice_intake"))
    assert store_res["status"] == "stored"

    raw_text_2 = "Patient Sunita Devi has fever for 3 days."
    plugin.store_case_observation(case_2, raw_text_2, "ocr_rx")

    # Recall case 1: must redact PII and have exactly 1 record
    recalled_1 = json.loads(plugin.recall_case_observations(case_1))
    assert recalled_1["count"] == 1
    obs_1 = recalled_1["records"][0]["text"]
    assert "2345 6789 0123" not in obs_1
    assert "9876543210" not in obs_1
    assert "[AADHAAR_REDACTED]" in obs_1 or "[PHONE_REDACTED]" in obs_1

    # Recall case 2: cross-case isolation verified
    recalled_2 = json.loads(plugin.recall_case_observations(case_2))
    assert recalled_2["count"] == 1
    assert "Sunita" not in recalled_2["records"][0]["text"] or "[PERSON_REDACTED]" in recalled_2["records"][0]["text"]


def test_clinical_handoff_plugin_disclaimer_and_citation():
    plugin = ClinicalHandoffPlugin()

    citation = plugin.format_source_citation("SpO2", "94%", "voice", "seg_01")
    assert citation == "SpO2: 94% [src:voice#seg_01]"

    note = "Patient presented with mild respiratory symptoms."
    guarded_note = plugin.append_mandatory_disclaimer(note)
    assert "Not a diagnosis" in guarded_note
    assert "Research prototype" in guarded_note


def test_safety_guard_plugin():
    guard_plugin = SafetyGuardPlugin()

    safe_text = "Patient reports mild cough and headache."
    safe_check = json.loads(guard_plugin.check_clinical_text(safe_text))
    assert safe_check["is_safe"] is True
    assert safe_check["blocked_reason"] is None

    # Diagnostic claim should be caught
    diagnostic_text = "Diagnosis: Confirmed case of dengue fever."
    diag_check = json.loads(guard_plugin.check_clinical_text(diagnostic_text))
    assert diag_check["is_safe"] is False
    assert diag_check["blocked_reason"] == "diagnostic_language"

    # Prescription claim should be caught
    rx_text = "Prescribe Paracetamol 500 mg tablets twice daily."
    rx_check = json.loads(guard_plugin.check_clinical_text(rx_text))
    assert rx_check["is_safe"] is False
    assert rx_check["blocked_reason"] == "prescriptive_language"
