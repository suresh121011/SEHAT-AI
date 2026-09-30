"""The Odisha demo (docs/07) must match what the engine actually returns.

Synthetic teaching case: 28 y, PHC Khurda, fever 3 days (38.9 °C), headache, severe abdominal pain.
Platelets (85K on the lab report) are clinician context only; no rule reads them (docs/10 ADR-7).
"""

import re
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.rules import TriageInput
from tests.conftest import login
from tests.rules.vignettes import rule_ids, run

DEMO = {
    "scenario": "opd",
    "age_years": 28,
    "red_flag_screen_completed": True,
    "red_flags_present": ["severe_pain"],
    "suspected_infection": True,
    "vitals": {
        "resp_rate": 20,
        "spo2": 97,
        "on_supplemental_oxygen": False,
        "pulse": 96,
        "sbp": 118,
        "dbp": 76,
        "temp_c": 38.9,
        "consciousness": "A",
    },
}


def demo(**changes):
    data = deepcopy(DEMO)
    data.update(changes)
    return data


def test_demo_patient_is_red_via_atp_severe_pain_only():
    r = run(DEMO)
    assert r.urgency.value == "RED"
    assert r.determination == "complete"
    assert rule_ids(r) == {"ATP_RED_SEVERE_PAIN"}
    hit = r.triggered_rules[0]
    assert hit.source_id == "ATP_2022"
    assert hit.evidence["red_flag"].value == "severe_pain"
    # scores shown in Beat 4 but not driving the decision
    assert (r.scores.news2.total, r.scores.news2.band) == (2, "low")
    assert r.scores.qsofa.positive is False


def test_demo_fever_alone_is_not_atp_red():
    # 102 °F = 38.9 °C is not above the ATP >39 °C threshold (Beat 2 note)
    assert "ATP_RED_FEVER_39" not in rule_ids(run(DEMO))


def test_demo_safety_floor_variant_is_yellow_with_review():
    r = run(demo(red_flag_screen_completed=False, red_flags_present=[]))
    assert r.urgency.value == "YELLOW"
    assert r.needs_human_review is True
    assert "SAFETY_FLOOR_INSUFFICIENT_DATA" in rule_ids(r)


def test_platelets_are_not_an_engine_input():
    with pytest.raises(ValidationError):
        TriageInput(**demo(platelets=85000))


def test_platelets_rejected_by_api(client):
    token = login(client, "anm_demo", "anm")
    resp = client.post("/api/v1/triage/process", json=demo(platelets=85000), headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_demo_via_api_matches_engine(client):
    token = login(client, "mo_demo", "medical_officer")
    body = client.post("/api/v1/triage/process", json=DEMO, headers={"Authorization": f"Bearer {token}"}).json()
    assert body["urgency"] == "RED"
    assert [t["rule_id"] for t in body["triggered_rules"]] == ["ATP_RED_SEVERE_PAIN"]
    assert body["triggered_rules"][0]["source_id"] == "ATP_2022"


def test_known_limitation_unrated_abdominal_pain_is_green():
    """Pins the documented dengue gap (docs/07 'Known limitation', docs/10 §9). When a verified
    dengue warning-sign pack is added this test should fail: update it and the demo docs together."""
    r = run(demo(red_flags_present=[]))
    assert r.urgency.value == "GREEN"


# ── Docs must not teach a platelet -> urgency rule ─────────────────────

DOCS = Path(__file__).resolve().parents[3] / "docs"
STALE = re.compile(r"platelets?\s*(<|≤|>)\s*100\s*,?\s*(k|000)|dengue warning signs.*(rule|red)", re.IGNORECASE)
DISCLAIMED = re.compile(r"not implemented|mis-cited|not supported|deferred|not a rule|not part|adr-7|historical", re.IGNORECASE)


def test_docs_have_no_active_platelet_urgency_claim():
    # Only the historical `DENGUE_RULES = [ ... ]` block in the architecture doc is exempt, and only while
    # its opening line carries a disclaimer. Removing the disclaimer (or renaming the block) makes its
    # lines count again, so this check fails loud rather than silently passing.
    offenders = []
    for path in sorted(DOCS.glob("*.md")):
        lines = path.read_text().splitlines()
        in_disclaimed_block = False
        for n, line in enumerate(lines, 1):
            if "DENGUE_RULES = [" in line:
                in_disclaimed_block = DISCLAIMED.search(line) is not None
            elif in_disclaimed_block and line.strip() == "]":
                in_disclaimed_block = False
                continue
            if STALE.search(line) and not DISCLAIMED.search(line) and not in_disclaimed_block:
                offenders.append(f"{path.name}:{n}: {line.strip()[:100]}")
    assert offenders == []
