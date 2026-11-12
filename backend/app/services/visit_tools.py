"""Read-only tools over one visit's record, for the clinical assistant (docs/plans/semantic-kernel-orchestration-plan.md
step 2; owner decision K1 of 2026-11-12; docs/24).

Seven Semantic Kernel functions over code that already exists (the encounter summary's evidence, gaps, conflicts and
pending checks; the rules engine's recorded urgency; the fixed question bank; the last stored summary). The rules:

- **Bound to one visit by the server.** `VisitTools` is created for a case id, a principal and a connection. No
  function takes a parameter, so a model can never name another visit or pass anything in.
- **Checked on every call.** Access as for the encounter summary (the visit's ANM or a medical officer; facility
  scope), triage consent, and AI-assistance consent, because the output is meant for a model.
- **Read-only.** No write, no audit row, no urgency, no review (a test scans this file). A refused call records the
  usual consent denial, as every other endpoint does.
- **Only what may be said.** Facts are the ones the summary itself may state in prose: not machine-read document
  values, not interview answers (a table only, decision I4), not values whose negation or timing is unclear. Conflicts
  are named without their values, so a model is never handed a value to pick. No visit id, source id or patient token.
- **Citable.** Every item a tool returns carries an id to cite (a fact id, or `missing-1`, `conflict-1`, `waiting-1`,
  `question-1`, `change-1`), and the instance records what it returned (`citable`), so the assistant's gate accepts
  only citations of what the model was actually given in this run. The urgency record has no id: no sentence may cite
  urgency (docs/24 §3).
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import aiosqlite
from semantic_kernel import Kernel
from semantic_kernel.functions import kernel_function

from app import consent
from app.ai import encounter_summary as es
from app.auth import Principal
from app.database import transaction

PLUGIN_NAME = "visit"
TOOL_NAMES = ("get_visit_facts", "list_missing_information", "list_conflicts", "list_waiting_for_check", "get_urgency_record",
              "suggest_follow_up_questions", "list_changes_since_last_summary")
CHECK_WORDS = {"checked": "checked by a person", "entered": "entered by staff", "unchecked": "not yet checked"}
_PENDING_KIND = {"ai_field": "AI-extracted value", "voice_candidate": "spoken value", "document": "document", "document_field": "document line",
                 "medical_image": "medical image"}


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _item(label: str) -> str:
    """'symptom:fever' -> 'symptom: fever'; 'lab_report' -> 'lab report'. Labels only, never a stored id."""
    head, _, rest = label.partition(":")
    return f"{head.replace('_', ' ')}: {rest.replace('_', ' ')}" if rest else head.replace("_", " ")


@dataclass(frozen=True, eq=False)
class Citable:
    """One item a tool gave the model, which a sentence may cite (docs/24 §3). A sentence's numbers may come only from
    the cited facts' label and value, or from a non-fact item's short label: never from the item's longer text (a
    question's wording, a reason). `facts` are the record facts behind the item; every fact check applies to them."""

    id: str
    kind: str  # fact, missing, conflict, waiting, question, change
    label: str
    text: str
    facts: tuple[es.Fact, ...] = ()
    removed: bool = False  # a change "no longer in the record": its old value is never stated, and the sentence must say so

    @classmethod
    def for_fact(cls, f: es.Fact) -> "Citable":
        return cls(id=f.fact_id, kind="fact", label=f"{f.label}: {f.display} ({CHECK_WORDS[f.trust]})", text=f"{f.label} {f.display}", facts=(f,))


def main_term(label: str) -> str:
    """'Headache (said: head is paining)' -> 'headache'; 'Consciousness (ACVPU)' -> 'consciousness'. Lower case."""
    return " ".join(label.split("(")[0].split(":")[0].lower().split())


def stateable(f: es.Fact) -> bool:
    """What the summary itself may state in prose (docs/19): not machine-read document values or interview answers,
    and not values whose negation or timing is unclear."""
    return bool(es.PROVENANCE[f.provenance]["narrative"]) and not es.uncertain(f)


class VisitTools:
    """The six tools, bound to one visit. Create one per request; never share across visits or principals."""

    def __init__(self, conn: aiosqlite.Connection, principal: Principal, case_id: str):
        self._conn, self._principal, self._case_id = conn, principal, case_id
        # A model may ask for several tools in one turn and Semantic Kernel runs them concurrently. They share one
        # connection, which holds one transaction at a time, so the calls take turns (found on the real Azure model).
        self._lock = asyncio.Lock()
        self.citable: dict[str, Citable] = {}  # every item returned to the model so far, by the id it may cite

    def _offer(self, items: list[Citable]) -> None:
        for c in items:
            self.citable[c.id] = c

    async def _summary(self) -> es.EncounterSummary:
        return (await self._read(previous=False))[0]

    async def server_terms(self) -> frozenset[str]:
        """Every item the visit's record names (all facts, also withheld ones; missing items, conflicts, pending checks
        other than document lines, follow-up topics), for the answer gate: a sentence may name one only when it cites
        it. Kept on the server; never offered to the model (not a kernel function)."""
        s = await self._summary()
        labels = [f.label for f in s.facts.values()] + [m["label"] for m in s.missing_information] + [c["label"] for c in s.conflicts]
        labels += [_item(p["label"]).split(": ", 1)[-1] for p in s.pending_review if p["kind"] != "document_field"]
        labels += [q["label"] for q in s.follow_up_questions]
        return frozenset(t for t in (main_term(x) for x in labels) if len(t) >= 2)

    async def _read(self, *, previous: bool) -> tuple[es.EncounterSummary, Any]:
        async with self._lock:
            denial = None
            try:
                async with transaction(self._conn):
                    await consent.load_case(self._conn, self._principal, self._case_id, "triage")
                    await consent.require(self._conn, self._case_id, "triage")
                    await consent.require(self._conn, self._case_id, "ai_assist")  # the output goes to a model
                    summary = await es.build_current(self._conn, self._case_id, ai_allowed=True)
                    row = None
                    if previous:
                        async with self._conn.execute("SELECT summary_json, created_at FROM encounter_summaries WHERE case_id = ? ORDER BY seq DESC LIMIT 1",
                                                      (self._case_id,)) as cur:
                            row = await cur.fetchone()
                    return summary, row
            except consent.ConsentNotEffective as exc:
                denial = exc
            raise await consent.audit_denied(self._conn, self._principal, self._case_id, denial, None)

    @kernel_function(name="get_visit_facts",
                     description="The facts recorded for this visit that may be stated in a summary, each with an id to cite, its section, "
                                 "its value and how far it was checked. Cite fact ids for every statement.")
    async def get_visit_facts(self) -> str:
        s = await self._summary()
        shown, offered, withheld = [], [], 0
        for f in s.facts.values():
            if not stateable(f):
                withheld += 1
                continue
            item = {"id": f.fact_id, "section": f.section, "label": f.label, "value": f.display, "check": CHECK_WORDS[f.trust],
                    "flags": [x for x in f.flags if not x.startswith(("linked_to:", "ocr_", "band:"))]}
            if tag := es.PROVENANCE[f.provenance]["tag"]:
                item["tag"] = tag  # an unchecked value is stated only with this tag right after it
            shown.append(item)
            offered.append(Citable.for_fact(f))
        self._offer(offered)
        return _dump({"facts": shown, "withheld": withheld,
                      "note": "Only facts that may be stated are listed. Machine-read document values, interview answers (shown to the "
                              "doctor as a table) and values whose negation or timing is unclear are withheld; their count is given. "
                              "A fact with a tag is stated only with that tag right after its value."})

    @kernel_function(name="list_missing_information",
                     description="What has not been recorded for this visit, danger signs first, with why each item matters.")
    async def list_missing_information(self) -> str:
        s = await self._summary()
        out = [{"id": f"missing-{n}", "item": m["label"], "why": m["why_it_matters"], "danger_sign": bool(m["is_danger_sign"])}
               for n, m in enumerate(s.missing_information, 1)]
        self._offer([Citable(id=m["id"], kind="missing", label=m["item"], text=f"{m['item']}. {m['why']}") for m in out])
        return _dump({"missing": out, "note": "A missing value is never normal."})

    @kernel_function(name="list_conflicts",
                     description="Values where the recorded sources disagree. Named only: no value is chosen.")
    async def list_conflicts(self) -> str:
        s = await self._summary()
        out = [{"id": f"conflict-{n}", "item": c["label"], "sources": len(c["fact_ids"])} for n, c in enumerate(s.conflicts, 1)]
        self._offer([Citable(id=c["id"], kind="conflict", label=c["item"], text=f"{c['item']}: {c['sources']} sources disagree.") for c in out])
        return _dump({"conflicts": out, "note": "Sources disagree on these items. No value is chosen; a person asks again."})

    @kernel_function(name="list_waiting_for_check",
                     description="Values and documents that no person has checked yet.")
    async def list_waiting_for_check(self) -> str:
        s = await self._summary()
        # A document line's label can be the machine-read text itself (a drug or test name as read): never passed on.
        out = [{"id": f"waiting-{n}", "kind": _PENDING_KIND.get(p["kind"], p["kind"].replace("_", " ")),
                "item": "a line of a document" if p["kind"] == "document_field" else _item(p["label"]),
                "state": p["state"].replace("_", " ")} for n, p in enumerate(s.pending_review, 1)]
        self._offer([Citable(id=w["id"], kind="waiting", label=w["item"], text=f"{w['kind']}: {w['item']} ({w['state']})") for w in out])
        return _dump({"waiting": out, "note": "Unchecked values are not used as confirmed."})

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
        out = [{"id": f"question-{n}", "question": q["question_text"], "about": q["label"], "danger_sign": bool(q.get("is_danger_sign"))}
               for n, q in enumerate(s.follow_up_questions, 1)]
        self._offer([Citable(id=q["id"], kind="question", label=q["about"], text=f"{q['question']} ({q['about']})") for q in out])
        return _dump({"questions": out, "note": "Fixed wording, English only. Do not write new questions."})

    @kernel_function(name="list_changes_since_last_summary",
                     description="What was added, changed or is no longer in the record since the last stored summary of this visit.")
    async def list_changes_since_last_summary(self) -> str:
        s, row = await self._read(previous=True)
        if row is None:
            return _dump({"since": None, "changes": [], "note": "No summary has been stored for this visit yet, so there is nothing to compare."})
        before: dict[str, es.Fact] = {}
        for fid, data in json.loads(row["summary_json"]).get("facts", {}).items():
            try:
                before[fid] = es.Fact.model_validate(data)
            except ValueError:  # a summary stored under an older schema: that fact is not compared
                continue
        now = s.facts
        changes, withheld = [], 0
        for fid in list(now) + [f for f in before if f not in now]:
            new, old = now.get(fid), before.get(fid)
            if (new is not None and not stateable(new)) or (old is not None and not stateable(old)):
                withheld += int(new is None or old is None or (new.display, new.trust) != (old.display, old.trust))
                continue
            if old is None:
                changes.append({"_fact": fid, "change": "added", "item": new.label, "now": f"{new.display} ({CHECK_WORDS[new.trust]})"})
            elif new is None:
                changes.append({"_fact": fid, "change": "no longer in the record", "item": old.label, "before": old.display})
            elif (new.display, new.trust) != (old.display, old.trust):
                changes.append({"_fact": fid, "change": "changed", "item": new.label, "before": f"{old.display} ({CHECK_WORDS[old.trust]})",
                                "now": f"{new.display} ({CHECK_WORDS[new.trust]})"})
        offered = []
        for n, ch in enumerate(changes, 1):
            ch["id"] = f"change-{n}"
            fid = ch.pop("_fact")
            if fid not in now:  # removed: no value may be stated, and a sentence must say it is no longer in the record
                offered.append(Citable(id=ch["id"], kind="change", label=f"{ch['item']} (no longer in the record)",
                                       text=f"{ch['item']}: no longer in the record", removed=True))
                continue
            fact = now[fid]  # a sentence may state only the current value, never an old one as current
            if tag := es.PROVENANCE[fact.provenance]["tag"]:
                ch["tag"] = tag
            offered.append(Citable(id=ch["id"], kind="change", label=ch["item"], text=f"{ch['item']}: {ch['change']}", facts=(fact,)))
        self._offer(offered)
        # No timestamp: the model does not need it, and a date-time reads like an identifier pattern to the redaction check.
        return _dump({"since": "the last stored summary of this visit", "changes": changes, "withheld": withheld,
                      "note": "Changes in what may be stated, compared with the last stored summary. Withheld values are counted only. "
                              "Only the current value may be stated, with its tag if it has one."})


def register(kernel: Kernel, *, conn: aiosqlite.Connection, principal: Principal, case_id: str) -> VisitTools:
    """Add the seven tools for one visit to a kernel. The server supplies the visit; the model never does."""
    tools = VisitTools(conn, principal, case_id)
    kernel.add_plugin(tools, plugin_name=PLUGIN_NAME)
    return tools
