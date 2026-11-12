"""Read-only tools over one visit's record, for the clinical assistant (docs/plans/semantic-kernel-orchestration-plan.md
step 2; owner decision K1 of 2026-11-12).

Six Semantic Kernel functions over code that already exists (the encounter summary's evidence, gaps, conflicts and
pending checks; the rules engine's recorded urgency; the fixed question bank). The rules:

- **Bound to one visit by the server.** `VisitTools` is created for a case id, a principal and a connection. No
  function takes a parameter, so a model can never name another visit or pass anything in.
- **Checked on every call.** Access as for the encounter summary (the visit's ANM or a medical officer; facility
  scope), triage consent, and AI-assistance consent, because the output is meant for a model.
- **Read-only.** No write, no audit row, no urgency, no review (a test scans this file). A refused call records the
  usual consent denial, as every other endpoint does.
- **Only what may be said.** Facts are the ones the summary itself may state in prose: not machine-read document
  values, not interview answers (a table only, decision I4), not values whose negation or timing is unclear. Conflicts
  are named without their values, so a model is never handed a value to pick. No visit id, source id or patient token.
"""

import asyncio
import json
from typing import Any

import aiosqlite
from semantic_kernel import Kernel
from semantic_kernel.functions import kernel_function

from app import consent
from app.ai import encounter_summary as es
from app.auth import Principal
from app.database import transaction

PLUGIN_NAME = "visit"
CHECK_WORDS = {"checked": "checked by a person", "entered": "entered by staff", "unchecked": "not yet checked"}
_PENDING_KIND = {"ai_field": "AI-extracted value", "voice_candidate": "spoken value", "document": "document", "document_field": "document line",
                 "medical_image": "medical image"}


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _item(label: str) -> str:
    """'symptom:fever' -> 'symptom: fever'; 'lab_report' -> 'lab report'. Labels only, never a stored id."""
    head, _, rest = label.partition(":")
    return f"{head.replace('_', ' ')}: {rest.replace('_', ' ')}" if rest else head.replace("_", " ")


class VisitTools:
    """The six tools, bound to one visit. Create one per request; never share across visits or principals."""

    def __init__(self, conn: aiosqlite.Connection, principal: Principal, case_id: str):
        self._conn, self._principal, self._case_id = conn, principal, case_id
        # A model may ask for several tools in one turn and Semantic Kernel runs them concurrently. They share one
        # connection, which holds one transaction at a time, so the calls take turns (found on the real Azure model).
        self._lock = asyncio.Lock()

    async def _summary(self) -> es.EncounterSummary:
        async with self._lock:
            return await self._summary_locked()

    async def _summary_locked(self) -> es.EncounterSummary:
        denial = None
        try:
            async with transaction(self._conn):
                await consent.load_case(self._conn, self._principal, self._case_id, "triage")
                await consent.require(self._conn, self._case_id, "triage")
                await consent.require(self._conn, self._case_id, "ai_assist")  # the output goes to a model
                return await es.build_current(self._conn, self._case_id, ai_allowed=True)
        except consent.ConsentNotEffective as exc:
            denial = exc
        raise await consent.audit_denied(self._conn, self._principal, self._case_id, denial, None)

    @kernel_function(name="get_visit_facts",
                     description="The facts recorded for this visit that may be stated in a summary, each with an id to cite, its section, "
                                 "its value and how far it was checked. Cite fact ids for every statement.")
    async def get_visit_facts(self) -> str:
        s = await self._summary()
        shown, withheld = [], 0
        for f in s.facts.values():
            if not es.PROVENANCE[f.provenance]["narrative"] or es.uncertain(f):
                withheld += 1
                continue
            shown.append({"id": f.fact_id, "section": f.section, "label": f.label, "value": f.display, "check": CHECK_WORDS[f.trust],
                          "flags": [x for x in f.flags if not x.startswith(("linked_to:", "ocr_", "band:"))]})
        return _dump({"facts": shown, "withheld": withheld,
                      "note": "Only facts that may be stated are listed. Machine-read document values, interview answers (shown to the "
                              "doctor as a table) and values whose negation or timing is unclear are withheld; their count is given."})

    @kernel_function(name="list_missing_information",
                     description="What has not been recorded for this visit, danger signs first, with why each item matters.")
    async def list_missing_information(self) -> str:
        s = await self._summary()
        return _dump({"missing": [{"item": m["label"], "why": m["why_it_matters"], "danger_sign": bool(m["is_danger_sign"])} for m in s.missing_information],
                      "note": "A missing value is never normal."})

    @kernel_function(name="list_conflicts",
                     description="Values where the recorded sources disagree. Named only: no value is chosen.")
    async def list_conflicts(self) -> str:
        s = await self._summary()
        return _dump({"conflicts": [{"item": c["label"], "sources": len(c["fact_ids"])} for c in s.conflicts],
                      "note": "Sources disagree on these items. No value is chosen; a person asks again."})

    @kernel_function(name="list_waiting_for_check",
                     description="Values and documents that no person has checked yet.")
    async def list_waiting_for_check(self) -> str:
        s = await self._summary()
        return _dump({"waiting": [{"kind": _PENDING_KIND.get(p["kind"], p["kind"].replace("_", " ")), "item": _item(p["label"]),
                                   "state": p["state"].replace("_", " ")} for p in s.pending_review],
                      "note": "Unchecked values are not used as confirmed."})

    @kernel_function(name="get_urgency_record",
                     description="The urgency recorded by the rules engine for this visit, with its reasons. Quote it; never change it.")
    async def get_urgency_record(self) -> str:
        u = (await self._summary()).urgency
        if u["status"] != "rules_engine":
            return _dump({"urgency": None, "source": "rules engine", "reasons": [], "note": u["message"] + " A model never sets or changes urgency."})
        return _dump({"urgency": u["recorded_urgency"], "source": "rules engine",
                      "reasons": [{"rule": t["rule_id"], "urgency": t["urgency"], "reason": t["reason"]} for t in u.get("triggered_rules", [])],
                      "missing_from_triage": u.get("rules_missing_fields", []), "needs_human_review": bool(u.get("needs_human_review")),
                      "signed_off": u.get("review_status") == "signed_off", "values_added_after_triage": bool(u.get("triage_predates_evidence")),
                      "note": "Urgency comes only from the rules engine. A model never sets, lowers or raises it."})

    @kernel_function(name="suggest_follow_up_questions",
                     description="Questions to ask the patient for what is still missing, from a fixed list. Use them as written.")
    async def suggest_follow_up_questions(self) -> str:
        s = await self._summary()
        return _dump({"questions": [{"question": q["question_text"], "about": q["label"], "danger_sign": bool(q.get("is_danger_sign"))}
                                    for q in s.follow_up_questions],
                      "note": "Fixed wording, English only. Do not write new questions."})


def register(kernel: Kernel, *, conn: aiosqlite.Connection, principal: Principal, case_id: str) -> VisitTools:
    """Add the six tools for one visit to a kernel. The server supplies the visit; the model never does."""
    tools = VisitTools(conn, principal, case_id)
    kernel.add_plugin(tools, plugin_name=PLUGIN_NAME)
    return tools
