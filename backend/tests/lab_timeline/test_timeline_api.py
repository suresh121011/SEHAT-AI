"""Lab timeline API (docs/22) through the real path with OCR engines replayed: upload, identity check, a person
confirms or corrects a value, visits linked by patient code, then the timeline. Synthetic data only."""

from decimal import Decimal

from app.consent_notice import NOTICE_VERSION

from tests.ocr.test_api import _attest, _review, _upload, ocr_client  # noqa: F401  (fixture)
from tests.privacy.helpers import audit_rows, auth, new_case, token_for, withdraw

API = "/api/v1"


def consent(c, tok, cid, link=True):
    r = c.post(f"{API}/cases/{cid}/consent", headers=auth(tok), json={"decision": "grant", "include_ai_assist": False, "include_voice_cloud": False,
                                                                      "include_link_visits": link, "language": "en", "notice_version": NOTICE_VERSION})
    assert r.status_code == 200, r.text


def visit(c, tok, link=True):
    cid = new_case(c, tok)
    consent(c, tok, cid, link)
    return cid


def checked_report(c, tok, cid, hb_value=None):
    """A lab report whose identity is confirmed and whose haemoglobin a person confirms (or corrects to `hb_value`)."""
    doc = _upload(c, tok, cid).json()
    assert _attest(c, tok, cid, doc["document_id"]).status_code == 200
    hb = next(f for f in doc["fields"] if f["analyte_key"] == "hemoglobin")
    r = _review(c, tok, cid, hb, "corrected", {"result": {"value": hb_value}}) if hb_value else _review(c, tok, cid, hb, "confirmed")
    assert r.status_code == 200, r.text
    return doc, r.json()


def timeline(c, tok, cid):
    return c.get(f"{API}/cases/{cid}/lab-timeline", headers=auth(tok))


def test_one_visit_shows_only_checked_values_with_their_source_and_counts_the_rest(ocr_client):
    anm = token_for(ocr_client, "anm")
    cid = visit(ocr_client, anm, link=False)
    doc, hb = checked_report(ocr_client, anm, cid)
    t = timeline(ocr_client, anm, cid)
    assert t.status_code == 200, t.text
    body = t.json()
    assert [x["analyte"] for x in body["tests"]] == ["hemoglobin"]  # every other value is still machine-read
    r = body["tests"][0]["results"][0]
    assert r["value"] == hb["reviewed_value"]["value"] and r["outcome"] == "confirmed" and r["current_visit"] is True
    assert r["source"]["document_id"] == doc["document_id"] and r["source"]["field_id"] == hb["field_id"] and r["source"]["regions"]
    gaps = {g["code"]: g["count"] for g in body["gaps"]}
    assert gaps["awaiting_check"] == len([f for f in doc["fields"]]) - 1
    assert body["other_visits"] == "not_linked" and body["other_visits_text"]
    assert "urgency" not in body and "never change triage or urgency" in body["note"]


def test_an_unconfirmed_document_shows_nothing_but_says_why(ocr_client):
    anm = token_for(ocr_client, "anm")
    cid = visit(ocr_client, anm, link=False)
    _upload(ocr_client, anm, cid)
    body = timeline(ocr_client, anm, cid).json()
    assert body["tests"] == [] and {g["code"]: g["count"] for g in body["gaps"]} == {"document_not_checked": 1}


def test_linked_visits_join_one_timeline_and_differences_are_higher_or_lower(ocr_client):
    anm, mo = token_for(ocr_client, "anm"), token_for(ocr_client, "mo")
    v1 = visit(ocr_client, anm)
    code = ocr_client.post(f"{API}/cases/{v1}/patient/new", headers=auth(anm)).json()["patient_code"]
    _, first = checked_report(ocr_client, anm, v1)
    v2 = visit(ocr_client, anm)
    assert ocr_client.post(f"{API}/cases/{v2}/patient/link", headers=auth(anm), json={"patient_code": code, "confirm": True}).status_code == 200
    higher = str(Decimal(first["reviewed_value"]["value"]) + 2)
    checked_report(ocr_client, anm, v2, hb_value=higher)
    body = timeline(ocr_client, mo, v2).json()
    assert body["other_visits"] == "shown" and {v["case_id"] for v in body["visits"]} == {v1, v2}
    hb = body["tests"][0]
    assert [(r["case_id"], r["value"]) for r in hb["results"]] == [(v1, first["reviewed_value"]["value"]), (v2, higher)]
    assert hb["results"][1]["change"]["direction"] == "higher" and hb["results"][1]["outcome"] == "corrected"
    assert withdraw(ocr_client, anm, v1, "link_visits").status_code == 200  # the earlier visit leaves the timeline
    assert {v["case_id"] for v in timeline(ocr_client, mo, v2).json()["visits"]} == {v2}


def test_access_roles_and_consent(ocr_client):
    anm, sup = token_for(ocr_client, "anm"), token_for(ocr_client, "supervisor")
    cid = visit(ocr_client, anm, link=False)
    assert timeline(ocr_client, sup, cid).status_code == 403
    assert timeline(ocr_client, token_for(ocr_client, "patient"), cid).status_code == 403
    assert timeline(ocr_client, token_for(ocr_client, "anm_other"), cid).status_code == 404
    assert timeline(ocr_client, anm, "00000000-0000-4000-8000-000000000000").status_code == 404
    assert withdraw(ocr_client, anm, cid, "triage").status_code == 200
    r = timeline(ocr_client, anm, cid)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert any(a["action"] == "consent_denied" and a["case_id"] == cid for a in audit_rows(ocr_client))
