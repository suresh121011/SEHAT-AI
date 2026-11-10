"""H1-3 summary transparency (docs/implementation/h1-3-summary-transparency.md): unmatched documents kept apart, machine
agreement labelled but never treated as a human check, what was withheld from the written summary and why, and
operational evidence counts. Synthetic data only; OCR engines replayed; the summary stays template-only."""

import json
import sqlite3

from app.ai import encounter_summary as es
from app.config import get_settings
from tests.ocr.test_api import _attest, _review, _upload, ocr_client  # noqa: F401  (fixture)
from tests.ocr.test_lab_flag_end_to_end import SURYA_HTML, KftEngines
from tests.privacy.helpers import auth, grant, new_case, token_for, triage

URL = "/api/v1/cases/{}/encounter-summary"


def _case(c):
    c.app.state.ocr_engines = KftEngines()
    anm = token_for(c, "anm")
    case_id = new_case(c, anm)
    assert grant(c, anm, case_id).status_code == 200
    return anm, case_id


def _summary(c, anm, case_id):
    r = c.post(URL.format(case_id), headers=auth(anm))
    assert r.status_code == 201, r.text
    return r.json()


def _lab_facts(s):
    return [f for f in s["facts"].values() if f["section"] == "labs"]


def test_unmatched_document_is_listed_apart_and_never_becomes_a_fact(ocr_client):  # noqa: F811
    c = ocr_client
    anm, case_id = _case(c)
    doc = _upload(c, anm, case_id).json()
    s = _summary(c, anm, case_id)["summary"]

    [u] = s["unmatched_documents"]
    assert u["document_id"] == doc["document_id"] and u["attestation"] is None
    assert [r["label"] for r in u["rows"]] == ["SERUM UREA", "SERUM CREATININE", "SERUM CALCIUM"]
    assert all(r["page_index"] == 0 and r["regions"] for r in u["rows"])  # provenance kept
    assert u["rows"][0]["shown"] == "17 mg/dl" and u["rows"][0]["printed_flag"] == "L"
    assert _lab_facts(s) == []  # not in the patient's summary
    assert not any("UREA" in n["text"].upper() for n in s["narrative"])
    assert {"kind": "document", "reason": "document_not_matched", "document_id": doc["document_id"], "section": "labs", "fact_ids": []} in s["withheld"]
    st = s["evidence_status"]
    assert st["documents"] == {"total": 1, "read": 1, "processing": 0, "failed": 0, "quality_rejected": 0, "deleted": 0, "unmatched": 1}
    assert st["readiness"]["state"] == "incomplete" and "document_matching_pending" in st["readiness"]["reasons"]


def test_matching_the_document_moves_its_rows_into_the_summary_as_unchecked(ocr_client):  # noqa: F811
    c = ocr_client
    anm, case_id = _case(c)
    doc = _upload(c, anm, case_id).json()
    _summary(c, anm, case_id)
    assert _attest(c, anm, case_id, doc["document_id"]).status_code == 200
    v = c.get(URL.format(case_id), headers=auth(anm)).json()
    assert v["is_stale"] is True  # matching is a recorded change of the evidence
    s = _summary(c, anm, case_id)["summary"]
    assert s["unmatched_documents"] == []
    labs = {f["key"]: f for f in _lab_facts(s)}
    assert set(labs) == {"lab:urea", "lab:creatinine", "lab:calcium"}
    assert all(f["provenance"] == "document_unchecked" and f["trust"] == "unchecked" for f in labs.values())


def test_machine_agreement_is_labelled_but_is_not_a_human_check(ocr_client):  # noqa: F811
    c = ocr_client
    anm, case_id = _case(c)
    doc = _upload(c, anm, case_id).json()
    assert _attest(c, anm, case_id, doc["document_id"]).status_code == 200
    s = _summary(c, anm, case_id)["summary"]
    calcium = next(f for f in _lab_facts(s) if f["key"] == "lab:calcium")
    assert "ocr_engines_agree" in calcium["flags"]
    assert calcium["trust"] == "unchecked" and calcium["provenance"] == "document_unchecked"  # agreement ≠ verification
    assert not any("CALCIUM" in n["text"].upper() for n in s["narrative"])  # never in the written summary
    obs = s["evidence_status"]["observations"]
    assert obs["machine_agreed_unchecked"] == 3 and obs["human_checked"] == 0 and obs["unchecked"] == 3

    calcium_row = next(f for f in doc["fields"] if f["analyte_key"] == "calcium")
    assert _review(c, anm, case_id, calcium_row, "confirmed").status_code == 200
    s2 = _summary(c, anm, case_id)["summary"]
    calcium2 = next(f for f in _lab_facts(s2) if f["key"] == "lab:calcium")
    assert calcium2["trust"] == "checked" and "ocr_engines_agree" in calcium2["flags"]  # both states kept, separately
    assert s2["evidence_status"]["observations"]["human_checked"] == 1


def test_engine_disagreement_stays_visible(ocr_client):  # noqa: F811
    class Disagree(KftEngines):
        def surya_page(self, png):
            html = SURYA_HTML.replace("<td>8.9</td>", "<td>9.8</td>")
            return {"blocks": [{"label": "Table", "bbox": [80, 330, 1170, 520], "confidence": 0.97, "html": html}]}

    c = ocr_client
    anm, case_id = _case(c)
    c.app.state.ocr_engines = Disagree()
    doc = _upload(c, anm, case_id).json()
    assert _attest(c, anm, case_id, doc["document_id"]).status_code == 200
    s = _summary(c, anm, case_id)["summary"]
    calcium = next(f for f in _lab_facts(s) if f["key"] == "lab:calcium")
    assert "disputed_reading" in calcium["flags"] and "ocr_engines_agree" not in calcium["flags"]
    assert s["evidence_status"]["observations"]["engines_disagree"] == 1


def test_withheld_lists_unchecked_document_values_with_their_reason(ocr_client):  # noqa: F811
    c = ocr_client
    anm, case_id = _case(c)
    doc = _upload(c, anm, case_id).json()
    assert _attest(c, anm, case_id, doc["document_id"]).status_code == 200
    urea = next(f for f in doc["fields"] if f["analyte_key"] == "urea")
    assert _review(c, anm, case_id, urea, "confirmed").status_code == 200
    s = _summary(c, anm, case_id)["summary"]
    narrated = {i for n in s["narrative"] for i in n["fact_ids"]}
    withheld = [w for w in s["withheld"] if w["reason"] == "source_not_verified"]
    held_ids = {i for w in withheld for i in w["fact_ids"]}
    keys = {s["facts"][i]["key"] for i in held_ids}
    assert keys == {"lab:creatinine", "lab:calcium"}  # urea was checked, so it is written; the others are withheld
    assert not held_ids & narrated
    assert any("SERUM UREA" in n["text"] for n in s["narrative"])


def test_a_sentence_the_gate_blocks_is_withheld_with_the_gate_reason_and_never_shown_as_text(ocr_client, monkeypatch):  # noqa: F811
    c = ocr_client
    anm, case_id = _case(c)
    real = es.render_narrative

    def with_bad_sentence(s, facts):
        out = real(s, facts)
        fid = next(iter(facts), None)
        if fid:
            out.append({"section": "labs", "fact_ids": [fid], "text": "The patient has kidney disease."})
        return out

    monkeypatch.setattr(es, "render_narrative", with_bad_sentence)
    triage(c, anm, case_id)  # gives the case a fact to cite
    s = _summary(c, anm, case_id)["summary"]
    gate_items = [w for w in s["withheld"] if w["kind"] == "sentence"]
    assert gate_items and gate_items[0]["reason"] == "diagnostic_language"
    assert "kidney disease" not in json.dumps(s)  # the blocked text is never stored or shown
    assert s["evidence_status"]["summary"]["withheld_sentences"] == len(s["blocked"]) == len(gate_items)


def test_counts_match_the_stored_rows(ocr_client):  # noqa: F811
    c = ocr_client
    anm, case_id = _case(c)
    doc = _upload(c, anm, case_id).json()
    assert _attest(c, anm, case_id, doc["document_id"]).status_code == 200
    rows = {f["analyte_key"]: f for f in doc["fields"]}
    assert _review(c, anm, case_id, rows["urea"], "confirmed").status_code == 200
    assert _review(c, anm, case_id, rows["creatinine"], "unsure").status_code == 200
    s = _summary(c, anm, case_id)["summary"]
    with sqlite3.connect(get_settings().database_path) as db:
        stored = db.execute("SELECT COUNT(*) FROM ocr_fields WHERE case_id = ?", (case_id,)).fetchone()[0]
    obs = s["evidence_status"]["observations"]
    assert obs["extracted"] == stored == 3
    assert (obs["human_checked"], obs["unsure"], obs["unchecked"], obs["rejected"]) == (1, 1, 1, 0)


def test_readiness_needs_matching_checking_and_a_triage_run_and_is_not_clinician_approval(ocr_client):  # noqa: F811
    c = ocr_client
    anm, case_id = _case(c)
    doc = _upload(c, anm, case_id).json()
    assert _attest(c, anm, case_id, doc["document_id"]).status_code == 200
    for f in doc["fields"]:
        assert _review(c, anm, case_id, f, "confirmed").status_code == 200
    s = _summary(c, anm, case_id)["summary"]
    assert s["evidence_status"]["readiness"] == {"state": "incomplete", "reasons": ["no_triage_run"]}
    assert triage(c, anm, case_id).status_code == 200
    v = _summary(c, anm, case_id)
    st = v["summary"]["evidence_status"]
    assert st["readiness"]["state"] == "ready_for_clinician_review" and st["readiness"]["reasons"] == []
    assert v["review"]["status"] == "draft_pending_review"  # clinician approval is a separate, human step
    assert "not mean" in st["note"].lower()


def test_nothing_pending_is_not_reported_as_ready_when_there_is_no_evidence(ocr_client):  # noqa: F811
    c = ocr_client
    anm, case_id = _case(c)
    s = _summary(c, anm, case_id)["summary"]
    assert s["evidence_status"]["readiness"]["state"] == "incomplete" and "no_triage_run" in s["evidence_status"]["readiness"]["reasons"]
    assert s["evidence_status"]["documents"]["total"] == 0


def test_schema_version_changes_the_inventory_so_old_snapshots_go_stale():
    assert es.SUMMARY_SCHEMA_VERSION.endswith(".3")  # .3 (S1-S4): summaries built with the old wording go stale
