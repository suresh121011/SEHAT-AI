"""Supervisors see case metadata only (final remediation plan F4, owner decision D-B; docs/11). They keep the
urgency, rule ids, escalation and audit metadata needed for oversight, but not the recorded clinical input, the
engine's result detail, the body map, free-text reasons or image findings. Synthetic data only."""

from tests.privacy.helpers import auth
from tests.review.test_review_api import RED, anm, make_case, mo, override, review, sup  # noqa: F401  (fixtures)


def test_supervisor_case_view_hides_clinical_content_but_keeps_urgency(client, anm, mo, sup):
    cid, run_id = make_case(client, anm, RED)
    assert override(client, mo, cid, run_id, "YELLOW", code="other", text="Reassessed at bedside, stable").status_code == 200
    v = review(client, sup, cid).json()
    assert v["clinical_content_available"] is False and v["clinical_hidden_reason"] == "role"
    latest = v["latest"]
    assert latest["input"] is None and latest["result"] is None and latest["field_provenance"] is None
    assert latest["rules_urgency"] == "RED" and latest["triage_run_id"] == run_id
    assert v["review_events"][0]["reason_text"] is None and v["review_events"][0]["reason_code"] == "other"
    assert v["image_findings"]["flags"] is None
    assert "Reassessed" not in str(v) and "resp_rate" not in str(v)


def test_medical_officer_still_sees_clinical_content(client, anm, mo):
    cid, _ = make_case(client, anm, RED)
    v = review(client, mo, cid).json()
    assert v["clinical_content_available"] is True and v["clinical_hidden_reason"] is None
    assert v["latest"]["input"] is not None


def test_supervisor_sees_that_a_body_map_exists_but_not_its_regions(client, anm, mo, sup):
    cid, _ = make_case(client, anm, RED)
    put = client.put(f"/api/v1/cases/{cid}/body-map", json={"regions": ["chest_left"]}, headers=auth(anm))
    assert put.status_code == 200, put.text
    s = client.get(f"/api/v1/cases/{cid}/body-map", headers=auth(sup)).json()
    assert s["regions"] is None and s["recorded_at"] is not None and s["metadata_only"] is True
    m = client.get(f"/api/v1/cases/{cid}/body-map", headers=auth(mo)).json()
    assert m["regions"] and m.get("metadata_only") is False
