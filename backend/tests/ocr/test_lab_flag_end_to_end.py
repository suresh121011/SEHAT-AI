"""P0-1 data path, end to end through the API with engines faked: a kidney-function report with no flag column
(PaddleOCR words replayed, Surya table replayed) → parser → cross-engine verification → ocr_fields → reviewer
attestation and confirmation → encounter-summary evidence. Synthetic data only."""

import json

from tests.ocr.test_api import FakeEngines, _attest, _review, _upload, ocr_client  # noqa: F401  (fixture)
from tests.privacy.helpers import auth, grant, new_case, token_for

# Coordinates on the 1240-px fixture page; the "L" sits just left of the VALUE anchor (inside the name column).
X = {"name": 90, "flag": 420, "value": 460, "unit": 660, "range": 820}
ROWS = [
    [("TEST", X["name"]), ("VALUE", X["value"]), ("UNIT", X["unit"]), ("REFERENCE", X["range"])],
    [("SERUM UREA", X["name"]), ("L", X["flag"]), ("17", X["value"]), ("mg/dl", X["unit"]), ("19-45", X["range"])],
    [("SERUM CREATININE", X["name"]), ("L", X["flag"]), ("0.69", X["value"]), ("mg/dl", X["unit"]), ("0.72-1.18", X["range"])],
    [("Creatinine is a nitrogenous waste product formed", X["name"]), ("in muscle from creatine phosphate.", X["value"])],
    [("SERUM CALCIUM", X["name"]), ("8.9", X["value"]), ("mg/dl", X["unit"]), ("8.8-10.6", X["range"])],
    [("Please correlate clinically. Although the test results", X["name"]), ("are checked thoroughly,", X["value"])],
]
SURYA_HTML = ("<table><thead><tr><th>Test</th><th>Value</th><th>Unit</th><th>Reference</th></tr></thead><tbody>"
              "<tr><td>SERUM UREA L</td><td>17</td><td>mg/dl</td><td>19-45</td></tr>"
              "<tr><td>SERUM CREATININE L</td><td>0.69</td><td>mg/dl</td><td>0.72-1.18</td></tr>"
              "<tr><td>SERUM CALCIUM</td><td>8.9</td><td>mg/dl</td><td>8.8-10.6</td></tr></tbody></table>")


class KftEngines(FakeEngines):
    def __init__(self):
        super().__init__("cbc_low_platelet")

    def paddle_page(self, png, page_index):
        from app.ocr.types import Line, PageOCR, Word

        lines = []
        for r, cells in enumerate(ROWS):
            y = 340 + 30 * r
            for text, x in cells:
                words = text.split() if x == X["name"] else [text]
                for k, w in enumerate(words):
                    box = (x + 60 * k if x == X["name"] else x, y, (x + 60 * k if x == X["name"] else x) + 14 * len(w), y + 22)
                    lines.append(Line(f"p0l{len(lines)}", w, box, 0.99, (Word(w, box, 0.99),)))
        return PageOCR(page_index, 1240, 1754, lines)

    def surya_page(self, png):
        return {"blocks": [{"label": "Table", "bbox": [80, 330, 1170, 520], "confidence": 0.97, "html": SURYA_HTML}]}


def test_flag_in_name_column_reaches_storage_and_summary_evidence(ocr_client):  # noqa: F811
    c = ocr_client
    c.app.state.ocr_engines = KftEngines()
    anm = token_for(c, "anm")
    case_id = new_case(c, anm)
    assert grant(c, anm, case_id).status_code == 200

    doc = _upload(c, anm, case_id).json()
    labs = {f["analyte_key"]: f for f in doc["fields"] if f["kind"] == "lab"}

    # parser: three result rows, the two paragraph rows are gone, the flag is its own field
    assert set(labs) == {"urea", "creatinine", "calcium"}
    urea = labs["urea"]
    assert (urea["name_raw"], urea["printed_flag"], urea["flag_raw"]) == ("SERUM UREA", "L", "L")
    assert "flag_in_name_column" in urea["flags"] and "unrecognized_analyte" not in urea["flags"]
    assert any(r["role"] == "flag" for r in urea["regions"])

    # verification: the second engine's "SERUM UREA L" row is matched by analyte, and the printed L agrees with 17 < 19
    checks = {(ch["check"], ch["status"]) for ch in urea["checks"]}
    assert ("second_engine_agreement", "pass") in checks
    assert ("printed_flag_consistency", "pass") in checks

    # persistence: the stored payload carries the split name and the flag
    from app.config import get_settings
    import sqlite3

    db = sqlite3.connect(get_settings().database_path)
    payloads = [json.loads(p) for (p,) in db.execute("SELECT payload_json FROM ocr_fields WHERE document_id = ?", (doc["document_id"],))]
    db.close()
    stored = {p["analyte_key"]: p for p in payloads}
    assert set(stored) == {"urea", "creatinine", "calcium"}
    assert (stored["urea"]["name_raw"], stored["urea"]["printed_flag"]) == ("SERUM UREA", "L")

    # summary evidence: after the document is matched to this visit and urea confirmed, urea is a checked lab fact
    assert _attest(c, anm, case_id, doc["document_id"]).status_code == 200
    assert _review(c, anm, case_id, urea, "confirmed").status_code == 200
    s = c.post(f"/api/v1/cases/{case_id}/encounter-summary", headers=auth(anm)).json()["summary"]
    rows = {r["key"].split("@")[0]: r for r in s["labs"]}  # keyed per test and date
    urea_fact = s["facts"][rows["lab:urea"]["fact_ids"][0]]
    assert urea_fact["provenance"] == "document_confirmed" and urea_fact["value"]["flag"] == "L"
    assert urea_fact["sources"][0]["document_id"] == doc["document_id"]
    creat_fact = s["facts"][rows["lab:creatinine"]["fact_ids"][0]]
    assert creat_fact["provenance"] == "document_unchecked"  # shown in the table, never in prose until a person checks it
    assert not any("Creatinine is" in json.dumps(f) or "correlate" in json.dumps(f) for f in s["facts"].values())


def test_second_engine_flag_printed_before_the_value_is_not_a_disagreement(ocr_client):  # noqa: F811
    # Real Surya output on the sample KFT report (2026-11-04): the value cell reads "L 17" (flag first). The flag was
    # already read for the row, so it is not part of the value; a different number would still be a disagreement.
    class LeadingFlag(KftEngines):
        def surya_page(self, png):
            html = SURYA_HTML.replace("<td>SERUM UREA L</td><td>17</td>", "<td>SERUM UREA</td><td>L 17</td>") \
                             .replace("<td>SERUM CREATININE L</td><td>0.69</td>", "<td>SERUM CREATININE</td><td>L 0.96</td>")
            return {"blocks": [{"label": "Table", "bbox": [80, 330, 1170, 520], "confidence": 0.97, "html": html}]}

    c = ocr_client
    c.app.state.ocr_engines = LeadingFlag()
    anm = token_for(c, "anm")
    case_id = new_case(c, anm)
    assert grant(c, anm, case_id).status_code == 200
    labs = {f["analyte_key"]: f for f in _upload(c, anm, case_id).json()["fields"]}
    urea = {(ch["check"], ch["status"]) for ch in labs["urea"]["checks"]}
    assert ("second_engine_agreement", "pass") in urea and not labs["urea"]["disputed"] and labs["urea"]["can_confirm"]
    creat = {(ch["check"], ch["status"]) for ch in labs["creatinine"]["checks"]}
    assert ("second_engine_agreement", "fail") in creat and labs["creatinine"]["disputed"]  # 0.69 vs 0.96: still disputed
