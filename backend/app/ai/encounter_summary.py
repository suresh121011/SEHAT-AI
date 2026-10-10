"""Clinical encounter summary (docs/19): one case-level, source-linked summary built from every stored source.

Hierarchical and deterministic (council, 2026-10-10): facts first, prose second, and no model call.
1. Assemble evidence (`assemble_evidence`): every extraction run's fields with their latest review, voice read-back
   decisions, OCR documents (reviewed and still-unchecked rows, dates, document type), every triage run (the latest
   is authoritative; earlier entries become history), reviewer sign-off/override, and the body map. Each source is
   read on its own: a source that fails is listed as unavailable and the summary is `partial`, never silently short.
2. Normalise and reconcile into `Fact`s. Provenance is never merged away: equal values from several sources become
   one fact citing every source; different values become a visible conflict with no value chosen.
3. Validate the structured summary (`EncounterSummary`, extra="forbid").
4. Render narrative sentences from facts only, then gate every sentence: it must cite existing facts, every number
   in it must appear in a cited fact, an unchecked fact must carry its tag, a machine-read document value that no
   human checked never appears in prose, and the output guard must pass. A failing sentence is listed as blocked.
5. Persist an append-only snapshot keyed by the hash of the case's whole source inventory, so any later change
   anywhere in the case marks it stale (council: not only the sources the assembler chose to read).

Urgency is copied from the rules engine's latest triage run and never computed here: this module never runs the
rules engine and never writes triage runs or review events (a test scans the source). Missing values are listed as missing, never
as normal. Not a diagnosis; a named human reviews and signs off.
"""

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from app import audit, consent
from app.ai import guard, note
from app.ai.extract import field_view, latest_reviews
from app.ai.review import effective_value
from app.auth import Principal
from app.database import read_transaction, transaction
from app.errors import ApiError, not_found
from app.rules import TriageInput, TriageResult
from app.rules.required_fields import missing as required_missing

SUMMARY_SCHEMA_VERSION = "encounter-summary-2026-10-10.1"
DISCLAIMER = ("AI-assisted draft assembled from recorded sources, pending review by a qualified clinician. Not a diagnosis. "
              "Urgency is the rules engine's. Research prototype, not a clinically validated device.")
log = logging.getLogger(__name__)

# ── Provenance: ONE exhaustive registry (council: the R9 KeyError came from a second hand-kept tag map) ─────────
# trust is the plain-language level shown on the page (legend below); tag is appended in prose to anything a human
# has not checked; `narrative` False means the value is shown in tables only, never in prose.
Trust = Literal["checked", "entered", "unchecked"]
PROVENANCE: dict[str, dict[str, Any]] = {
    "ai_accepted": {"label": "AI-extracted from speech/text, accepted by a reviewer", "trust": "checked", "tag": "", "narrative": True},
    "ai_corrected": {"label": "AI-extracted, corrected by a reviewer", "trust": "checked", "tag": "", "narrative": True},
    "ai_unchecked": {"label": "AI-extracted from speech/text, not yet checked", "trust": "unchecked", "tag": " [not yet checked]", "narrative": True},
    "keyword_accepted": {"label": "Keyword match (not AI), accepted by a reviewer", "trust": "checked", "tag": "", "narrative": True},
    "keyword_unchecked": {"label": "Keyword match (not AI), not yet checked", "trust": "unchecked", "tag": " [keyword match, not yet checked]", "narrative": True},
    "voice_confirmed": {"label": "Spoken value, confirmed by a reviewer on read-back", "trust": "checked", "tag": "", "narrative": True},
    "voice_corrected": {"label": "Spoken value, corrected by a reviewer", "trust": "checked", "tag": "", "narrative": True},
    "document_confirmed": {"label": "Document value, confirmed by a reviewer against the image", "trust": "checked", "tag": "", "narrative": True},
    "document_corrected": {"label": "Document value, typed by a reviewer from the paper", "trust": "checked", "tag": "", "narrative": True},
    "document_unchecked": {"label": "Machine-read from a document, not yet checked", "trust": "unchecked", "tag": " [machine-read, not yet checked]", "narrative": False},
    "triage_form": {"label": "Entered on the triage form by a health worker", "trust": "entered", "tag": "", "narrative": True},
    "body_map": {"label": "Marked on the body map", "trust": "entered", "tag": "", "narrative": True},
}
TRUST_LEGEND = {
    "checked": "A health worker or doctor checked this value against its source (read-back, document image, or AI field review).",
    "entered": "Typed or marked directly by a health worker (or the patient, for the body map); not machine-read.",
    "unchecked": "Read by a machine (speech, OCR or AI) and not yet checked by a person. Do not rely on it until checked.",
}

Section = Literal["presenting_problem", "timeline", "symptoms", "vitals", "labs", "medications", "body_map", "red_flags", "context"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Fact(_M):
    fact_id: str
    section: Section
    key: str
    label: str
    display: str
    value: Any
    provenance: Literal[tuple(PROVENANCE)]  # type: ignore[valid-type]
    trust: Trust
    sources: list[dict] = Field(min_length=1)
    flags: list[str] = Field(default_factory=list)


class Row(_M):
    key: str
    label: str
    fact_ids: list[str]
    display: str | None  # None when sources conflict: no value is chosen
    conflict: bool = False
    history: list[dict] = Field(default_factory=list)


class Sentence(_M):
    section: str
    text: str
    fact_ids: list[str] = Field(min_length=1)


class Blocked(_M):
    section: str
    reason: str
    fact_ids: list[str]


class Attention(_M):
    kind: Literal["conflict", "unavailable_source", "blocked_sentence", "pending_review", "triage_predates_evidence", "no_triage_run",
                  "missing_danger_sign"]
    message: str
    action: str
    refs: list[str] = Field(default_factory=list)


class EncounterSummary(_M):
    schema_version: str
    case_id: str
    scenario: str
    status: Literal["complete", "partial"]
    generated_at: str
    attention: list[Attention]
    presenting_problem: list[str]
    timeline: list[str]
    symptoms: dict[str, list[str]]
    vitals: list[Row]
    labs: list[Row]
    medications: list[str]
    body_map: list[str]
    red_flag_mentions: list[str]
    context: list[str]
    missing_information: list[dict]
    follow_up_questions: list[dict]
    conflicts: list[dict]
    pending_review: list[dict]
    unavailable_sources: list[dict]
    excluded_sources: list[dict]
    urgency: dict
    narrative: list[Sentence]
    blocked: list[Blocked]
    facts: dict[str, Fact]
    trust_legend: dict[str, str]
    disclaimer: str
    clinical_use_allowed: Literal[False] = False
    requires_sign_off: Literal[True] = True


# ── Small pure helpers ──────────────────────────────────────────────────────────────────────────────────────────

_WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "eleven": 11, "twelve": 12, "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30}
_UNITS = {"h": "hour", "hr": "hour", "hrs": "hour", "hour": "hour", "hours": "hour", "d": "day", "day": "day", "days": "day",
          "w": "week", "wk": "week", "wks": "week", "week": "week", "weeks": "week", "mo": "month", "month": "month", "months": "month",
          "y": "year", "yr": "year", "yrs": "year", "year": "year", "years": "year"}
_DURATION = re.compile(r"\b(?:(about|around|approximately|approx\.?|nearly|almost|over|more than|less than|since|for|past|last)\s+)?(?:the\s+)?"
                       r"(\d{1,3}(?:\.\d)?|" + "|".join(_WORD_NUM) + r")\s*(hours?|hrs?|h|days?|d|weeks?|wks?|w|months?|mo|years?|yrs?|y)\b", re.IGNORECASE)
_X_DURATION = re.compile(r"\bx\s*(\d{1,3})\s*(d|w|days?|weeks?)\b", re.IGNORECASE)
_APPROX = {"about", "around", "approximately", "approx", "approx.", "nearly", "almost", "over", "more than", "less than"}


def parse_duration(text: str | None) -> dict | None:
    """'3 days', 'three days', 'since 2 weeks', 'x5d', 'about 4 hours' → {amount, unit, approximate, text}. Never computes
    an onset date (the reference time is unknown). Unparseable text returns None; the caller keeps the text as said."""
    if not text:
        return None
    m = _DURATION.search(text) or _X_DURATION.search(text)
    if m is None:
        return None
    if m.re is _X_DURATION:
        qual, num, unit = None, m.group(1), m.group(2)
    else:
        qual, num, unit = m.group(1), m.group(2), m.group(3)
    amount = _WORD_NUM.get(num.lower()) if not num[0].isdigit() else (float(num) if "." in num else int(num))
    unit_key = _UNITS.get(unit.lower().rstrip("."))
    if amount is None or unit_key is None:
        return None
    return {"amount": amount, "unit": unit_key, "approximate": (qual or "").lower() in _APPROX, "text": text.strip()}


def duration_display(d: dict) -> str:
    unit = d["unit"] + ("" if d["amount"] == 1 else "s")
    return f"{'about ' if d['approximate'] else ''}{note._fmt_num(d['amount']) if isinstance(d['amount'], float) else d['amount']} {unit}"


def _fact_id(*parts) -> str:
    return "F" + hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _num(x) -> str:
    return note._fmt_num(x) if isinstance(x, (int, float)) else str(x)


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    return {n.rstrip("0").rstrip(".") if "." in n else n for n in _NUMBER.findall(text)}


class _Builder:
    """Collects facts; equal (section, key, normalised value) from several sources merge into one fact."""

    def __init__(self) -> None:
        self.facts: dict[str, Fact] = {}
        self._by_value: dict[tuple, str] = {}

    def add(self, section: str, key: str, label: str, display: str, value, provenance: str, sources: list[dict], flags=(), merge_key=None) -> str:
        mk = (section, key, json.dumps(merge_key if merge_key is not None else value, sort_keys=True, default=str), PROVENANCE[provenance]["trust"])
        if mk in self._by_value:
            f = self.facts[self._by_value[mk]]
            f.sources.extend(s for s in sources if s not in f.sources)
            f.flags.extend(x for x in flags if x not in f.flags)
            return f.fact_id
        fid = _fact_id(section, key, mk[2], provenance, sources[0] if sources else None)
        self.facts[fid] = Fact(fact_id=fid, section=section, key=key, label=label, display=display, value=value, provenance=provenance,
                               trust=PROVENANCE[provenance]["trust"], sources=list(sources), flags=list(flags))
        self._by_value[mk] = fid
        return fid


# ── Stage 1: evidence assembly (each source on its own; a failure is visible, never silent) ─────────────────────

_VITAL_LABELS = {"temp_c": "Temperature", "spo2": "SpO2", "pulse": "Pulse", "resp_rate": "Respiratory rate", "bp": "Blood pressure",
                 "consciousness": "Consciousness (ACVPU)", "gcs": "GCS", "on_supplemental_oxygen": "On supplemental oxygen"}
_VITAL_UNITS = {"temp_c": "°C", "spo2": "%", "pulse": "/min", "resp_rate": "/min", "bp": "mmHg"}
_VITAL_ORDER = ("temp_c", "spo2", "pulse", "resp_rate", "bp", "consciousness", "gcs", "on_supplemental_oxygen")


def vital_display(key: str, v) -> str:
    if key == "bp":
        return f"{_num(v[0])}/{_num(v[1])} mmHg"
    if key == "temp_c":
        return f"{round(float(v), 1):g} °C"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return f"{_num(v)}{(' ' + _VITAL_UNITS[key]) if key in _VITAL_UNITS else ''}".replace(" %", "%")


def _vital_norm(key: str, v):
    """Comparison form only (the displayed value keeps the source's own form)."""
    if key == "temp_c":
        return round(float(v), 1)
    if key == "bp":
        return [int(v[0]), int(v[1])]
    return v


async def _ai_source(conn, case_id: str, b: _Builder, out: dict) -> None:
    """Every extraction run of the case (not only the latest), each field with its latest human decision. OCR copies
    (`origin='ocr_reviewed'`) are skipped: documents are read directly, with their dates and document context."""
    reviews = await latest_reviews(conn, case_id)
    async with conn.execute(
        "SELECT f.*, r.seq AS run_seq, r.segments_json, r.extraction_id AS run_id FROM ai_fields f JOIN ai_extraction_runs r USING (extraction_id) "
        "WHERE f.case_id = ? ORDER BY r.seq, f.ordinal", (case_id,),
    ) as cur:
        rows = await cur.fetchall()
    durations, segs_by_run = [], {}
    for r in rows:
        if r["origin"] == "ocr_reviewed":
            continue
        f = field_view(r, reviews.get(r["field_id"]))
        rv = f["review"]
        if rv and rv["outcome"] == "rejected":
            continue
        v = effective_value(f["kind"], f["value"], rv)
        decided = rv is not None and rv["outcome"] in ("accepted", "corrected")
        if v is None or (rv is not None and rv["outcome"] == "unsure"):
            out["pending"].append({"kind": "ai_field", "ref": f["field_id"], "label": f["field"], "state": "disputed" if v is None else "unsure",
                                   "action": "Decide this AI field (accept, correct or reject) on the AI review step."})
            if v is None:
                continue
        kw = r["origin"] == "keyword_rule"
        prov = (("keyword_accepted" if kw else ("ai_corrected" if rv["outcome"] == "corrected" else "ai_accepted")) if decided
                else ("keyword_unchecked" if kw else "ai_unchecked"))
        if not decided and not (rv is not None and rv["outcome"] == "unsure"):
            out["pending"].append({"kind": "ai_field", "ref": f["field_id"], "label": f["field"], "state": "not_yet_checked",
                                   "action": "Check this AI-extracted value against what the patient said."})
        sources = [{"type": "ai_field", "field_id": f["field_id"], "extraction_id": r["run_id"], "origin": f["origin"],
                    "evidence": [{"segment_id": e.get("segment_id"), "quote": e.get("quote"), "source": e.get("source")} for e in f["evidence"]]}]
        key = f["field"].split("#")[0]
        kind = f["kind"]
        if kind == "text":
            section = {"chief_complaint": "presenting_problem", "onset": "timeline", "duration": "timeline"}.get(key, "context")
            value: Any = {"text": v}
            if key == "duration":
                parsed = parse_duration(v)
                value = {"text": v, "parsed": parsed}
                display = duration_display(parsed) if parsed else str(v)
                fid = b.add(section, key, "Duration", display, value, prov, sources, merge_key=parsed or v)
                segs_by_run.setdefault(r["run_id"], json.loads(r["segments_json"]))
                durations.append((fid, r["run_id"], {e.get("segment_id") for e in f["evidence"]}))
                continue
            b.add(section, key, note._LABELS.get(key, key.replace("_", " ").capitalize()), str(v), value, prov, sources, merge_key=str(v).lower())
        elif kind == "measurement":
            name = key
            if name in ("temp", "spo2", "pulse", "resp_rate", "bp"):
                vk = {"temp": "temp_c"}.get(name, name)
                if name == "bp":
                    if v.get("value2") is None:
                        continue
                    val = [v["value"], v["value2"]]
                elif name == "temp":
                    unit = (v.get("unit") or "").upper().replace("°", "")
                    if unit not in ("C", "F"):
                        out["pending"] = [p for p in out["pending"] if p["ref"] != f["field_id"]]
                        out["pending"].append({"kind": "ai_field", "ref": f["field_id"], "label": "temp", "state": "unit_missing",
                                               "action": "The temperature unit was not said; enter it on the triage form."})
                        continue
                    val = v["value"] if unit == "C" else round((v["value"] - 32.0) * 5.0 / 9.0, 1)
                else:
                    val = v["value"]
                disp = vital_display(vk, val) + (f" (said as {_num(v['value'])} °F)" if name == "temp" and (v.get("unit") or "").upper().startswith("F") else "")
                b.add("vitals", vk, _VITAL_LABELS[vk], disp, _vital_norm(vk, val), prov, sources, merge_key=_vital_norm(vk, val))
            else:
                unit = f" {v['unit']}" if v.get("unit") else ""
                label = note._LABELS.get(name, name)
                b.add("labs" if name != "pain_severity" else "context", name, label, f"{_num(v['value'])}{unit}", v, prov, sources,
                      merge_key=[v["value"], v.get("unit")])
        elif kind == "symptom":
            b.add("symptoms", f"symptom:{v['name'].lower()}:{'denied' if v['negated'] else 'present'}", v["name"], v["name"], v, prov, sources,
                  merge_key=[v["name"].lower(), v["negated"]])
        elif kind == "medication":
            extra = " ".join(p for p in (v.get("dose"), v.get("frequency")) if p)
            b.add("medications", f"reported:{v['name'].lower()}", "Reported by patient", f"{v['name']}{' ' + extra if extra else ''}", v, prov, sources,
                  merge_key=[v["name"].lower(), v.get("dose"), v.get("frequency")])
        elif kind == "red_flag":
            flag = key.split(":", 1)[1].replace("_", " ")
            b.add("red_flags", f"red_flag:{flag}:{'denied' if v['negated'] else 'mentioned'}", flag, flag, v, prov, sources, merge_key=[flag, v["negated"]])
    # A duration is tied to one symptom only when exactly one reported (not denied) symptom comes from the same sentence.
    for fid, run_id, seg_ids in durations:
        same = [f for f in b.facts.values() if f.section == "symptoms" and f.key.endswith(":present")
                and any(s.get("extraction_id") == run_id and {e.get("segment_id") for e in s.get("evidence", [])} & seg_ids for s in f.sources)]
        if len(same) == 1:
            b.facts[fid].value = {**b.facts[fid].value, "linked_symptom": same[0].display}
            b.facts[fid].flags.append(f"linked_to:{same[0].fact_id}")


async def _voice_source(conn, case_id: str, b: _Builder, out: dict) -> None:
    from app.voice.service import case_resolutions

    resolved, unresolved, display_only = await case_resolutions(conn, case_id)
    for r in resolved:
        if r["outcome"] not in ("confirmed", "corrected") or not r["values"]:
            continue
        prov = "voice_corrected" if r["outcome"] == "corrected" else "voice_confirmed"
        src = [{**r["source"], "type": r["source"]["type"]}]
        vals = r["values"]
        if "sbp" in vals:
            b.add("vitals", "bp", _VITAL_LABELS["bp"], vital_display("bp", [vals["sbp"], vals["dbp"]]), _vital_norm("bp", [vals["sbp"], vals["dbp"]]), prov, src,
                  merge_key=_vital_norm("bp", [vals["sbp"], vals["dbp"]]))
        for k in ("temp_c", "spo2", "pulse", "resp_rate"):
            if k in vals:
                b.add("vitals", k, _VITAL_LABELS[k], vital_display(k, vals[k]), _vital_norm(k, vals[k]), prov, src, merge_key=_vital_norm(k, vals[k]))
        if "age_years" in vals:
            b.add("context", "age_years", "Age", f"{vals['age_years']} years", vals["age_years"], prov, src)
        if "pregnant" in vals:
            b.add("context", "pregnant", "Pregnant", "yes" if vals["pregnant"] else "no", vals["pregnant"], prov, src)
    for d in display_only:
        if d["state"] == "confirmed" and d["value"] is not None:
            parsed = parse_duration(f"{_num(d['value'])} {d['unit'] or ''}")
            disp = duration_display(parsed) if parsed else f"{_num(d['value'])} {d['unit'] or ''}".strip()
            b.add("timeline", "duration", "Duration", disp, {"text": disp, "parsed": parsed}, "voice_confirmed", [d["source"]], merge_key=parsed or disp)
        elif d["state"] in ("undecided", "unsure"):
            out["pending"].append({"kind": "voice_candidate", "ref": d["source"]["candidate_id"], "label": d["field"], "state": d["state"],
                                   "action": "Read the spoken duration back to the patient and confirm or reject it."})
    for u in unresolved:
        out["pending"].append({"kind": "voice_candidate", "ref": u["candidate_id"], "label": u["field"], "state": u["state"],
                               "action": "Read this spoken value back to the patient and confirm, correct or reject it."})


def _doc_source_ref(doc: dict, f: dict, typ: str) -> dict:
    return {"type": typ, "document_id": doc["document_id"], "document_type": doc["document_type"], "field_id": f["field_id"],
            "page_index": f["page_index"], "png_sha256": f["page_png_sha256"], "regions": f["regions"],
            "resolved_by_role": f["review"]["actor_role"] if f["review"] else None}


async def _ocr_source(conn, case_id: str, b: _Builder, out: dict) -> None:
    from app.ocr.service import document_view

    async with conn.execute("SELECT document_id FROM ocr_documents WHERE case_id = ? ORDER BY created_at", (case_id,)) as cur:
        ids = [r["document_id"] for r in await cur.fetchall()]
    for did in ids:
        doc = await document_view(conn, did)
        if doc["status"] == "deleted":
            continue
        if doc["status"] != "completed":
            out["pending"].append({"kind": "document", "ref": did, "label": doc["document_type"], "state": f"processing_{doc['status']}",
                                   "action": "This document was not read (failed or still processing). Retry the upload or enter values by hand."})
            continue
        att = doc["attestation"]
        if att is None or att["answer"] != "matches":
            # Values of a document nobody has matched to this patient and visit are never shown.
            out["pending"].append({"kind": "document", "ref": did, "label": doc["document_type"], "state": "attestation_" + (att["answer"] if att else "missing"),
                                   "action": "Confirm that this document belongs to this patient and this visit before any value is used."})
            continue
        dates = doc.get("dates") or {}
        date = dates.get("collected_date") or dates.get("report_date") or dates.get("prescription_date")
        for f in doc["fields"]:
            state = f["review_status"]
            if state == "rejected":
                continue
            checked = state in ("confirmed", "corrected")
            prov = {"confirmed": "document_confirmed", "corrected": "document_corrected"}.get(state, "document_unchecked")
            src = [_doc_source_ref(doc, f, "ocr_manual_correction" if state == "corrected" else "ocr")]
            if not checked:
                out["pending"].append({"kind": "document_field", "ref": f["field_id"], "label": f.get("analyte_key") or f.get("name_raw") or f.get("drug_raw") or "row",
                                       "state": state, "action": "Check this machine-read row against the document image (confirm, correct, reject or mark unsure)."})
            flags = [x for x in (("disputed_reading" if f.get("disputed") else None), (f"band:{f['band']}" if f.get("band") else None)) if x]
            if f["kind"] == "medication":
                v = f["reviewed_value"] if checked else {k: f.get(k) for k in ("drug_raw", "strength_raw", "dosage_pattern", "frequency_raw", "duration_raw")}
                rx = f.get("rxnorm") or {}
                if rx.get("status") and rx["status"] != "matched":
                    flags.append(f"drug_name_not_matched:{rx['status']}")
                if not checked and (f.get("disputed") or f.get("band") == "human_entry"):
                    flags.append("uncertain_handwriting")
                b.add("medications", f"documented:{(v.get('drug_raw') or '').lower()}:{date}", "Documented on prescription",
                      note.ocr_medication_text(v) or (f.get("line_raw") or "unreadable line"), {**v, "document_date": date}, prov, src, flags,
                      merge_key=[v, date])
            else:
                v = f["reviewed_value"] if checked else {"value": (f.get("value") or {}).get("value"), "comparator": (f.get("value") or {}).get("comparator"),
                                                          "qualitative": (f.get("value") or {}).get("qualitative"), "unit": (f.get("unit") or {}).get("key"),
                                                          "range": f.get("range"), "flag": f.get("printed_flag")}
                name = f.get("analyte_key") or f.get("name_raw") or "test"
                rng = (v.get("range") or {}).get("raw") if isinstance(v.get("range"), dict) else None
                value = {**v, "name": name, "name_raw": f.get("name_raw"), "date": date, "date_kind": "collected" if dates.get("collected_date") else
                         ("reported" if dates.get("report_date") else None), "printed_range": rng}
                b.add("labs", f"lab:{name}", f.get("name_raw") or name, note._ocr_value_text(v), value, prov, src, flags,
                      merge_key=[name, v.get("value"), v.get("qualitative"), v.get("unit"), date])


def _triage_vitals(inp: TriageInput) -> dict[str, Any]:
    vt = inp.vitals
    out: dict[str, Any] = {}
    for k in ("temp_c", "spo2", "pulse", "resp_rate", "consciousness", "gcs", "on_supplemental_oxygen"):
        if getattr(vt, k) is not None:
            out[k] = getattr(vt, k)
    if vt.sbp is not None and vt.dbp is not None:
        out["bp"] = [vt.sbp, vt.dbp]
    return out


async def _triage_source(conn, case_id: str, b: _Builder, out: dict) -> None:
    async with conn.execute("SELECT run_id, urgency, result_json, input_json, created_at, corrects_run_id FROM triage_runs WHERE case_id = ? ORDER BY seq", (case_id,)) as cur:
        runs = await cur.fetchall()
    out["triage_runs"] = runs
    if not runs:
        return
    latest = runs[-1]
    history: dict[str, list[dict]] = {}
    for r in runs[:-1]:
        if r["input_json"]:
            for k, v in _triage_vitals(TriageInput.model_validate_json(r["input_json"])).items():
                history.setdefault(k, []).append({"display": vital_display(k, v), "triage_run_id": r["run_id"], "recorded_at": r["created_at"]})
    out["vital_history"] = history
    if latest["input_json"] is None:
        out["unavailable"].append({"source": "triage_input", "reason": "triage run recorded before inputs were stored"})
        return
    inp = TriageInput.model_validate_json(latest["input_json"])
    out["triage_input"] = inp

    def src(field: str) -> list[dict]:
        return [{"type": "triage_form", "triage_run_id": latest["run_id"], "field": field, "recorded_at": latest["created_at"],
                 "corrects_run_id": latest["corrects_run_id"]}]

    for k, v in _triage_vitals(inp).items():
        b.add("vitals", k, _VITAL_LABELS[k], vital_display(k, v), _vital_norm(k, v), "triage_form", src(f"vitals.{k}" if k != "bp" else "vitals.sbp/dbp"),
              merge_key=_vital_norm(k, v))
    if inp.age_years is not None:
        b.add("context", "age_years", "Age", f"{inp.age_years} years", inp.age_years, "triage_form", src("age_years"))
    if inp.pregnant:
        b.add("context", "pregnant", "Pregnant", "yes" + (f", trimester {inp.trimester}" if inp.trimester else ""), True, "triage_form", src("pregnant"),
              merge_key=True)
    if inp.red_flag_screen_completed:
        b.add("red_flags", "red_flag_screen", "Red-flag screen", "completed on the triage form", True, "triage_form", src("red_flag_screen_completed"))
    for flag in inp.red_flags_present:
        name = flag.value.replace("_", " ") if hasattr(flag, "value") else str(flag).replace("_", " ")
        b.add("red_flags", f"red_flag:{name}:ticked", name, f"{name} (ticked present on the red-flag screen)", True, "triage_form", src("red_flags_present"))


async def _review_status(conn, run_id: str) -> dict:
    async with conn.execute("SELECT kind, new_urgency, actor_role, created_at FROM review_events WHERE triage_run_id = ? ORDER BY seq", (run_id,)) as cur:
        events = await cur.fetchall()
    overrides = [e for e in events if e["kind"] == "override"]
    sign_off = next((e for e in events if e["kind"] == "sign_off"), None)
    return {"review_status": "signed_off" if sign_off else "awaiting_review", "signed_off_by_role": sign_off["actor_role"] if sign_off else None,
            "signed_off_at": sign_off["created_at"] if sign_off else None,
            "reviewer_urgency": overrides[-1]["new_urgency"] if overrides else None, "override_count": len(overrides)}


async def _body_map_source(conn, case_id: str, b: _Builder, out: dict) -> None:
    async with conn.execute("SELECT event_id, regions_json, actor_role, created_at FROM body_map_events WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return
    for region in json.loads(row["regions_json"]):
        b.add("body_map", f"region:{region}", "Body region", region.replace("_", " "), region, "body_map",
              [{"type": "body_map", "event_id": row["event_id"], "recorded_by_role": row["actor_role"], "recorded_at": row["created_at"]}])


SOURCES = (("ai_extraction", _ai_source), ("voice", _voice_source), ("documents", _ocr_source), ("triage", _triage_source), ("body_map", _body_map_source))


async def assemble_evidence(conn: aiosqlite.Connection, case_id: str, *, ai_allowed: bool) -> tuple[_Builder, dict]:
    """Public: every recorded source for a case as facts, inside the caller's (read or write) transaction and after its
    access and consent checks. Reusable by other views (council). AI fields are read only while `ai_assist` is in effect."""
    b = _Builder()
    out: dict[str, Any] = {"pending": [], "unavailable": [], "excluded": [], "triage_runs": [], "vital_history": {}, "triage_input": None}
    for name, fn in SOURCES:
        if name == "ai_extraction" and not ai_allowed:  # a consent choice, not a failure: the summary can still be complete
            out["excluded"].append({"source": name, "reason": "ai_assist_consent_not_in_effect"})
            continue
        try:
            await fn(conn, case_id, b, out)
        except Exception as exc:  # one failing source must not hide the others, and must never be silent
            log.warning("encounter summary source failed: source=%s error=%s", name, type(exc).__name__)
            out["unavailable"].append({"source": name, "reason": "read_failed"})
    return b, out


# ── Stage 2: reconciliation ─────────────────────────────────────────────────────────────────────────────────────

_TRUST_RANK = {"checked": 0, "entered": 1, "unchecked": 2}


def reconcile_rows(facts: list[Fact], keys_order: tuple[str, ...] | None, history: dict | None = None) -> tuple[list[Row], list[dict]]:
    """One row per key. Facts that agree were already merged; several facts left for one key disagree → conflict
    (no value chosen; every candidate and its source stay visible). Unchecked-only disagreement is still a conflict."""
    by_key: dict[str, list[Fact]] = {}
    for f in facts:
        by_key.setdefault(f.key, []).append(f)
    order = list(keys_order or ()) + sorted(k for k in by_key if not keys_order or k not in keys_order)
    rows, conflicts = [], []
    for k in order:
        fs = by_key.get(k)
        if not fs:
            continue
        fs = sorted(fs, key=lambda f: _TRUST_RANK[f.trust])
        distinct = {json.dumps(f.value, sort_keys=True, default=str) for f in fs}
        conflict = len(fs) > 1 and len(distinct) > 1
        label = fs[0].label
        rows.append(Row(key=k, label=label, fact_ids=[f.fact_id for f in fs], display=None if conflict else fs[0].display, conflict=conflict,
                        history=(history or {}).get(k, [])))
        if conflict:
            conflicts.append({"key": k, "label": label, "fact_ids": [f.fact_id for f in fs],
                              "values": [{"display": f.display, "provenance": f.provenance, "trust": f.trust} for f in fs],
                              "action": f"Sources disagree on {label.lower()}. Re-measure or check the sources, then record the right value on the triage form. "
                                        "No value is chosen for you."})
    return rows, conflicts


def lab_rows(facts: list[Fact]) -> tuple[list[Row], list[dict]]:
    """Labs: one row per (test, date). The same test on different dates is two rows (history, not a conflict); the
    same test and date with different values is a conflict."""
    keyed: dict[str, list[Fact]] = {}
    for f in facts:
        date = f.value.get("date") if isinstance(f.value, dict) else None
        keyed.setdefault(f"{f.key}@{date or 'undated'}", []).append(f)
    rows, conflicts = [], []
    for k in sorted(keyed):
        fs = sorted(keyed[k], key=lambda f: _TRUST_RANK[f.trust])
        distinct = {json.dumps([f.value.get("value"), f.value.get("qualitative"), f.value.get("unit")] if isinstance(f.value, dict) else f.value) for f in fs}
        conflict = len(distinct) > 1
        date = k.split("@", 1)[1]
        label = fs[0].label + ("" if date == "undated" else f" ({date})")
        rows.append(Row(key=k, label=label, fact_ids=[f.fact_id for f in fs], display=None if conflict else fs[0].display, conflict=conflict))
        if conflict:
            conflicts.append({"key": k, "label": label, "fact_ids": [f.fact_id for f in fs],
                              "values": [{"display": f.display, "provenance": f.provenance, "trust": f.trust} for f in fs],
                              "action": "Two readings of the same test and date differ. Check both against the document images."})
    return rows, conflicts


def duration_conflicts(facts: list[Fact]) -> list[dict]:
    durs = [f for f in facts if f.key == "duration"]
    parsed = {json.dumps((f.value or {}).get("parsed") or f.display, sort_keys=True) for f in durs}
    if len(durs) > 1 and len(parsed) > 1:
        return [{"key": "duration", "label": "Duration", "fact_ids": [f.fact_id for f in durs],
                 "values": [{"display": f.display, "provenance": f.provenance, "trust": f.trust} for f in durs],
                 "action": "Different durations were recorded. Ask the patient again; both stay visible until one is confirmed."}]
    return []


def symptom_conflicts(facts: list[Fact]) -> list[dict]:
    by_name: dict[str, set[str]] = {}
    for f in facts:
        if f.section == "symptoms":
            by_name.setdefault(f.display.lower(), set()).add(f.fact_id)
    out = []
    for name, ids in by_name.items():
        keys = {f.key.rsplit(":", 1)[1] for f in facts if f.fact_id in ids}
        if {"present", "denied"} <= keys:
            out.append({"key": f"symptom:{name}", "label": name, "fact_ids": sorted(ids),
                        "values": [{"display": f"{'denied' if f.key.endswith('denied') else 'reported'} ({f.provenance})", "provenance": f.provenance, "trust": f.trust}
                                   for f in facts if f.fact_id in ids],
                        "action": f"'{name}' is both reported and denied. Ask the patient again."})
    return out


# ── Missing information (rules engine list + scenario intake checklist; never inferred as normal) ─────────────

_WHY = {
    "red_flag_screen": "The rules engine cannot rule out danger signs without it; GREEN is never given while it is missing.",
    "danger_signs": "Pregnancy danger signs decide escalation; the screen must be done by a person.",
    "chief_complaint": "Without the main problem the handover has no reason for the visit.",
    "duration": "How long the problem has lasted changes how it is assessed and followed up.",
    "severity": "Severity guides how quickly the patient should be seen.",
}
_RULES_TO_CHECKLIST = {"vitals.spo2": "spo2", "vitals.pulse": "pulse", "vitals.resp_rate": "resp_rate", "vitals.temp_c": "temp", "vitals.sbp": "bp",
                       "vitals.dbp": "bp", "red_flag_screen_completed": "red_flag_screen"}
_RULES_WHY = "Required by the rules engine; a missing value is never treated as normal and keeps the case at human review."


def present_keys(facts: list[Fact], triage_input: TriageInput | None) -> set[str]:
    """Keys a human-checked, entered, or agreed value exists for (AI fields count as in docs/16: unchecked but single-valued)."""
    keys = set()
    for f in facts:
        k = f.key
        if f.section == "vitals":
            keys.add({"temp_c": "temp"}.get(k, k))
        elif f.section in ("presenting_problem", "timeline", "context"):
            keys.add(k)
            if k == "pain_severity":
                keys.add("pain_severity")
        elif f.section == "labs":
            keys.add(k.replace("lab:", "ocr:"))
        elif f.section == "medications":
            keys.add("medication:" + k.split(":", 2)[1])
    if triage_input is not None and triage_input.red_flag_screen_completed:
        keys.add("red_flag_screen_completed")
    return keys


def missing_information(scenario: str, facts: list[Fact], triage_input: TriageInput | None, rules_missing: list[str]) -> tuple[list[dict], list[dict]]:
    from app.ai import question_bank

    gaps = required_missing(scenario, present_keys(facts, triage_input))
    items = [{"key": r.key, "label": r.label, "why_it_matters": _WHY.get(r.key, _RULES_WHY if r.priority >= 2 else "Part of the intake checklist for this scenario."),
              "is_danger_sign": r.danger_sign, "source": "intake_checklist", "status": "needs_human_review"} for r in gaps]
    seen = {i["key"] for i in items}
    for m in rules_missing:
        key = m.split(" ")[0]
        if _RULES_TO_CHECKLIST.get(key, key) in seen:
            continue
        items.append({"key": key, "label": f"{m} (missing from the latest triage run)", "why_it_matters": _RULES_WHY,
                      "is_danger_sign": "red_flag" in m or "danger" in m, "source": "rules_engine", "status": "needs_human_review"})
    return items, question_bank.questions_for(gaps)


# ── Stage 4–5: narrative and the grounding gate ───────────────────────────────────────────────────────────────────


def _tagged(f: Fact, text: str) -> str:
    return text + PROVENANCE[f.provenance]["tag"]


def render_narrative(s: dict, facts: dict[str, Fact]) -> list[dict]:
    """Template sentences, one per section, each citing the facts it is built from. Unchecked document values are
    excluded here by construction AND re-checked by the gate. Dates and printed ranges stay in the tables."""
    out: list[dict] = []

    def ok(fid: str) -> bool:
        return PROVENANCE[facts[fid].provenance]["narrative"]

    pp = [fid for fid in s["presenting_problem"] if ok(fid)]
    if pp:
        out.append({"section": "presenting_problem", "fact_ids": pp,
                    "text": "Presenting problem: " + "; ".join(_tagged(facts[f], facts[f].display) for f in pp) + "."})
    dur_conflict = {c for x in s["conflicts"] if x["key"] == "duration" for c in x["fact_ids"]}
    if dur_conflict:
        out.append({"section": "timeline", "fact_ids": sorted(dur_conflict), "names_only": True,
                    "text": "Different durations were recorded, none chosen (see conflicts)."})
    # Agreeing values from several sources (e.g. spoken and typed "3 days") are ONE sentence citing the best-checked
    # source; the timeline itself keeps every source.
    groups: dict[tuple, list[Fact]] = {}
    for fid in s["timeline"]:
        if fid in dur_conflict:
            continue
        f = facts[fid]
        parsed = f.value.get("parsed") if isinstance(f.value, dict) else None
        groups.setdefault((f.key, json.dumps(parsed or f.display, sort_keys=True)), []).append(f)
    for group in groups.values():
        f = min(group, key=lambda x: _TRUST_RANK[x.trust])
        if not ok(f.fact_id):
            continue
        fid = f.fact_id
        linked = (f.value or {}).get("linked_symptom") if isinstance(f.value, dict) else None
        what = f"{linked[:1].upper()}{linked[1:]} for" if linked else ("Duration:" if f.key == "duration" else f"{f.label}:")
        out.append({"section": "timeline", "fact_ids": [fid], "text": f"{_tagged(f, f'{what} {f.display}')} (as reported)."})
    present = [fid for fid in s["symptoms"]["present"] if ok(fid)]
    denied = [fid for fid in s["symptoms"]["denied"] if ok(fid)]
    if present:
        out.append({"section": "symptoms", "fact_ids": present, "text": "Patient reports: " + ", ".join(_tagged(facts[f], facts[f].display) for f in present) + "."})
    if denied:
        out.append({"section": "symptoms", "fact_ids": denied, "text": "Patient denies: " + ", ".join(_tagged(facts[f], facts[f].display) for f in denied) + "."})
    vit = [r for r in s["vitals"] if not r.conflict and r.display]
    if vit:
        parts, ids = [], []
        for r in vit:
            f = facts[r.fact_ids[0]]
            if not ok(f.fact_id):
                continue
            parts.append(_tagged(f, f"{r.label} {r.display}"))
            ids.append(f.fact_id)
        if ids:
            out.append({"section": "vitals", "fact_ids": ids, "text": "Recorded measurements: " + ", ".join(parts) + "."})
    conflicted = [r for r in s["vitals"] if r.conflict]
    if conflicted:
        ids = [fid for r in conflicted for fid in r.fact_ids]
        out.append({"section": "vitals", "fact_ids": ids, "names_only": True,
                    "text": "Sources disagree, no value chosen: " + ", ".join(r.label for r in conflicted) + " (see conflicts)."})
    for r in s["labs"]:
        f = facts[r.fact_ids[0]]
        if r.conflict or not ok(f.fact_id):
            continue
        name = (f.value or {}).get("name_raw") or f.label
        if f.provenance.startswith("document_"):
            what = "Document value, typed by reviewer from the paper" if f.provenance == "document_corrected" else "Document value, reviewed"
        else:
            what = "Lab value mentioned at intake"
        out.append({"section": "labs", "fact_ids": [f.fact_id], "text": _tagged(f, f"{what}: {name} {r.display}") + "."})
    for fid in s["medications"]:
        f = facts[fid]
        if not ok(fid):
            continue
        prefix = "Documented on prescription (reviewed)" if f.key.startswith("documented:") else "Medicine mentioned by patient"
        out.append({"section": "medications", "fact_ids": [fid], "text": f"{_tagged(f, f'{prefix}: {f.display}')}."})
    rf = [fid for fid in s["red_flag_mentions"] if ok(fid)]
    for fid in rf:
        f = facts[fid]
        if f.key == "red_flag_screen":
            text = "Red-flag screen: completed on the triage form."
        elif f.key.endswith(":ticked"):
            text = f"Red flag ticked present: {f.label}."
        elif f.key.endswith(":denied"):
            text = _tagged(f, f"Source text denies: {f.label} (not a completed red-flag screen)") + "."
        else:
            text = _tagged(f, f"Red-flag mention to check on the screen: {f.label}") + "."
        out.append({"section": "red_flags", "fact_ids": [fid], "text": text})
    bm = [fid for fid in s["body_map"] if ok(fid)]
    if bm:
        out.append({"section": "body_map", "fact_ids": bm, "text": "Body map: " + ", ".join(facts[f].display for f in bm) + "."})
    ctx = [fid for fid in s["context"] if ok(fid)]
    if ctx:
        out.append({"section": "context", "fact_ids": ctx, "text": "Context: " + ", ".join(_tagged(facts[f], f"{facts[f].label.lower()} {facts[f].display}") for f in ctx) + "."})
    return out


def gate(sentences: list[dict], facts: dict[str, Fact]) -> tuple[list[Sentence], list[Blocked]]:
    """Every check runs on every sentence; a failing sentence is never repaired, only listed as blocked."""
    passed, blocked = [], []
    for s in sentences:
        s = dict(s)
        shows_values = not s.pop("names_only", False)  # a sentence that only names fields (e.g. "conflicting: SpO2") shows no value
        ids = s["fact_ids"]
        reason = None
        if not ids or any(i not in facts for i in ids):
            reason = "uncited_or_unknown_fact"
        else:
            cited = [facts[i] for i in ids]
            allowed = set().union(*(numbers_in(f"{f.label} {f.display}") for f in cited))
            if not numbers_in(s["text"]) <= allowed:
                reason = "number_not_in_cited_facts"
            elif any(not PROVENANCE[f.provenance]["narrative"] for f in cited):
                reason = "unchecked_document_value_in_prose"
            elif shows_values and any(PROVENANCE[f.provenance]["tag"] and PROVENANCE[f.provenance]["tag"].strip() not in s["text"] for f in cited):
                reason = "missing_unchecked_tag"
            else:
                reason = guard.check(s["text"])
        if reason:
            blocked.append(Blocked(section=s["section"], reason=reason, fact_ids=[i for i in ids if i in facts]))
        else:
            passed.append(Sentence(**s))
    return passed, blocked


# ── Build ─────────────────────────────────────────────────────────────────────────────────────────────────────


def urgency_view(triage_runs: list, review: dict | None, evidence_after: str | None) -> dict:
    if not triage_runs:
        return {"status": "not_determined", "recorded_urgency": None, "message": "Urgency not determined: no triage run on this case. Human triage required."}
    latest = triage_runs[-1]
    res = TriageResult.model_validate_json(latest["result_json"])
    return {
        "status": "rules_engine", "triage_run_id": latest["run_id"], "triage_run_at": latest["created_at"],
        "recorded_urgency": res.urgency.value, "determination": res.determination, "needs_human_review": res.needs_human_review,
        "triggered_rules": [{"rule_id": t.rule_id, "urgency": t.urgency.value, "reason": t.reason, "source_id": t.source_id} for t in res.triggered_rules],
        "scores": {"news2": res.scores.news2.model_dump(include={"status", "total", "partial_total", "band", "reason"}),
                   "qsofa": res.scores.qsofa.model_dump(include={"status", "total", "positive", "reason"})},
        "advisories": [a.model_dump() for a in res.advisories],
        "rules_missing_fields": res.missing_fields,
        **(review or {}),
        "triage_predates_evidence": bool(evidence_after and evidence_after > latest["created_at"]),
        "message": "The urgency shown is the rules engine's, from the latest triage run. This summary never changes it.",
    }


def build_summary(case_id: str, scenario: str, b: _Builder, ctx: dict, review: dict | None, evidence_after: str | None) -> EncounterSummary:
    facts = b.facts
    by_section: dict[str, list[Fact]] = {}
    for f in facts.values():
        by_section.setdefault(f.section, []).append(f)
    vitals, vconf = reconcile_rows(by_section.get("vitals", []), _VITAL_ORDER, ctx.get("vital_history"))
    labs, lconf = lab_rows(by_section.get("labs", []))
    conflicts = vconf + lconf + duration_conflicts(list(facts.values())) + symptom_conflicts(list(facts.values()))
    sym = by_section.get("symptoms", [])
    triage_runs = ctx["triage_runs"]
    rules_missing = TriageResult.model_validate_json(triage_runs[-1]["result_json"]).missing_fields if triage_runs else []
    missing, questions = missing_information(scenario, list(facts.values()), ctx.get("triage_input"), rules_missing)
    s: dict[str, Any] = {
        "presenting_problem": [f.fact_id for f in by_section.get("presenting_problem", [])],
        "timeline": [f.fact_id for f in by_section.get("timeline", [])],
        "symptoms": {"present": [f.fact_id for f in sym if f.key.endswith(":present")], "denied": [f.fact_id for f in sym if f.key.endswith(":denied")]},
        "vitals": vitals, "labs": labs,
        "medications": [f.fact_id for f in sorted(by_section.get("medications", []), key=lambda f: (not f.key.startswith("documented:"), _TRUST_RANK[f.trust]))],
        "body_map": [f.fact_id for f in by_section.get("body_map", [])],
        "red_flag_mentions": [f.fact_id for f in sorted(by_section.get("red_flags", []), key=lambda f: (f.key != "red_flag_screen", f.key.endswith(":denied")))],
        "context": [f.fact_id for f in by_section.get("context", [])],
        "conflicts": conflicts,
    }
    narrative, blocked = gate(render_narrative(s, facts), facts)
    urgency = urgency_view(triage_runs, review, evidence_after)
    attention: list[Attention] = []
    for u in ctx["unavailable"]:
        attention.append(Attention(kind="unavailable_source", message=f"Source not included: {u['source'].replace('_', ' ')} ({u['reason'].replace('_', ' ')}).",
                                   action="This summary is incomplete. Look at that source directly, or fix the problem and refresh."))
    for c in conflicts:
        attention.append(Attention(kind="conflict", message=f"Conflicting values: {c['label']}.", action=c["action"], refs=c["fact_ids"]))
    if blocked:
        attention.append(Attention(kind="blocked_sentence", message=f"{len(blocked)} summary sentence(s) were withheld by the safety checks.",
                                   action="Read the withheld facts in the tables below; nothing was removed from them.", refs=[i for x in blocked for i in x.fact_ids]))
    if not triage_runs:
        attention.append(Attention(kind="no_triage_run", message="No triage has been run on this case, so there is no urgency.",
                                   action="Run triage from the review step. Until then, treat the case as needing human triage."))
    elif urgency["triage_predates_evidence"]:
        attention.append(Attention(kind="triage_predates_evidence", message="Values were added or checked after the last triage run.",
                                   action="If any of them could change urgency, enter them on the triage form and run triage again."))
    for m in missing:
        if m["is_danger_sign"]:
            attention.append(Attention(kind="missing_danger_sign", message=f"Not yet recorded: {m['label']}.", action=m["why_it_matters"]))
    if ctx["pending"]:
        attention.append(Attention(kind="pending_review", message=f"{len(ctx['pending'])} item(s) are waiting for a person to check them.",
                                   action="Open the listed items and decide each one. Unchecked values are marked and never used as confirmed."))
    return EncounterSummary(
        schema_version=SUMMARY_SCHEMA_VERSION, case_id=case_id, scenario=scenario, status="partial" if ctx["unavailable"] else "complete",
        generated_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"), attention=attention,
        presenting_problem=s["presenting_problem"], timeline=s["timeline"], symptoms=s["symptoms"], vitals=vitals, labs=labs,
        medications=s["medications"], body_map=s["body_map"], red_flag_mentions=s["red_flag_mentions"], context=s["context"],
        missing_information=missing, follow_up_questions=questions, conflicts=conflicts, pending_review=ctx["pending"],
        unavailable_sources=ctx["unavailable"], excluded_sources=ctx["excluded"], urgency=urgency, narrative=narrative, blocked=blocked, facts=facts,
        trust_legend=TRUST_LEGEND, disclaimer=DISCLAIMER,
    )


# ── Source inventory (staleness) ──────────────────────────────────────────────────────────────────────────────

_INVENTORY = (
    ("ai_extraction_runs", "seq", "case_id = ?"), ("ai_fields", "rowid", "case_id = ?"), ("ai_field_review_events", "seq", "case_id = ?"),
    ("voice_transcriptions", "rowid", "case_id = ?"), ("voice_candidates", "rowid", "case_id = ?"), ("voice_readback_events", "seq", "case_id = ?"),
    ("ocr_documents", "rowid", "case_id = ?"), ("ocr_fields", "rowid", "case_id = ?"), ("ocr_attestation_events", "seq", "case_id = ?"),
    ("ocr_review_events", "seq", "case_id = ?"), ("ocr_purge_events", "rowid", "document_id IN (SELECT document_id FROM ocr_documents WHERE case_id = ?)"),
    ("triage_runs", "seq", "case_id = ?"), ("review_events", "seq", "case_id = ?"), ("body_map_events", "seq", "case_id = ?"),
    ("consent_events", "seq", "case_id = ?"), ("cases", "rowid", "case_id = ?"),
)
_MUTABLE = (("ocr_documents", "document_id || ':' || status"), ("voice_transcriptions", "transcription_id || ':' || status"), ("cases", "scenario"))


async def inventory(conn, case_id: str) -> tuple[str, str | None]:
    """(sha256, latest evidence timestamp). Hashes EVERY source table's row count and newest row id for the case, plus the
    status of rows that change in place, so a source the assembler failed to read still changes the hash."""
    parts: list[Any] = [SUMMARY_SCHEMA_VERSION]
    for table, key, where in _INVENTORY:
        async with conn.execute(f"SELECT COUNT(*) AS n, MAX({key}) AS m FROM {table} WHERE {where}", (case_id,)) as cur:  # noqa: S608 (constants)
            r = await cur.fetchone()
        parts.append([table, r["n"], r["m"]])
    for table, expr in _MUTABLE:
        async with conn.execute(f"SELECT group_concat(x, ',') AS g FROM (SELECT {expr} AS x FROM {table} WHERE case_id = ? ORDER BY 1)", (case_id,)) as cur:  # noqa: S608
            parts.append([table, (await cur.fetchone())["g"]])
    latest = None
    for table, col in (("ai_field_review_events", "created_at"), ("voice_readback_events", "created_at"), ("ocr_review_events", "created_at"),
                       ("ocr_attestation_events", "created_at"), ("ai_extraction_runs", "created_at"), ("body_map_events", "created_at"),
                       ("ocr_documents", "created_at"), ("voice_transcriptions", "created_at")):
        async with conn.execute(f"SELECT MAX({col}) AS m FROM {table} WHERE case_id = ?", (case_id,)) as cur:  # noqa: S608
            m = (await cur.fetchone())["m"]
        latest = max(latest, m) if latest and m else (m or latest)
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest(), latest


# ── Access ────────────────────────────────────────────────────────────────────────────────────────────────────


async def _gate(conn, principal: Principal, case_id: str, request_id) -> None:
    """Reviewer access (creator ANM or medical officer) and `triage` consent. `ai_assist` is optional: without it the
    summary is built from non-AI sources and says so."""
    denial = None
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "triage")
            await consent.require(conn, case_id, "triage")
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)


async def _consent_state(conn, case_id: str):
    snap = await consent.snapshot(conn, case_id)
    if not snap.is_effective("triage"):
        raise ApiError(409, "CONSENT_WITHDRAWN", "Consent changed; reload")
    return snap


async def _reviews(conn, summary_id: str) -> list[dict]:
    async with conn.execute("SELECT event_id, outcome, acknowledged_conflicts, supersedes, actor_role, created_at FROM encounter_summary_review_events "
                            "WHERE summary_id = ? ORDER BY seq", (summary_id,)) as cur:
        return [{**dict(r), "acknowledged_conflicts": bool(r["acknowledged_conflicts"])} for r in await cur.fetchall()]


def _has_ai_content(row) -> bool:
    return not any(x.get("source") == "ai_extraction" for x in json.loads(row["summary_json"]).get("excluded_sources", []))


def _view(row, current_sha: str, reviews: list[dict]) -> dict:
    summary = json.loads(row["summary_json"])
    latest = reviews[-1] if reviews else None
    stale = row["inventory_sha256"] != current_sha
    return {
        "summary_id": row["summary_id"], "created_at": row["created_at"], "created_by_role": row["actor_role"],
        "is_stale": stale,
        "stale_message": ("New or changed information was recorded after this summary was made. Refresh it before relying on it." if stale else None),
        "review": {"status": latest["outcome"] if latest else "draft_pending_review", "latest": latest, "history": reviews},
        "summary": summary,
    }


# ── Endpoints' services ──────────────────────────────────────────────────────────────────────────────────────


async def generate(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id=None) -> dict:
    await _gate(conn, principal, case_id, request_id)
    async with transaction(conn):
        snap = await _consent_state(conn, case_id)
        sha, evidence_after = await inventory(conn, case_id)
        async with conn.execute("SELECT * FROM encounter_summaries WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)) as cur:
            existing = await cur.fetchone()
        if existing is not None and existing["inventory_sha256"] == sha:  # same sources: reuse, store nothing new
            s = json.loads(existing["summary_json"])
            await audit.record(conn, principal=principal, action="encounter_summary_generated", outcome="success", case_id=case_id, request_id=request_id,
                               details=_details(existing["summary_id"], s, reused=True))
            return _view(existing, sha, await _reviews(conn, existing["summary_id"]))
        async with conn.execute("SELECT scenario FROM cases WHERE case_id = ?", (case_id,)) as cur:
            scenario = (await cur.fetchone())["scenario"]
        b, ctx = await assemble_evidence(conn, case_id, ai_allowed=snap.is_effective("ai_assist"))
        review = await _review_status(conn, ctx["triage_runs"][-1]["run_id"]) if ctx["triage_runs"] else None
        summary = build_summary(case_id, scenario, b, ctx, review, evidence_after)
        data = summary.model_dump(mode="json")
        summary_id, now = str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(timespec="microseconds")
        await conn.execute(
            "INSERT INTO encounter_summaries (summary_id, case_id, triage_run_id, recorded_urgency, inventory_sha256, schema_version, status, summary_json, "
            "consent_seq, actor_id, actor_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (summary_id, case_id, data["urgency"].get("triage_run_id"), data["urgency"]["recorded_urgency"], sha, SUMMARY_SCHEMA_VERSION, data["status"],
             json.dumps(data), snap.latest_seq["triage"], principal.user_id, principal.role.value, now),
        )
        await audit.record(conn, principal=principal, action="encounter_summary_generated", outcome="success", case_id=case_id, request_id=request_id,
                           details=_details(summary_id, data, reused=False))
        async with conn.execute("SELECT * FROM encounter_summaries WHERE summary_id = ?", (summary_id,)) as cur:
            row = await cur.fetchone()
    return _view(row, sha, [])


def _details(summary_id: str, s: dict, reused: bool) -> audit.EncounterSummaryDetails:
    return audit.EncounterSummaryDetails(
        summary_id=summary_id, triage_run_id=s["urgency"].get("triage_run_id"), recorded_urgency=s["urgency"]["recorded_urgency"], status=s["status"],
        reused=reused, facts=len(s["facts"]), conflicts=len(s["conflicts"]), pending_review=len(s["pending_review"]), blocked_sentences=len(s["blocked"]),
        unavailable_sources=len(s["unavailable_sources"]))


async def latest(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id=None) -> dict:
    await _gate(conn, principal, case_id, request_id)
    async with read_transaction(conn):
        snap = await _consent_state(conn, case_id)
        sha, _ = await inventory(conn, case_id)
        async with conn.execute("SELECT * FROM encounter_summaries WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            return {"case_id": case_id, "summary": None, "message": "No encounter summary yet. Generate one to see every recorded source in one place."}
        if _has_ai_content(row) and not snap.is_effective("ai_assist"):
            # Stored AI output is not served once ai_assist is withdrawn (docs/16 §8); the row is kept, not shown.
            return {"case_id": case_id, "summary": None, "is_stale": True,
                    "message": "AI-assist consent changed, so the last summary (which included AI-extracted values) is withheld. Build a new one."}
        return {"case_id": case_id, **_view(row, sha, await _reviews(conn, row["summary_id"]))}


async def history(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id=None) -> dict:
    await _gate(conn, principal, case_id, request_id)
    async with read_transaction(conn):
        await _consent_state(conn, case_id)
        async with conn.execute("SELECT summary_id, status, recorded_urgency, triage_run_id, actor_role, created_at FROM encounter_summaries WHERE case_id = ? "
                                "ORDER BY seq", (case_id,)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return {"case_id": case_id, "summaries": rows}


class SummaryReviewBody(BaseModel):
    """Enums and ids only: no free text (identifiers cannot enter through this endpoint)."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    outcome: Literal["accepted", "needs_changes"]
    acknowledged_conflicts: bool = False
    supersedes: uuid.UUID | None = None


async def review_summary(conn: aiosqlite.Connection, principal: Principal, case_id: str, summary_id: str, body: SummaryReviewBody, request_id=None) -> dict:
    """Records a human decision on ONE summary. It changes nothing in triage: urgency, sign-off and overrides stay on
    the triage run (docs/17)."""
    await _gate(conn, principal, case_id, request_id)
    async with transaction(conn):
        await _consent_state(conn, case_id)
        async with conn.execute("SELECT * FROM encounter_summaries WHERE summary_id = ? AND case_id = ?", (summary_id, case_id)) as cur:
            row = await cur.fetchone()
        if row is None:
            raise not_found()
        async with conn.execute("SELECT summary_id FROM encounter_summaries WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)) as cur:
            if (await cur.fetchone())["summary_id"] != summary_id:
                raise ApiError(409, "SUMMARY_SUPERSEDED", "A newer summary exists for this case; review that one")
        sha, _ = await inventory(conn, case_id)
        if row["inventory_sha256"] != sha:
            raise ApiError(409, "SUMMARY_STALE", "Information changed after this summary was made; refresh it and review the new one")
        reviews = await _reviews(conn, summary_id)
        if (reviews[-1]["event_id"] if reviews else None) != (str(body.supersedes) if body.supersedes else None):
            raise ApiError(409, "REVIEW_CONFLICT", "Someone else reviewed this summary in the meantime; reload")
        s = json.loads(row["summary_json"])
        if body.outcome == "accepted":
            if s["status"] != "complete":
                raise ApiError(409, "SUMMARY_PARTIAL", "A source could not be read, so this summary cannot be accepted; fix it and refresh")
            if s["conflicts"] and not body.acknowledged_conflicts:
                raise ApiError(422, "CONFLICTS_NOT_ACKNOWLEDGED", "This summary has conflicting values; confirm you have seen them before accepting",
                               {"conflicts": len(s["conflicts"])})
        event_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO encounter_summary_review_events (event_id, summary_id, case_id, outcome, acknowledged_conflicts, supersedes, actor_id, actor_role, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, summary_id, case_id, body.outcome, int(body.acknowledged_conflicts), str(body.supersedes) if body.supersedes else None,
             principal.user_id, principal.role.value, datetime.now(timezone.utc).isoformat(timespec="microseconds")),
        )
        await audit.record(conn, principal=principal, action="encounter_summary_reviewed", outcome="success", case_id=case_id, request_id=request_id,
                           details=audit.EncounterSummaryReviewDetails(summary_id=summary_id, event_id=event_id, outcome=body.outcome,
                                                                       acknowledged_conflicts=body.acknowledged_conflicts))
        return {"case_id": case_id, **_view(row, sha, await _reviews(conn, summary_id))}
