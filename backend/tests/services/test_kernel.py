"""Semantic Kernel smoke test (services/kernel.py, decision D6) and the plugin TEST HARNESS (services/plugins.py).

Contributed in 69071c4 / 21a9761 by niranjanswain067; adapted in the remediation (docs/integration/
semantic-kernel-remediation-plan.md): the runtime kernel registers only `SehatPlugin` and no cloud chat service; the
other plugins are exercised here as typed wrappers over the deterministic rules engine and the output guard."""

import dataclasses
import json
import pytest

from app.config import get_settings
from app.services.kernel import build_kernel, llm_status
from app.services.plugins import (
    ClinicalHandoffPlugin,
    SafetyGuardPlugin,
    SehatPlugin,
    TriageRulesPlugin,
)




def test_runtime_kernel_is_a_smoke_test_with_only_the_sehat_plugin_and_no_cloud_service():
    base = get_settings()
    settings = dataclasses.replace(base, azure_openai_api_key="", azure_openai_endpoint="", azure_openai_deployment_name="",
                                   google_ai_api_key="test-key-not-real")
    kernel = build_kernel(settings)
    assert set(kernel.plugins.keys()) == {"sehat"}
    assert llm_status(kernel) == "not_configured"  # a Google key alone registers nothing


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



