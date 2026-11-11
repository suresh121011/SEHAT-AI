"""Encounter-summary evaluation on the synthetic corpus (tests/ai/fixtures/encounter_cases.json, docs/19 §8).

Each case runs through the real API (fake provider = keyword extractor, NOT an LLM; replayed OCR; explicit human
review actions). Metrics are reported with their denominators and printed with `pytest -s`, or written as markdown to
$ENCOUNTER_EVAL_REPORT (scripts/eval_encounter_summary.py). This measures the summary stage on hand-written synthetic
cases: a regression and safety check, NOT evidence of clinical or handwriting-recognition accuracy."""

import json
import os
import time
from collections import defaultdict
from pathlib import Path

from app.ai import encounter_summary as es
from app.ai.fake_provider import FakeProvider
from tests.ai.helpers import extract
from tests.ocr.test_api import FakeEngines, _attest, _review, _upload, ocr_client  # noqa: F401  (fixture)
from tests.interview.test_interview import answer
from tests.privacy.helpers import auth, grant, new_case, token_for, triage
from tests.test_medgemma import FX, StubBackend, _enable, _img

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "encounter_cases.json").read_text())["cases"]


class Tally:
    def __init__(self) -> None:
        self.n: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # metric -> [passed, total]
        self.failures: list[str] = []

    def check(self, metric: str, ok: bool, what: str) -> None:
        self.n[metric][1] += 1
        if ok:
            self.n[metric][0] += 1
        else:
            self.failures.append(f"{metric}: {what}")


ENGINES = ("Text extraction: fake provider (keyword extractor, NOT an LLM). Documents: replayed OCR engine output. Images: replayed image-model output "
           "(fixtures) or the built-in fake output. Interview: answers recorded through the API; no speech engine. No real model runs in this evaluation.")


def _run_case(c, anm, case: dict) -> dict:
    case_id = new_case(c, anm, case.get("scenario", "opd"))
    assert grant(c, anm, case_id, ai=case.get("ai_consent", True)).status_code == 200
    for doc in case.get("docs", []):
        c.app.state.ocr_engines = FakeEngines(doc.get("engines", "cbc_low_platelet"))
        d = _upload(c, anm, case_id, doc["fixture"], doc["type"]).json()
        assert d["status"] == "completed", d
        if doc.get("attest"):
            assert _attest(c, anm, case_id, d["document_id"], "matches" if doc["attest"] is True else doc["attest"]).status_code == 200
        by_name = {f.get("analyte_key") or f.get("drug_raw"): f for f in d["fields"]}
        for name, action in doc.get("review", {}).items():
            outcome, corr = (action, None) if isinstance(action, str) else action
            r = _review(c, anm, case_id, by_name[name], outcome, corr)
            assert r.status_code == 200, (case["id"], name, r.text)
    for text in case.get("texts", []):
        assert extract(c, anm, case_id, text=text).status_code == 201, case["id"]
    if "triage" in case:
        assert triage(c, anm, case_id, scenario=case.get("scenario", "opd"), vitals=case["triage"]).status_code == 200
    for img in case.get("images", []):
        if img.get("stub"):
            c.app.state.medgemma_backend = StubBackend(FX[img["stub"]]["output"])
        r = _img(c, anm, case_id, img["type"])
        assert r.status_code == 200, (case["id"], r.text)
        doc_id = r.json()["document_id"]
        if img.get("acknowledge"):
            assert c.post(f"/api/v1/cases/{case_id}/medical-images/{doc_id}/acknowledge", json={}, headers=auth(anm)).status_code == 200
        if img.get("delete"):
            assert c.delete(f"/api/v1/cases/{case_id}/medical-images/{doc_id}", headers=auth(anm)).status_code in (200, 204)
    for a in case.get("interview", []):
        body = {k: v for k, v in a.items() if k not in ("key", "expect_error")}
        r = answer(c, anm, case_id, a["key"], **body)
        if a.get("expect_error"):  # a refusal the case expects (a documented limit): nothing may be stored
            assert r.status_code >= 400 and r.json()["error"]["code"] == a["expect_error"], (case["id"], r.text)
            continue
        assert r.status_code == 201, (case["id"], a["key"], r.text)
    r = c.post(f"/api/v1/cases/{case_id}/encounter-summary", headers=auth(anm))
    assert r.status_code == 201, r.text
    return r.json()["summary"]


def _score(s: dict, case: dict, t: Tally) -> None:
    e, cid = case["expect"], case["id"]
    facts = s["facts"]
    prose = " ".join(x["text"] for x in s["narrative"])
    disp = lambda ids: [facts[i]["display"].lower() for i in ids]  # noqa: E731

    # Grounding, every case: each sentence cites known facts and every number in it comes from them.
    for x in s["narrative"]:
        cited = [facts[i] for i in x["fact_ids"] if i in facts]
        ok = bool(x["fact_ids"]) and len(cited) == len(x["fact_ids"]) and es.numbers_in(x["text"]) <= es.numbers_in(
            " ".join(f"{f['label']} {f['display']}" for f in cited)) and all(f["provenance"] != "document_unchecked" for f in cited)
        t.check("supported_sentences", ok, f"{cid}: {x['text']}")
    for f in facts.values():
        t.check("traceable_facts", bool(f["sources"]) and all(src.get("type") for src in f["sources"]), f"{cid}: {f['key']}")
    t.check("urgency_matches_rules", s["urgency"]["status"] in ("rules_engine", "not_determined") and
            (s["urgency"]["recorded_urgency"] is None) == ("triage" not in case), cid)

    if "duration" in e:
        durs = {facts[i]["display"] for i in s["timeline"] if facts[i]["key"] == "duration"}
        t.check("duration_exact", durs == ({e["duration"]} if e["duration"] else set()), f"{cid}: {durs}")
    if "linked" in e:
        linked = {(facts[i]["value"] or {}).get("linked_symptom") for i in s["timeline"] if facts[i]["key"] == "duration"}
        t.check("duration_symptom_link", linked == {e["linked"]}, f"{cid}: {linked}")
    for kind in ("present", "denied"):
        if kind in e:
            got, want = set(disp(s["symptoms"][kind])), set(e[kind])
            for w in want:
                t.check("symptom_recall", w in got, f"{cid}: missing {kind} {w}")
            for g in got:
                t.check("symptom_precision", g in want, f"{cid}: extra {kind} {g}")
    for key, want in e.get("vitals", {}).items():
        row = next((r for r in s["vitals"] if r["key"] == key), None)
        got = "conflict" if row and row["conflict"] else (row["display"] if row else None)
        t.check("numeric_exact", got == want, f"{cid}: {key} {got!r} != {want!r}")
    for key in e.get("vitals_absent", []):
        t.check("abstains_when_unit_or_value_unclear", not any(r["key"] == key for r in s["vitals"]), f"{cid}: {key}")
    for name, want in e.get("labs", {}).items():
        row = next((r for r in s["labs"] if r["key"].startswith(f"lab:{name}@")), None)
        t.check("numeric_exact", bool(row) and want in (row["display"] or ""), f"{cid}: lab {name} {row and row['display']!r}")
        if row:
            f = facts[row["fact_ids"][0]]
            t.check("lab_date_kept", bool(f["value"].get("date")), f"{cid}: {name}")
    for name in e.get("labs_unchecked_not_in_prose", []):
        row = next((r for r in s["labs"] if r["key"].startswith(f"lab:{name}@")), None)
        t.check("unchecked_kept_out_of_prose", bool(row) and facts[row["fact_ids"][0]]["trust"] == "unchecked" and name not in prose.lower(), f"{cid}: {name}")
    if "labs_count" in e:
        t.check("unattested_values_hidden", len(s["labs"]) == e["labs_count"], f"{cid}: {len(s['labs'])}")
    for name, n in e.get("lab_rows", {}).items():
        rows = [r for r in s["labs"] if r["key"].startswith(f"lab:{name}@")]
        t.check("dedupe_correct", len(rows) == n, f"{cid}: {len(rows)} rows")
        if rows:
            n_src = sum(len(facts[i]["sources"]) for i in rows[0]["fact_ids"])
            t.check("dedupe_keeps_all_sources", n_src == e["lab_sources"][name], f"{cid}: {n_src} sources")
    for m in e.get("meds_in_prose", []):
        t.check("medication_exact", m in prose, f"{cid}: {m}")
    for m in e.get("meds_abstained", []):
        med = [f for f in facts.values() if f["section"] == "medications" and (f["value"] or {}).get("drug_raw") == m]
        t.check("handwriting_abstention", bool(med) and med[0]["trust"] == "unchecked" and m not in prose, f"{cid}: {m}")
    for m in e.get("uncertain_flagged", []):
        med = [f for f in facts.values() if f["section"] == "medications" and (f["value"] or {}).get("drug_raw") == m]
        t.check("handwriting_abstention", bool(med) and "uncertain_handwriting" in med[0]["flags"], f"{cid}: {m} flag")
    for m in e.get("meds_absent", []):
        t.check("rejected_values_dropped", not any((f["value"] or {}).get("drug_raw") == m for f in facts.values() if f["section"] == "medications"), f"{cid}: {m}")
    if "conflicts" in e:
        t.check("conflict_detection", sorted(x["key"] for x in s["conflicts"]) == sorted(e["conflicts"]), f"{cid}: {[x['key'] for x in s['conflicts']]}")
    for d in e.get("duration_not_in_prose", []):
        t.check("conflicting_values_not_narrated", d not in prose, f"{cid}: {d}")
    for k in e.get("missing_includes", []):
        t.check("missing_info_listed", k in {m["key"] for m in s["missing_information"]}, f"{cid}: {k}")
    for st in e.get("pending_states", []):
        t.check("pending_surfaced", st in {p["state"] for p in s["pending_review"]}, f"{cid}: {st}")
    # ── schema .4: every case ──
    cited = {i for sec in s["note_sections"] for line in sec["lines"] for i in line["fact_ids"]}
    t.check("note_covers_every_fact", cited == set(facts), f"{cid}: {len(set(facts) - cited)} fact(s) not in the note")
    stated = [i for sec in s["note_sections"] for line in sec["lines"] if line["kind"] in ("sentence", "row") for i in line["fact_ids"]]
    t.check("note_states_each_fact_once", len(stated) == len(set(stated)), cid)
    # ── images ──
    if "images" in e:
        # needs_review = the image carries a RED/YELLOW flag or a type mismatch (docs/18 §8); a negated mention does not.
        got = [(facts[i]["value"]["image_type"], facts[i]["provenance"], facts[i]["value"]["requires_acknowledgement"]) for i in s["imaging"]]
        want = [(x["type"], x["provenance"], x["needs_review"]) for x in e["images"]]
        t.check("image_listed_with_review_state", got == want, f"{cid}: {got}")
        for i in s["imaging"]:
            src = facts[i]["sources"][0]
            t.check("image_source_linked", src.get("type") == "medical_image" and bool(src.get("document_id")) and bool(src.get("source_image_url")), f"{cid}: {src}")
    for w in e.get("findings_not_in_prose", []):
        t.check("image_findings_kept_out_of_prose", w not in prose.lower(), f"{cid}: {w}")
    if "image_attention" in e:
        t.check("image_flag_surfaced", any(a["kind"] == "image_flag_unacknowledged" for a in s["attention"]) == e["image_attention"], cid)
    if "image_text_withheld" in e:
        t.check("diagnostic_image_text_withheld", all(facts[i]["value"]["text_withheld"] for i in s["imaging"]) == e["image_text_withheld"], cid)
    for w in e.get("not_in_note_values", []):
        t.check("unsupported_statement_absent", w.lower() not in s["note_text"].lower(), f"{cid}: {w!r} is in the note")
    if "unmatched_documents" in e:
        t.check("identity_mismatch_values_hidden", len(s["unmatched_documents"]) == e["unmatched_documents"] and not s["labs"], cid)
    if "urgency_unchanged" in e:
        t.check("image_flag_never_changes_urgency", s["urgency"]["recorded_urgency"] == e["urgency_unchanged"], f"{cid}: {s['urgency']['recorded_urgency']}")
    # ── interview ──
    if "interview" in e:
        got = {facts[i]["value"]["question_key"]: facts[i]["display"] for i in s["interview"]}
        t.check("interview_answer_exact", got == e["interview"], f"{cid}: {got}")
        for i in s["interview"]:
            f = facts[i]
            t.check("interview_source_linked", f["sources"][0].get("type") == "interview_answer" and bool(f["sources"][0].get("event_id")), cid)
            t.check("interview_verification_state", f["provenance"] == "interview_confirmed" and f["trust"] == "checked", cid)
        t.check("interview_never_in_prose", not any(x["section"] == "interview" for x in s["narrative"]), cid)
    for w in e.get("interview_not_in_prose", []):
        t.check("interview_never_in_prose", w.lower() not in prose.lower(), f"{cid}: {w}")
    for w in e.get("interview_not_in_note", []):
        t.check("interview_latest_answer_wins", w.lower() not in s["note_text"].lower(), f"{cid}: {w}")
    for k in e.get("interview_unknown", []):
        f = next((facts[i] for i in s["interview"] if facts[i]["value"]["question_key"] == k), None)
        t.check("not_sure_kept_as_unknown", bool(f) and f["value"]["is_unknown"] and "unknown_answer" in f["flags"], f"{cid}: {k}")
    for k, who in e.get("interview_speaker", {}).items():
        f = next((facts[i] for i in s["interview"] if facts[i]["value"]["question_key"] == k), None)
        t.check("interview_speaker_kept", bool(f) and f["value"]["speaker"] == who, f"{cid}: {k}")
    for k in e.get("missing_excludes", []):
        t.check("answered_question_not_missing", k not in {m["key"] for m in s["missing_information"]}, f"{cid}: {k}")
    if "excluded" in e:
        t.check("consent_respected", [x["source"] for x in s["excluded_sources"]] == e["excluded"] and s["status"] == e["status"], cid)


def _report(t: Tally, timing: dict[str, list[float]] | None = None) -> str:
    lines = ["| Metric | Passed / total | Rate |", "|:---|---:|---:|"]
    for k in sorted(t.n):
        ok, n = t.n[k]
        lines.append(f"| {k} | {ok} / {n} | {ok / n:.0%} |")
    groups = defaultdict(int)
    for case in CORPUS:
        groups[case.get("group", "summary")] += 1
    lines.append(f"\n{len(CORPUS)} synthetic cases ({', '.join(f'{n} {g}' for g, n in sorted(groups.items()))}). Mode: MOCK / offline. {ENGINES}")
    lines.append(f"Schema {es.SUMMARY_SCHEMA_VERSION}. Provider errors: 0 (a provider error fails the run). Not evidence of clinical accuracy.")
    if timing:
        lines += ["", "| Case group | Cases | Mean time per case (build + summary, in-process) |", "|:---|---:|---:|"]
        lines += [f"| {g} | {len(v)} | {sum(v) / len(v) * 1000:.0f} ms |" for g, v in sorted(timing.items())]
    if t.failures:
        lines += ["", "Failures:"] + [f"- {x}" for x in t.failures]
    return "\n".join(lines)


def test_corpus_has_image_and_interview_cases_with_gold_labels():
    groups = defaultdict(list)
    for case in CORPUS:
        groups[case.get("group", "summary")].append(case)
        assert case["expect"], case["id"]
    assert len(groups["image"]) >= 10 and len(groups["interview"]) >= 10 and len(groups["summary"]) >= 18
    assert len({c["id"] for c in CORPUS}) == len(CORPUS)


def test_encounter_summary_corpus(ocr_client, monkeypatch):  # noqa: F811
    c = ocr_client
    _enable(monkeypatch)  # the fake image backend; cases with "stub" replay a recorded model output instead
    default_backend = getattr(c.app.state, "medgemma_backend", None)
    anm = token_for(c, "anm")
    c.app.state.ai_provider = FakeProvider()
    t = Tally()
    timing: dict[str, list[float]] = defaultdict(list)
    for case in CORPUS:
        c.app.state.medgemma_backend = default_backend
        t0 = time.perf_counter()
        _score(_run_case(c, anm, case), case, t)
        timing[case.get("group", "summary")].append(time.perf_counter() - t0)
    report = _report(t, timing)
    print("\n" + report)
    if os.environ.get("ENCOUNTER_EVAL_REPORT"):
        Path(os.environ["ENCOUNTER_EVAL_REPORT"]).write_text(report + "\n")
    # Safety metrics must be perfect; any failure is listed in full.
    assert not t.failures, "\n".join(t.failures)
