"""FHIR R4 export of a referral (docs/06 §4, architecture §16 "fhir_bundle"). Built only from the stored referral and
its signed-off triage run. These tests check structure (a document Bundle, references that resolve, LOINC and UCUM
codes, no patient name); the bundle has not been run through an official FHIR validator. Synthetic data only."""

import json

from tests.privacy.helpers import audit_rows, auth
from tests.referral_notes.test_referral_api import API, YELLOW, move, referred
from tests.review.test_review_api import anm, mo  # noqa: F401  (fixtures)

XHTML = '<div xmlns="http://www.w3.org/1999/xhtml">'


def export(client, token, cid, rid):
    return client.get(f"{API}/cases/{cid}/referrals/{rid}/fhir", headers=auth(token))


def bundle(client, anm, mo, inputs=None):
    cid, run_id, ref = referred(client, anm, mo) if inputs is None else referred(client, anm, mo, inputs)
    r = export(client, mo, cid, ref["referral_id"])
    assert r.status_code == 200, r.text
    return cid, ref, r.json()


def resources(b: dict, kind: str) -> list[dict]:
    return [e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == kind]


def references(node) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "reference":
                found.add(v)
            else:
                found |= references(v)
    elif isinstance(node, list):
        for v in node:
            found |= references(v)
    return found


def test_bundle_is_a_document_with_the_referral_note_first(client, anm, mo):
    _, ref, b = bundle(client, anm, mo)
    assert b["resourceType"] == "Bundle" and b["type"] == "document" and b["timestamp"]
    assert b["identifier"] == {"system": "urn:sehat:referral", "value": ref["referral_id"]}
    comp = b["entry"][0]["resource"]
    assert comp["resourceType"] == "Composition" and comp["status"] == "final" and comp["title"] == "SEHAT AI referral note"
    assert comp["type"]["coding"][0] == {"system": "http://loinc.org", "code": "57133-1", "display": "Referral note"}
    assert comp["attester"][0]["mode"] == "professional" and comp["attester"][0]["party"]["display"] == "Medical officer (signed off in SEHAT AI)"
    kinds = sorted(e["resource"]["resourceType"] for e in b["entry"])
    assert kinds == sorted(["Composition", "Patient", "Organization", "Organization", "Encounter", "ServiceRequest",
                            "RiskAssessment", "Observation", "Observation", "Observation", "Observation", "Observation"])


def test_every_reference_resolves_inside_the_bundle(client, anm, mo):
    _, _, b = bundle(client, anm, mo)
    full_urls = [e["fullUrl"] for e in b["entry"]]
    assert len(full_urls) == len(set(full_urls)) and all(u.startswith("urn:uuid:") for u in full_urls)
    refs = references(b)
    assert refs and refs <= set(full_urls)


def test_patient_resource_carries_only_the_token(client, anm, mo):
    _, ref, b = bundle(client, anm, mo)
    (patient,) = resources(b, "Patient")
    assert set(patient) == {"resourceType", "id", "identifier"}
    assert patient["identifier"] == [{"system": "urn:sehat:patient-token", "value": ref["patient_token"]}]


def test_request_and_risk_follow_the_rules_engine(client, anm, mo):
    cid, ref, b = bundle(client, anm, mo)
    (sr,) = resources(b, "ServiceRequest")
    assert sr["status"] == "active" and sr["intent"] == "order" and sr["priority"] == "stat"
    assert sr["category"][0]["coding"][0] == {"system": "http://snomed.info/sct", "code": "3457005", "display": "Patient referral"}
    to_org = next(o for o in resources(b, "Organization") if o["identifier"][0]["value"] == "DH-BBSR-01")
    to_url = next(e["fullUrl"] for e in b["entry"] if e["resource"] is to_org or e["resource"] == to_org)
    assert sr["performer"] == [{"reference": to_url, "display": "District Hospital, Bhubaneswar"}]
    (risk,) = resources(b, "RiskAssessment")
    coding = risk["prediction"][0]["qualitativeRisk"]["coding"][0]
    assert coding["system"] == "urn:sehat:triage-urgency" and coding["code"] == "RED"
    assert any(x["display"].startswith("Singh SK") for x in risk["basis"])
    for status, expected, detail in (("reached", "referred", None), ("seen", "reached", None), ("outcome_recorded", "seen", "discharged")):
        assert move(client, mo, cid, ref["referral_id"], status, expected, detail).status_code == 200
    (closed,) = resources(export(client, mo, cid, ref["referral_id"]).json(), "ServiceRequest")
    assert closed["status"] == "completed"


def test_vital_signs_use_loinc_and_ucum_and_missing_values_are_left_out(client, anm, mo):
    _, _, b = bundle(client, anm, mo)
    obs = {o["code"]["coding"][0]["code"]: o for o in resources(b, "Observation")}
    assert obs["9279-1"]["valueQuantity"] == {"value": 30, "unit": "/min", "system": "http://unitsofmeasure.org", "code": "/min"}
    assert obs["59408-5"]["valueQuantity"]["value"] == 98 and obs["59408-5"]["valueQuantity"]["code"] == "%"
    assert obs["8867-4"]["valueQuantity"]["value"] == 78 and obs["8310-5"]["valueQuantity"]["code"] == "Cel"
    bp = {c["code"]["coding"][0]["code"]: c["valueQuantity"]["value"] for c in obs["85354-9"]["component"]}
    assert bp == {"8480-6": 124, "8462-4": 80}
    assert all(o["category"][0]["coding"][0]["code"] == "vital-signs" and o["status"] == "final" for o in obs.values())
    _, _, yellow = bundle(client, anm, mo, YELLOW)
    assert "59408-5" not in {o["code"]["coding"][0]["code"] for o in resources(yellow, "Observation")}


def test_export_is_deterministic_audited_and_for_medical_officers_only(client, anm, mo):
    cid, ref, first = bundle(client, anm, mo)
    second = export(client, mo, cid, ref["referral_id"]).json()
    assert first == second
    exported = [json.loads(a["details_json"]) for a in audit_rows(client) if a["action"] == "referral_exported"]
    assert exported == [{"referral_id": ref["referral_id"], "format": "fhir_r4"}] * 2
    assert export(client, anm, cid, ref["referral_id"]).status_code == 403


def test_narratives_are_xhtml_and_escaped(client, anm, mo):
    _, _, b = bundle(client, anm, mo)
    divs = [s["text"]["div"] for s in b["entry"][0]["resource"]["section"]]
    divs += [e["resource"]["text"]["div"] for e in b["entry"] if "text" in e["resource"]]
    assert divs
    for div in divs:
        assert div.startswith(XHTML) and div.endswith("</div>") and "<script" not in div
    assert "&gt;22" in json.dumps(b)  # the rule text "respiratory rate >22" is escaped, not raw markup
