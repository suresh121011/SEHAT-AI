"""Phase 8 reviewer workflow (docs/17): priority queue, case review, sign-off, override, RED escalation, governance.

Safety model
- The rules engine's urgency on the latest `triage_runs` row is authoritative. Nothing here re-evaluates or
  edits it; LLM output never enters ordering.
- Review actions are append-only `review_events` rows bound to ONE triage run, plus an audit event in the same
  transaction. A newer triage run starts a fresh review (earlier sign-offs/overrides stay in history).
- Queue order: priority urgency (RED > YELLOW > GREEN), then oldest latest-run first (operational tie-breaker).
  Priority urgency is the rules urgency, raised (never lowered) by a reviewer override: a reviewer may record a
  lower urgency with a reason, but the case does not drop in the queue because of it.
- RED escalation: deadline = anchor + 180 s, computed server-side. Anchor is the triage run time for a rules RED,
  or the override time when a reviewer raised the case to RED. A re-triage neither restarts nor closes the clock:
  an earlier rules-RED run that nobody acknowledged keeps the escalation open on every later run, RED or not, with
  the earliest such run as the anchor (`anchor_source: "earlier_red_run"`). An acknowledgment or sign-off on the
  latest run acknowledges it. Unacknowledged past the deadline is "overdue"; the first observation records ONE audit event.
  No notification is sent by this prototype: "overdue" is a displayed state, not a delivered alert.
"""

import json
import sqlite3
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import aiosqlite
import anyio
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import audit, case_triage, consent
from app.auth import Principal, Role
from app.database import read_transaction, transaction
from app.errors import ApiError
from app.rules import TriageInput

ESCALATION_SECONDS = 180
_RANK = {"RED": 3, "YELLOW": 2, "GREEN": 1}

# Prototype list pending clinical governance review (docs/17 §4). Not a clinical taxonomy.
OVERRIDE_REASONS: dict[str, str] = {
    "clinical_reassessment": "Clinical reassessment on examination",
    "data_entry_error": "Recorded input was entered incorrectly",
    "additional_information": "Additional information not captured in the triage input",
    "source_disputed": "Source evidence is disputed or unreliable",
    "other": "Other (explanation required)",
}
ReasonCode = Literal["clinical_reassessment", "data_entry_error", "additional_information", "source_disputed", "other"]
Urgency = Literal["RED", "YELLOW", "GREEN"]

# Why a recorded value was corrected (docs/17 §3a). Prototype list pending clinical governance review, like the
# override reasons. Codes are stored with each run; a future relabel keeps old codes valid for history.
CORRECTION_REASONS: dict[str, str] = {
    "remeasured": "Re-measured with the patient",
    "entry_error": "Typed in wrong when recorded",
    "source_disputed": "Source of the recorded value is doubtful",
    "other": "Other (explanation required)",
}
CorrectionReason = Literal["remeasured", "entry_error", "source_disputed", "other"]
CORRECTION_ACTOR_ROLE = Role.MEDICAL_OFFICER.value  # the corrections route is medical-officer only


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class SignOffBody(_Body):
    triage_run_id: uuid.UUID
    confirm: Literal[True]  # explicit confirmation; anything else is a 422


class OverrideBody(_Body):
    triage_run_id: uuid.UUID
    new_urgency: Urgency
    reason_code: ReasonCode
    reason_text: str | None = Field(default=None, max_length=500)
    expected_urgency: Urgency | None = None  # the urgency the reviewer saw; a different current value → 409
    confirm: Literal[True]

    @model_validator(mode="after")
    def _text(self) -> "OverrideBody":
        text = (self.reason_text or "").strip()
        self.reason_text = text or None
        if self.reason_code == "other" and (self.reason_text is None or len(self.reason_text) < 10):
            raise ValueError("reason_text of at least 10 characters is required when reason_code is 'other'")
        return self


class CorrectionBody(_Body):
    """A reviewer's correction of the recorded rules-engine input. `expected_triage_run_id` is the run the reviewer
    was looking at; if a newer run exists the correction is refused (409), never applied on top of it."""

    expected_triage_run_id: uuid.UUID
    input: TriageInput
    reason_code: CorrectionReason
    reason_text: str | None = Field(default=None, max_length=300)
    confirm: Literal[True]

    @model_validator(mode="after")
    def _text(self) -> "CorrectionBody":
        text = (self.reason_text or "").strip()
        self.reason_text = text or None
        if self.reason_code == "other" and (self.reason_text is None or len(self.reason_text) < 10):
            raise ValueError("reason_text of at least 10 characters is required when reason_code is 'other'")
        return self


class AcknowledgeBody(_Body):
    triage_run_id: uuid.UUID


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="microseconds")


def _utc(dt: datetime) -> datetime:
    """Naive datetimes are taken as UTC; aware ones are converted (stored timestamps are UTC ISO strings)."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Read model ───────────────────────────────────────────────────────────


def _scope_sql(scope: frozenset[str] | None, column: str = "c.facility_code") -> tuple[str | None, list[str]]:
    """SQL condition limiting rows to a facility scope (docs/17 §8). None = unrestricted (no condition); an empty scope
    matches nothing. The scope comes from `Principal.facilities` (server config), never from the request."""
    if scope is None:
        return None, []
    if not scope:
        return "0", []
    codes = sorted(scope)
    return f"{column} IN ({','.join('?' * len(codes))})", codes


async def _latest_runs(conn: aiosqlite.Connection, facility_code: str | None, case_id: str | None = None, *,
                       scope: frozenset[str] | None = None) -> list[aiosqlite.Row]:
    """Latest triage run per case. `facility_code` is a filter; `scope` is authorization (keyword-only, default None =
    unrestricted, for system callers). Both apply, so a filter can only narrow the scope, never widen it."""
    sql = (
        "SELECT c.case_id, c.patient_token, c.facility_code, c.scenario, c.status, c.created_at AS case_created_at, "
        "r.run_id, r.urgency, r.result_json, r.created_at AS run_created_at, r.input_json IS NOT NULL AS has_input "
        "FROM cases c JOIN triage_runs r ON r.seq = (SELECT MAX(seq) FROM triage_runs WHERE case_id = c.case_id) "
    )
    where, args = [], []
    if facility_code:
        where.append("c.facility_code = ?")
        args.append(facility_code)
    if case_id:
        where.append("c.case_id = ?")
        args.append(case_id)
    scope_cond, scope_args = _scope_sql(scope)
    if scope_cond is not None:
        where.append(scope_cond)
        args.extend(scope_args)
    if where:
        sql += "WHERE " + " AND ".join(where)
    async with conn.execute(sql, args) as cur:
        return list(await cur.fetchall())


async def _events_by_run(conn: aiosqlite.Connection, run_ids: list[str]) -> dict[str, list[aiosqlite.Row]]:
    out: dict[str, list[aiosqlite.Row]] = {r: [] for r in run_ids}
    if not run_ids:
        return out
    marks = ",".join("?" * len(run_ids))
    async with conn.execute(f"SELECT * FROM review_events WHERE triage_run_id IN ({marks}) ORDER BY seq", run_ids) as cur:
        for row in await cur.fetchall():
            out[row["triage_run_id"]].append(row)
    return out


def walk_escalations(runs: list[dict], events_by_run: dict[str, list], open_: dict | None = None) -> tuple[list[dict], dict | None]:
    """Pure: walk one case's runs (oldest first, each {run_id, urgency, created_at}) and their review events in time
    order. An escalation episode opens at a rules-engine RED run, or at a reviewer override to RED on a non-RED run,
    while no episode is open; it closes at the next acknowledgment or sign-off. Returns (closed episodes, the open
    episode or None). Used for the queue state, the carry-over across re-runs and governance, so all three agree:
    re-running triage can neither restart nor close an escalation, a later override that lowers the urgency does not
    close it, and a NEW raise after an acknowledgment opens a NEW episode with its own deadline (docs/17 §5)."""
    closed: list[dict] = []
    for run in runs:
        if open_ is None and run["urgency"] == "RED":
            open_ = {"anchor": run["created_at"], "source": "triage_run", "run_id": run["run_id"]}
        for e in sorted(events_by_run.get(run["run_id"], []), key=lambda e: e["created_at"]):
            if open_ is None and e["kind"] == "override" and e["new_urgency"] == "RED" and run["urgency"] != "RED":
                open_ = {"anchor": e["created_at"], "source": "reviewer_override", "run_id": run["run_id"]}
            elif open_ is not None and e["kind"] in ("acknowledge", "sign_off") and e["created_at"] >= open_["anchor"]:
                closed.append({**open_, "ack": e})
                open_ = None
    return closed, open_


def red_hold(runs: list[dict], events_by_run: dict[str, list]) -> bool:
    """Pure: True if a RED (a rules-engine RED run, or a reviewer raise to RED) occurred on these runs after the last
    sign-off. Acknowledging stops the escalation timer but NOT the hold: only a sign-off releases RED queue priority,
    so an acknowledgment followed by a lower re-run cannot drop the case from RED without review (docs/17 §5)."""
    hold = False
    for run in runs:
        if run["urgency"] == "RED":
            hold = True
        for e in sorted(events_by_run.get(run["run_id"], []), key=lambda e: e["created_at"]):
            if e["kind"] == "override" and e["new_urgency"] == "RED":
                hold = True
            elif e["kind"] == "sign_off":
                hold = False
    return hold


async def _red_chain_anchors(conn: aiosqlite.Connection, case_ids: list[str]) -> dict[str, dict | None]:
    """Per case: the escalation still open after walking every run BEFORE the latest one (see `walk_escalations`),
    marked as carried over; else None. The latest run's own events are walked in `_review_state`."""
    out: dict[str, dict | None] = {cid: None for cid in case_ids}
    by_case: dict[str, list[dict]] = {cid: [] for cid in case_ids}
    events: dict[str, list] = {}
    for i in range(0, len(case_ids), 500):
        chunk = case_ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        async with conn.execute(f"SELECT case_id, run_id, urgency, created_at FROM triage_runs WHERE case_id IN ({marks}) ORDER BY seq", chunk) as cur:
            for row in await cur.fetchall():
                by_case[row["case_id"]].append(dict(row))
        async with conn.execute(f"SELECT * FROM review_events WHERE case_id IN ({marks}) ORDER BY seq", chunk) as cur:
            for row in await cur.fetchall():
                events.setdefault(row["triage_run_id"], []).append(row)
    for cid, runs in by_case.items():
        _, open_ = walk_escalations(runs[:-1], events)
        hold = red_hold(runs[:-1], events)
        out[cid] = {"open": {**open_, "source": "earlier_red_run"} if open_ else None, "hold": hold} if (open_ or hold) else None
    return out


def _review_state(run: aiosqlite.Row, events: list[aiosqlite.Row], now: datetime, chain: dict | None = None) -> dict:
    """Pure: effective urgency, priority, review status and escalation for one latest run. `carried` is an escalation
    still open from an earlier run (see `_red_chain_anchors`)."""
    rules = run["urgency"]
    effective, effective_source = rules, "rules_engine"
    overrides = [e for e in events if e["kind"] == "override"]
    for e in overrides:
        effective, effective_source = e["new_urgency"], "reviewer_override"
    priority, priority_source = (effective, "reviewer_override_raise") if _RANK[effective] > _RANK[rules] else (rules, "rules_engine")
    sign_off = next((e for e in events if e["kind"] == "sign_off"), None)

    carried, held = (chain["open"], chain["hold"]) if chain else (None, False)
    closed, open_ = walk_escalations([{"run_id": run["run_id"], "urgency": rules, "created_at": run["run_created_at"]}], {run["run_id"]: events}, carried)
    current = open_ or (closed[-1] if closed else None)  # the open episode, else the most recent closed one
    escalation = None
    if current is not None:
        deadline = _parse(current["anchor"]) + timedelta(seconds=ESCALATION_SECONDS)
        ack = current.get("ack")
        if ack is not None:
            ack_at, state, late = _parse(ack["created_at"]), "acknowledged", _parse(ack["created_at"]) > deadline
        else:
            ack_at, late = None, None
            state = "overdue" if now >= deadline else "pending"
        escalation = {
            "state": state,
            "anchor": current["anchor"],
            "anchor_source": current["source"],
            "deadline": _iso(deadline),
            "window_seconds": ESCALATION_SECONDS,
            "acknowledged_at": _iso(ack_at) if ack_at else None,
            "acknowledged_by_role": ack["actor_role"] if ack else None,
            "acknowledged_via": ack["kind"] if ack else None,
            "acknowledged_late": late,
            "notification_sent": False,  # this prototype has no notification channel (docs/17 §5)
        }
    if (escalation is not None or held) and sign_off is None and priority != "RED":
        # A RED escalation on this case keeps it at RED in the queue, whatever the latest run or a later override
        # says, until the run is SIGNED OFF. An acknowledgment ("seen") closes the escalation timer but does not by
        # itself lower queue priority, so a reflexive acknowledgment cannot bury the case (docs/17 §5; final council).
        priority, priority_source = "RED", "open_red_escalation"
    return {
        "rules_urgency": rules,
        "effective_urgency": effective,
        "effective_urgency_source": effective_source,
        "priority_urgency": priority,
        "priority_source": priority_source,
        "override_count": len(overrides),
        "review_status": "signed_off" if sign_off else "awaiting_review",
        "signed_off_at": sign_off["created_at"] if sign_off else None,
        "escalation": escalation,
    }


def _queue_item(run: aiosqlite.Row, state: dict, consent_triage: str, now: datetime) -> dict:
    result = json.loads(run["result_json"])
    clinical = consent_triage == "granted"  # docs/11 D5: rule ids / missing fields are not served after withdrawal
    return {
        "case_id": run["case_id"],
        "patient_token": run["patient_token"],
        "facility_code": run["facility_code"],
        "scenario": run["scenario"],
        "triage_run_id": run["run_id"],
        "triaged_at": run["run_created_at"],
        "waiting_seconds": max(0, int((now - _parse(run["run_created_at"])).total_seconds())),
        "determination": result.get("determination"),
        "needs_human_review": bool(result.get("needs_human_review", True)),
        "missing_fields": result.get("missing_fields", []) if clinical else [],
        "triggered_rule_ids": [t.get("rule_id") for t in result.get("triggered_rules", [])] if clinical else [],
        "consent_triage": consent_triage,
        **state,
    }


def _sort_key(item: dict) -> tuple:
    return (-_RANK[item["priority_urgency"]], item["triaged_at"], item["case_id"])


async def _consents(conn: aiosqlite.Connection, case_ids: list[str]) -> dict[str, str]:
    return {cid: (await consent.snapshot(conn, cid)).effective("triage") for cid in case_ids}


async def _record_overdue(conn: aiosqlite.Connection, principal: Principal, items: list[dict], request_id: str | None) -> list[dict]:
    """Record ONE `red_escalation_overdue` audit event per escalation (keyed by case + server deadline, which is
    derived from the escalation's anchor) the first time it is observed overdue — re-running triage on a carried
    RED does not create another event."""
    overdue = [i for i in items if i["escalation"] and i["escalation"]["state"] == "overdue"]
    if not overdue:
        return []
    newly: list[dict] = []
    async with transaction(conn):
        for item in overdue:
            async with conn.execute("SELECT 1 FROM audit_log WHERE action = 'red_escalation_overdue' AND case_id = ? AND json_extract(details_json, '$.deadline') = ? LIMIT 1",
                                    (item["case_id"], item["escalation"]["deadline"])) as cur:
                if await cur.fetchone():
                    continue
            # Re-check under the write lock: an acknowledgment may have committed since the read snapshot.
            async with conn.execute("SELECT 1 FROM review_events WHERE triage_run_id = ? AND kind IN ('acknowledge', 'sign_off') AND created_at >= ? LIMIT 1",
                                    (item["triage_run_id"], item["escalation"]["anchor"])) as cur:
                if await cur.fetchone():
                    continue
            await audit.record(conn, principal=principal, action="red_escalation_overdue", outcome="success", case_id=item["case_id"], request_id=request_id,
                               details=audit.EscalationDetails(triage_run_id=item["triage_run_id"], deadline=item["escalation"]["deadline"],
                                                               seconds_after_deadline=int((_now() - _parse(item["escalation"]["deadline"])).total_seconds())))
            newly.append(item)
    return newly


async def queue(conn: aiosqlite.Connection, principal: Principal, facility_code: str | None, include_signed_off: bool, request_id: str | None) -> dict:
    now = _now()
    async with read_transaction(conn):
        runs = await _latest_runs(conn, facility_code, scope=principal.facilities)
        events = await _events_by_run(conn, [r["run_id"] for r in runs])
        consents = await _consents(conn, [r["case_id"] for r in runs])
        chains = await _red_chain_anchors(conn, [r["case_id"] for r in runs])
    items = [_queue_item(r, _review_state(r, events[r["run_id"]], now, chains[r["case_id"]]), consents[r["case_id"]], now) for r in runs]
    newly = await _record_overdue(conn, principal, items, request_id)
    # Never hide an unacknowledged RED case, even when signed-off cases are filtered out.
    visible = [i for i in items if include_signed_off or i["review_status"] != "signed_off" or (i["escalation"] and i["escalation"]["state"] != "acknowledged")]
    visible.sort(key=_sort_key)
    return {
        "generated_at": _iso(now),
        "facility_code": facility_code,
        "ordering": "priority_urgency (RED > YELLOW > GREEN; rules engine, raised-only by reviewer override), then oldest triage run first",
        "escalation_window_seconds": ESCALATION_SECONDS,
        "counts": {u: sum(1 for i in visible if i["priority_urgency"] == u) for u in ("RED", "YELLOW", "GREEN")},
        "items": visible,
        "_newly_overdue": newly,
    }


async def escalations(conn: aiosqlite.Connection, principal: Principal, facility_code: str | None, request_id: str | None) -> dict:
    data = await queue(conn, principal, facility_code, include_signed_off=True, request_id=request_id)
    items = [i for i in data["items"] if i["escalation"] is not None]
    return {
        "generated_at": data["generated_at"],
        "window_seconds": ESCALATION_SECONDS,
        "notification_channel": None,
        "items": [{k: i[k] for k in ("case_id", "patient_token", "facility_code", "triage_run_id", "priority_urgency", "rules_urgency", "escalation")} for i in items],
        "_newly_overdue": data["_newly_overdue"],
    }


def _flatten(d: dict, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for k, v in d.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, f"{path}."))
        else:
            out[path] = v
    return out


def input_changes(old: dict, new: dict) -> list[dict]:
    """Pure: every leaf of the rules-engine input whose value differs, as {field, old, new} (lists compared whole)."""
    a, b = _flatten(old), _flatten(new)
    return [{"field": f, "old": a.get(f), "new": b.get(f)} for f in sorted(set(a) | set(b)) if a.get(f) != b.get(f)]


def _corrections_view(history: list[dict], principal: Principal, clinical_allowed: bool) -> list[dict]:
    by_id = {h["run_id"]: h for h in history}
    out = []
    for h in history:
        if not h["corrects_run_id"]:
            continue
        prev = by_id.get(h["corrects_run_id"])
        changes = input_changes(json.loads(prev["input_json"]), json.loads(h["input_json"])) if prev and prev["input_json"] and h["input_json"] else []
        out.append({
            "triage_run_id": h["run_id"], "corrects_run_id": h["corrects_run_id"],
            "urgency_before": prev["urgency"] if prev else None, "urgency_after": h["urgency"],
            "changed_fields": [c["field"] for c in changes],
            "changes": changes if clinical_allowed else None,
            "reason_code": h["correction_reason_code"], "reason_label": CORRECTION_REASONS.get(h["correction_reason_code"] or ""),
            "reason_text": h["correction_reason_text"] if clinical_allowed else None,
            "actor_role": CORRECTION_ACTOR_ROLE, "is_current_user": h["actor_id"] == principal.user_id, "created_at": h["created_at"],
        })
    return out


def _field_provenance(latest_run_id: str, corrections: list[dict]) -> dict[str, dict]:
    """Per-field source of the latest run's input, ONLY where the server recorded it: a run created by a reviewer
    correction knows which fields the reviewer changed and which were carried over unchanged. Any other run has no
    per-field source (intake submits one combined input), so the map is empty — never guessed."""
    corr = next((c for c in corrections if c["triage_run_id"] == latest_run_id), None)
    if corr is None or corr["changes"] is None:
        return {}
    changed = {c["field"]: c for c in corr["changes"]}
    meta = {"from_run_id": corr["corrects_run_id"], "created_at": corr["created_at"], "actor_role": corr["actor_role"],
            "is_current_user": corr["is_current_user"], "reason_code": corr["reason_code"], "reason_label": corr["reason_label"]}
    return {f: ({"source": "reviewer_correction", "previous_value": c["old"], **meta}) for f, c in changed.items()} | {
        "*": {"source": "unchanged_from_previous_run", "from_run_id": corr["corrects_run_id"]}}


async def case_review(conn: aiosqlite.Connection, principal: Principal, case_id: str) -> dict:
    """Case detail for the reviewer. Clinical content needs effective triage consent (docs/11 D5: earlier results
    are kept, but are not served after withdrawal); the urgency and escalation stay visible for safety."""
    now = _now()
    async with read_transaction(conn):
        case = await consent.load_case(conn, principal, case_id, "read")
        runs = await _latest_runs(conn, None, case_id)
        snap = await consent.snapshot(conn, case_id)
        async with conn.execute("SELECT run_id, urgency, engine_version, ruleset_version, created_at, input_json IS NOT NULL AS has_input, input_json, actor_id, "
                                "corrects_run_id, correction_reason_code, correction_reason_text FROM triage_runs WHERE case_id = ? ORDER BY seq", (case_id,)) as cur:
            history = [dict(r) for r in await cur.fetchall()]
        async with conn.execute("SELECT * FROM review_events WHERE case_id = ? ORDER BY seq", (case_id,)) as cur:
            all_events = list(await cur.fetchall())
        async with conn.execute("SELECT seq, timestamp, actor_role, action, outcome FROM audit_log WHERE case_id = ? ORDER BY seq", (case_id,)) as cur:
            audit_rows = [dict(r) for r in await cur.fetchall()]
        input_json = None
        chains = await _red_chain_anchors(conn, [case_id])
        if runs:
            async with conn.execute("SELECT input_json FROM triage_runs WHERE run_id = ?", (runs[0]["run_id"],)) as cur:
                input_json = (await cur.fetchone())["input_json"]
    consent_triage = snap.effective("triage")
    clinical_allowed = consent_triage == "granted"
    base = {
        "generated_at": _iso(now),
        "case": {"case_id": case["case_id"], "patient_token": case["patient_token"], "scenario": case["scenario"], "facility_code": case["facility_code"],
                 "status": case["status"], "created_at": case["created_at"]},
        "consent": consent.state_view(snap),
        "clinical_content_available": clinical_allowed,
        "can_review": principal.role == Role.MEDICAL_OFFICER,
        "override_reasons": [{"code": k, "label": v, "requires_text": k == "other"} for k, v in OVERRIDE_REASONS.items()],
        "correction_reasons": [{"code": k, "label": v, "requires_text": k == "other"} for k, v in CORRECTION_REASONS.items()],
        "escalation_window_seconds": ESCALATION_SECONDS,
        "audit": audit_rows,
    }
    corrections = _corrections_view(history, principal, clinical_allowed)
    runs_out = [{k: h[k] for k in ("run_id", "urgency", "engine_version", "ruleset_version", "created_at", "has_input", "corrects_run_id", "correction_reason_code")}
                for h in history]
    if not runs:
        return {**base, "latest": None, "runs": runs_out, "review_events": [], "corrections": corrections}
    run = runs[0]
    run_events = [e for e in all_events if e["triage_run_id"] == run["run_id"]]
    item = _queue_item(run, _review_state(run, run_events, now, chains[case_id]), consent_triage, now)
    events_out = [{
        "event_id": e["event_id"], "triage_run_id": e["triage_run_id"], "kind": e["kind"], "rules_urgency": e["rules_urgency"],
        "old_urgency": e["old_urgency"], "new_urgency": e["new_urgency"], "reason_code": e["reason_code"],
        "reason_label": OVERRIDE_REASONS.get(e["reason_code"]) if e["reason_code"] else None,
        "reason_text": e["reason_text"] if clinical_allowed else None,
        "actor_role": e["actor_role"], "is_current_user": e["actor_id"] == principal.user_id, "created_at": e["created_at"],
    } for e in all_events]
    return {
        **base,
        "latest": {**item, "result": json.loads(run["result_json"]) if clinical_allowed else None, "has_input": bool(run["has_input"]),
                   "input": json.loads(input_json) if clinical_allowed and input_json else None,
                   "field_provenance": _field_provenance(run["run_id"], corrections) if clinical_allowed else None},
        "runs": runs_out,
        "review_events": events_out,
        "corrections": corrections,
    }


# ── Writes ───────────────────────────────────────────────────────────────


def _require_reviewer(principal: Principal) -> None:
    """Defence in depth: the routes already require a medical officer (routes/review.py), but every review action
    also checks it here, so no other caller can let a case's creating ANM sign off, override, acknowledge or correct
    their own case (final council, Contrarian). It is also what makes CORRECTION_ACTOR_ROLE true by construction."""
    if principal.role != Role.MEDICAL_OFFICER:
        raise ApiError(403, "FORBIDDEN", "Only a medical officer can record a review action")


async def _load_for_action(conn: aiosqlite.Connection, principal: Principal, case_id: str, run_id: str, need_consent: bool) -> tuple[aiosqlite.Row, list[aiosqlite.Row], str | None]:
    _require_reviewer(principal)
    await consent.load_case(conn, principal, case_id, "triage")
    if need_consent:
        await consent.require(conn, case_id, "triage")
    runs = await _latest_runs(conn, None, case_id)
    if not runs:
        raise ApiError(409, "NO_TRIAGE_RUN", "This case has no recorded triage run to review")
    run = runs[0]
    if run["run_id"] != run_id:
        raise ApiError(409, "STALE_TRIAGE_RUN", "A newer triage run exists for this case; reload and review the latest run",
                       {"latest_triage_run_id": run["run_id"]})
    async with conn.execute("SELECT * FROM review_events WHERE triage_run_id = ? ORDER BY seq", (run_id,)) as cur:
        events = list(await cur.fetchall())
    return run, events, (await _red_chain_anchors(conn, [case_id]))[case_id]


async def _insert_event(conn, *, case_id, run, kind, principal, old=None, new=None, code=None, text=None) -> tuple[str, str]:
    event_id, created_at = str(uuid.uuid4()), _iso(_now())
    try:
        await conn.execute(
            "INSERT INTO review_events (event_id, case_id, triage_run_id, kind, rules_urgency, old_urgency, new_urgency, reason_code, reason_text, actor_id, actor_role, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, case_id, run["run_id"], kind, run["urgency"], old, new, code, text, principal.user_id, principal.role.value, created_at),
        )
    except sqlite3.IntegrityError as exc:  # one sign-off / one acknowledgment per run (unique partial indexes)
        if "UNIQUE" not in str(exc):
            raise
        code_ = "ALREADY_SIGNED_OFF" if kind == "sign_off" else "ALREADY_ACKNOWLEDGED"
        raise ApiError(409, code_, "This triage run already has this review action") from None
    return event_id, created_at


async def _guarded(conn, principal, case_id, request_id, fn):
    denial: consent.ConsentNotEffective | None = None
    try:
        async with transaction(conn):
            return await fn()
    except consent.ConsentNotEffective as exc:
        denial = exc
    raise await consent.audit_denied(conn, principal, case_id, denial, request_id)


async def sign_off(conn: aiosqlite.Connection, principal: Principal, case_id: str, body: SignOffBody, request_id: str | None) -> dict:
    async def _do() -> dict:
        run, events, chain = await _load_for_action(conn, principal, case_id, str(body.triage_run_id), need_consent=True)
        if any(e["kind"] == "sign_off" for e in events):
            raise ApiError(409, "ALREADY_SIGNED_OFF", "This triage run has already been signed off")
        state = _review_state(run, events, _now(), chain)
        event_id, created_at = await _insert_event(conn, case_id=case_id, run=run, kind="sign_off", principal=principal)
        await audit.record(conn, principal=principal, action="review_signed_off", outcome="success", case_id=case_id, request_id=request_id,
                           details=audit.ReviewSignOffDetails(review_event_id=event_id, triage_run_id=run["run_id"], rules_urgency=run["urgency"],
                                                              effective_urgency=state["effective_urgency"]))
        if state["escalation"] and state["escalation"]["state"] != "acknowledged":
            await _audit_ack(conn, principal, case_id, run["run_id"], state["escalation"]["deadline"], created_at, request_id)
        return {"case_id": case_id, "review_event_id": event_id, "triage_run_id": run["run_id"], "signed_off_at": created_at,
                "reviewer_role": principal.role.value, "rules_urgency": run["urgency"], "effective_urgency": state["effective_urgency"]}

    return await _guarded(conn, principal, case_id, request_id, _do)


async def _reason_has_identifier(text: str) -> bool:
    """Same heuristic as AI-field corrections (docs/16 §8): identifier patterns, then the full redaction pass
    (Presidio NER + patterns). The reason is stored as free text, so identifiers and names are rejected."""
    from app.privacy.pii import _process_raw, residual_hit

    if residual_hit(text):
        return True
    outcome = await anyio.to_thread.run_sync(_process_raw, text)
    return outcome.redacted is None or outcome.redacted.redacted_total > 0


async def override(conn: aiosqlite.Connection, principal: Principal, case_id: str, body: OverrideBody, request_id: str | None) -> dict:
    if body.reason_text is not None and await _reason_has_identifier(body.reason_text):
        raise ApiError(422, "PII_DETECTED", "The reason looks like it contains an identifier or a name; describe the clinical reason only")

    async def _do() -> dict:
        run, events, chain = await _load_for_action(conn, principal, case_id, str(body.triage_run_id), need_consent=True)
        if any(e["kind"] == "sign_off" for e in events):
            raise ApiError(409, "ALREADY_SIGNED_OFF", "This triage run is signed off; a new triage run is needed before overriding")
        old = _review_state(run, events, _now(), chain)["effective_urgency"]
        if body.expected_urgency is not None and body.expected_urgency != old:
            raise ApiError(409, "URGENCY_CHANGED", "Another reviewer changed this case's urgency; nothing was recorded. Reload and review again",
                           {"current_urgency": old})
        if body.new_urgency == old:
            raise ApiError(409, "NO_CHANGE", "The new urgency equals the current urgency")
        event_id, created_at = await _insert_event(conn, case_id=case_id, run=run, kind="override", principal=principal, old=old, new=body.new_urgency,
                                                   code=body.reason_code, text=body.reason_text)
        await audit.record(conn, principal=principal, action="urgency_overridden", outcome="success", case_id=case_id, request_id=request_id,
                           details=audit.UrgencyOverrideDetails(review_event_id=event_id, triage_run_id=run["run_id"], rules_urgency=run["urgency"], old_urgency=old,
                                                                new_urgency=body.new_urgency, reason_code=body.reason_code, has_reason_text=body.reason_text is not None))
        return {"case_id": case_id, "review_event_id": event_id, "triage_run_id": run["run_id"], "rules_urgency": run["urgency"], "old_urgency": old,
                "new_urgency": body.new_urgency, "reason_code": body.reason_code, "overridden_at": created_at, "reviewer_role": principal.role.value}

    return await _guarded(conn, principal, case_id, request_id, _do)


async def correct(conn: aiosqlite.Connection, principal: Principal, case_id: str, body: CorrectionBody, request_id: str | None) -> dict:
    """A reviewer's correction: ONE write transaction (BEGIN IMMEDIATE) that checks the run the reviewer saw is still
    the latest, re-runs the deterministic rules on the corrected input through the same path as intake triage, and
    records a new append-only run naming the corrected run and the reason. The earlier run is never modified. A run
    can be corrected at most once (partial unique index), so a concurrent duplicate also gets 409."""
    if body.reason_text is not None and await _reason_has_identifier(body.reason_text):
        raise ApiError(422, "PII_DETECTED", "The reason looks like it contains an identifier or a name; describe the clinical reason only")

    async def _do() -> dict:
        _require_reviewer(principal)
        case = await consent.load_case(conn, principal, case_id, "triage")
        snap = await consent.require(conn, case_id, "triage")
        runs = await _latest_runs(conn, None, case_id)
        if not runs:
            raise ApiError(409, "NO_TRIAGE_RUN", "This case has no recorded triage run to correct")
        run = runs[0]
        if run["run_id"] != str(body.expected_triage_run_id):
            raise ApiError(409, "STALE_TRIAGE_RUN", "A newer triage run exists for this case; nothing was changed. Reload and review the latest run",
                           {"latest_triage_run_id": run["run_id"]})
        async with conn.execute("SELECT input_json FROM triage_runs WHERE run_id = ?", (run["run_id"],)) as cur:
            stored = (await cur.fetchone())["input_json"]
        if not stored:
            raise ApiError(409, "NO_STORED_INPUT", "This run was recorded before inputs were stored, so it cannot be corrected here")
        changes = input_changes(json.loads(stored), json.loads(body.input.model_dump_json()))
        if not changes:
            raise ApiError(409, "NO_CHANGE", "Nothing was changed. To confirm the recorded values, sign off instead")
        new_id, result, created_at = await case_triage.record_run(conn, principal, case, body.input, snap, request_id,
                                                                   (run["run_id"], body.reason_code, body.reason_text))
        fields = [c["field"] for c in changes]
        await audit.record(conn, principal=principal, action="triage_corrected", outcome="success", case_id=case_id, request_id=request_id,
                           details=audit.TriageCorrectedDetails(from_run_id=run["run_id"], to_run_id=new_id, rules_urgency_before=run["urgency"],
                                                                rules_urgency_after=result.urgency.value, changed_fields=fields, reason_code=body.reason_code,
                                                                has_reason_text=body.reason_text is not None))
        return {"case_id": case_id, "corrects_run_id": run["run_id"], "triage_run_id": new_id, "urgency_before": run["urgency"],
                "urgency": result.urgency.value, "changed_fields": fields, "reason_code": body.reason_code, "corrected_at": created_at,
                "reviewer_role": principal.role.value}

    try:
        return await _guarded(conn, principal, case_id, request_id, _do)
    except sqlite3.IntegrityError as exc:  # the one-correction-per-run index: a concurrent correction won
        raise ApiError(409, "STALE_TRIAGE_RUN", "This run was corrected by another request; nothing was changed. Reload and review the latest run") from exc


async def _audit_ack(conn, principal, case_id, run_id, deadline, created_at, request_id) -> None:
    await audit.record(conn, principal=principal, action="red_escalation_acknowledged", outcome="success", case_id=case_id, request_id=request_id,
                       details=audit.EscalationDetails(triage_run_id=run_id, deadline=deadline,
                                                       seconds_after_deadline=int((_parse(created_at) - _parse(deadline)).total_seconds())))


async def acknowledge(conn: aiosqlite.Connection, principal: Principal, case_id: str, body: AcknowledgeBody, request_id: str | None) -> dict:
    """Acknowledge a RED escalation. Deliberately does not require triage consent: it processes no clinical data
    and must stay possible for an urgent case (it records only who saw the alert and when)."""
    async with transaction(conn):
        run, events, chain = await _load_for_action(conn, principal, case_id, str(body.triage_run_id), need_consent=False)
        state = _review_state(run, events, _now(), chain)
        esc = state["escalation"]
        if esc is None:
            raise ApiError(409, "NOT_ESCALATED", "This triage run is not RED; there is nothing to acknowledge")
        if esc["state"] == "acknowledged":
            raise ApiError(409, "ALREADY_ACKNOWLEDGED", "This escalation was already acknowledged")
        event_id, created_at = await _insert_event(conn, case_id=case_id, run=run, kind="acknowledge", principal=principal)
        await _audit_ack(conn, principal, case_id, run["run_id"], esc["deadline"], created_at, request_id)
    return {"case_id": case_id, "review_event_id": event_id, "triage_run_id": run["run_id"], "acknowledged_at": created_at, "deadline": esc["deadline"],
            "acknowledged_late": _parse(created_at) > _parse(esc["deadline"]), "notification_sent": False}


# ── Governance ───────────────────────────────────────────────────────────


def escalation_episodes(runs: list[dict], events_by_run: dict[str, list], now: datetime) -> list[dict]:
    """Pure: every RED escalation episode of ONE case across all its runs (see `walk_escalations`), as
    {state, late}. Re-running triage neither erases nor duplicates an episode, so governance keeps history (§7)."""
    closed, open_ = walk_escalations(runs, events_by_run)
    out = []
    for ep in closed:
        deadline = _parse(ep["anchor"]) + timedelta(seconds=ESCALATION_SECONDS)
        out.append({"state": "acknowledged", "late": _parse(ep["ack"]["created_at"]) > deadline})
    if open_ is not None:
        deadline = _parse(open_["anchor"]) + timedelta(seconds=ESCALATION_SECONDS)
        out.append({"state": "overdue" if now >= deadline else "pending", "late": None})
    return out


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


async def governance(conn: aiosqlite.Connection, since: datetime | None, until: datetime | None, *,
                     scope: frozenset[str] | None = None) -> dict:
    """Aggregates only: no case ids, tokens, reviewer ids or free text. Period filters on the latest triage run time.
    `scope` (the caller's `Principal.facilities`; None = unrestricted) limits every aggregate to those facilities."""
    now = _now()
    async with read_transaction(conn):
        runs = await _latest_runs(conn, None, scope=scope)
        lo, hi = (_iso(_utc(since)) if since else None), (_iso(_utc(until)) if until else None)
        runs = [r for r in runs if (lo is None or r["run_created_at"] >= lo) and (hi is None or r["run_created_at"] < hi)]
        events = await _events_by_run(conn, [r["run_id"] for r in runs])
        chains = await _red_chain_anchors(conn, [r["case_id"] for r in runs])
        period_cases = {r["case_id"] for r in runs}
        async with conn.execute("SELECT run_id, case_id, urgency, created_at FROM triage_runs ORDER BY seq") as cur:
            case_runs: dict[str, list[dict]] = {}
            for r in await cur.fetchall():
                if r["case_id"] in period_cases:
                    case_runs.setdefault(r["case_id"], []).append(dict(r))
        all_events = await _events_by_run(conn, [r["run_id"] for rs in case_runs.values() for r in rs])
        async with conn.execute("SELECT case_id, old_urgency, new_urgency, reason_code FROM review_events WHERE kind = 'override' ORDER BY seq") as cur:
            all_overrides = [e for e in await cur.fetchall() if e["case_id"] in period_cases]  # every run of the period's cases
        ai_cond, ai_args = _scope_sql(scope)
        ai_sql = ("SELECT e.outcome, COUNT(*) AS n FROM ai_field_review_events e JOIN cases c ON c.case_id = e.case_id "
                  + (f"WHERE {ai_cond} " if ai_cond else "") + "GROUP BY e.outcome")
        async with conn.execute(ai_sql, ai_args) as cur:
            ai_reviews = {r["outcome"]: r["n"] for r in await cur.fetchall()}
    states = [(r, _review_state(r, events[r["run_id"]], now, chains[r["case_id"]])) for r in runs]
    n = len(states)
    signed = [(r, s) for r, s in states if s["review_status"] == "signed_off"]
    overridden = {e["case_id"] for e in all_overrides}
    reasons = {code: sum(1 for e in all_overrides if e["reason_code"] == code) for code in OVERRIDE_REASONS}
    raised = sum(1 for e in all_overrides if _RANK[e["new_urgency"]] > _RANK[e["old_urgency"]])
    turnaround = [(_parse(s["signed_off_at"]) - _parse(r["run_created_at"])).total_seconds() for r, s in signed]
    per_case = {cid: escalation_episodes(rs, all_events, now) for cid, rs in case_runs.items()}
    eps = [e for es in per_case.values() for e in es]
    ack = [e for e in eps if e["state"] == "acknowledged"]
    insufficient = sum(1 for r, _ in states if json.loads(r["result_json"]).get("determination") == "insufficient_data")
    ai_total = sum(ai_reviews.values())
    return {
        "generated_at": _iso(now),
        "period": {"since": lo, "until": hi, "basis": "latest triage run per case, by run time"},
        "facility_scope": sorted(scope) if scope is not None else None,  # None = all facilities
        "sample_size": n,
        "small_sample_warning": n < 30,
        "review_completion": {"signed_off": len(signed), "denominator": n, "rate": _rate(len(signed), n),
                              "definition": "cases whose LATEST triage run is signed off ÷ cases whose latest triage run falls in the period"},
        "overrides": {
            "cases_with_override": len(overridden), "denominator": n, "rate": _rate(len(overridden), n),
            "definition": "cases with ≥1 urgency override on ANY of their triage runs ÷ cases whose latest triage run falls in the period; "
                          "events, raised, lowered and reasons count every override on any run of those cases (re-running triage does not erase them)",
            "events": len(all_overrides), "raised": raised, "lowered": len(all_overrides) - raised,
            "reasons": [{"code": k, "label": OVERRIDE_REASONS[k], "count": v} for k, v in reasons.items()],
            "interpretation": "An override rate is not, by itself, evidence of good or poor triage quality.",
        },
        "red_escalation": {
            "red_cases": sum(1 for es in per_case.values() if es), "episodes": len(eps),
            "pending": sum(1 for e in eps if e["state"] == "pending"), "overdue_unacknowledged": sum(1 for e in eps if e["state"] == "overdue"),
            "acknowledged": len(ack), "acknowledged_within_window": sum(1 for e in ack if not e["late"]),
            "acknowledged_late": sum(1 for e in ack if e["late"]), "window_seconds": ESCALATION_SECONDS,
            "definition": "RED episodes (a rules-engine RED or a reviewer raise to RED, until acknowledged or signed off) on ANY triage run of the "
                          "period's cases; red_cases = cases with ≥1 episode. Re-running triage neither erases nor duplicates an episode",
            "notification_channel": None,
        },
        "turnaround_seconds": {"n": len(turnaround), "median": round(statistics.median(turnaround), 1) if turnaround else None,
                               "definition": "latest triage run time → sign-off time"},
        "insufficient_data": {"count": insufficient, "denominator": n, "rate": _rate(insufficient, n)},
        "ai_field_reviews": {"total": ai_total, "by_outcome": {k: ai_reviews.get(k, 0) for k in ("accepted", "corrected", "rejected", "unsure")},
                             "disagreement_rate": _rate(ai_reviews.get("corrected", 0) + ai_reviews.get("rejected", 0), ai_total),
                             "definition": "(corrected + rejected) ÷ all reviewer decisions on AI-extracted fields; all time, not period-filtered"},
    }
