"""Referral closure tracking service (docs/21; architecture §16; docs/06 §2.5).

Safety model (docs/implementation/h0-1-wip-routers.md lists what the earlier, unregistered router lacked):
- Only a medical officer refers, and only from the case's latest triage run after it was signed off. The packet's
  urgency is the rules-engine urgency, raised (never lowered) by a reviewer override, fixed when the referral is made.
- Every action checks case access and facility scope (`consent.load_case`) before anything else, and triage consent
  inside the write transaction; a consent refusal is audited. Unknown and other-facility cases are the same 404.
- Requests carry closed-list codes only. Packets and status events are append-only rows (database triggers); a status
  can only move forward (`options.TRANSITIONS`), and the caller states the status it saw (`expected_status`).
- One audit event per action, ids and codes only. Overdue (48 hours without an update before arrival) is computed
  when read; it is a displayed state, not a delivered alert.
- Supervisors see tracking metadata only (no reason, transport, outcome or letter), as everywhere else (docs/11).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import audit, consent, review_queue
from app.auth import Principal, Role
from app.database import read_transaction, transaction
from app.errors import ApiError, not_found
from app.referral_notes import fhir, letter as letter_mod, options as o

_RANK = {"GREEN": 0, "YELLOW": 1, "RED": 2}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="microseconds")


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReferralCreate(_Body):
    triage_run_id: uuid.UUID
    to_facility: o.DestinationCode
    reason_code: o.ReasonCode
    transport_mode: o.TransportMode
    escort_required: bool = Field(strict=True)
    travel_minutes: int | None = Field(default=None, ge=1, le=1440, strict=True)
    insurance_scheme: o.InsuranceScheme
    confirm: Literal[True]


class StatusUpdate(_Body):
    status: o.NextStatus
    expected_status: o.Status
    detail_code: o.OutcomeCode | o.BarrierCode | None = None
    confirm: Literal[True]

    @model_validator(mode="after")
    def _detail_matches_status(self) -> "StatusUpdate":
        if self.status == "outcome_recorded" and self.detail_code not in o.OUTCOMES:
            raise ValueError("outcome_recorded needs an outcome code: " + ", ".join(o.OUTCOMES))
        if self.status == "not_reached" and self.detail_code not in o.BARRIERS:
            raise ValueError("not_reached needs a barrier code: " + ", ".join(o.BARRIERS))
        if self.status not in ("outcome_recorded", "not_reached") and self.detail_code is not None:
            raise ValueError("detail_code applies only to outcome_recorded and not_reached")
        return self


# ── Reads ────────────────────────────────────────────────────────────────────────────────────────────────────────
_PACKET_SQL = ("SELECT p.*, c.patient_token, c.scenario, c.facility_code FROM referral_packets p "
               "JOIN cases c ON c.case_id = p.case_id ")


async def _packet(conn: aiosqlite.Connection, case_id: str, referral_id: str) -> dict:
    """The referral if it belongs to this case; otherwise the same 404 as a missing one."""
    async with conn.execute(_PACKET_SQL + "WHERE p.referral_id = ? AND p.case_id = ?", (referral_id, case_id)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise not_found()
    return dict(row)


async def _events(conn: aiosqlite.Connection, referral_ids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {rid: [] for rid in referral_ids}
    if not referral_ids:
        return out
    marks = ",".join("?" * len(referral_ids))
    async with conn.execute(f"SELECT * FROM referral_events WHERE referral_id IN ({marks}) ORDER BY seq", referral_ids) as cur:
        for row in await cur.fetchall():
            out[row["referral_id"]].append(dict(row))
    return out


async def _review_events(conn: aiosqlite.Connection, run_id: str) -> list[aiosqlite.Row]:
    async with conn.execute("SELECT * FROM review_events WHERE triage_run_id = ? ORDER BY seq", (run_id,)) as cur:
        return list(await cur.fetchall())


def _review_state(run: dict, events: list, now: datetime) -> dict:
    # The review queue's own read model (effective and raise-only priority urgency, sign-off), so a referral can never
    # show an urgency the review screens would not.
    return review_queue._review_state({"urgency": run["urgency"], "run_id": run["run_id"], "run_created_at": run["created_at"]}, events, now)


async def _open_referral(conn: aiosqlite.Connection, case_id: str) -> str | None:
    async with conn.execute("SELECT referral_id FROM referral_packets WHERE case_id = ? ORDER BY seq", (case_id,)) as cur:
        ids = [r["referral_id"] for r in await cur.fetchall()]
    events = await _events(conn, ids)
    return next((rid for rid in ids if events[rid] and events[rid][-1]["status"] not in o.TERMINAL), None)


def _history(events: list[dict], clinical: bool) -> list[dict]:
    rows = letter_mod.history(events)
    if not clinical:  # outcome codes are clinical; supervisors see the step and its time
        rows = [{k: v for k, v in r.items() if k not in ("detail_code", "detail_label")} for r in rows]
    return rows


def _view(packet: dict, events: list[dict], now: datetime, *, clinical: bool, may_update: bool) -> dict:
    last = events[-1]
    last_at = _parse(last["created_at"])
    view: dict[str, Any] = {
        "referral_id": packet["referral_id"],
        "case_id": packet["case_id"],
        "patient_token": packet["patient_token"],
        "from_facility": o.facility_view(packet["from_facility"]),
        "to_facility": o.facility_view(packet["to_facility"]),
        "urgency": packet["urgency"],
        "triage_run_id": packet["triage_run_id"],
        "status": last["status"],
        "status_label": o.STATUS_LABELS[last["status"]],
        "created_at": packet["created_at"],
        "last_update_at": last["created_at"],
        "hours_since_update": o.hours_since(last_at, now),
        "overdue": o.is_overdue(last["status"], last_at, now),
        "overdue_threshold_hours": o.OVERDUE_HOURS,
        "status_history": _history(events, clinical),
    }
    if clinical:
        view["reason"] = {"code": packet["reason_code"], "label": o.REASONS[packet["reason_code"]]}
        view["transport"] = {"mode": packet["transport_mode"], "label": o.TRANSPORT[packet["transport_mode"]],
                             "escort_required": bool(packet["escort_required"]), "travel_minutes": packet["travel_minutes"]}
        view["insurance"] = {"scheme": packet["insurance_scheme"], "label": o.INSURANCE[packet["insurance_scheme"]]}
    view["allowed_next"] = list(o.TRANSITIONS[last["status"]]) if may_update else []
    return view


def _options() -> dict:
    return {
        "destinations": o.destinations(),
        "reasons": o.labelled(o.REASONS),
        "transport": o.labelled(o.TRANSPORT),
        "insurance": o.labelled(o.INSURANCE),
        "outcomes": o.labelled(o.OUTCOMES),
        "barriers": o.labelled(o.BARRIERS),
        "statuses": o.labelled(o.STATUS_LABELS),
        "facility_list_note": "Demo facility list. Replace with the state's facility registry before real use.",
    }


async def _refer_blocked(conn: aiosqlite.Connection, case_id: str, consent_ok: bool) -> tuple[str | None, str | None]:
    """(reason the medical officer cannot refer now, latest run id). None = a referral can be made."""
    runs = await review_queue._latest_runs(conn, None, case_id)
    if not runs:
        return "no_triage_run", None
    run_id = runs[0]["run_id"]
    if not consent_ok:
        return "consent", run_id
    if not any(e["kind"] == "sign_off" for e in await _review_events(conn, run_id)):
        return "sign_off_required", run_id
    if await _open_referral(conn, case_id):
        return "referral_open", run_id
    return None, run_id


async def case_referrals(conn: aiosqlite.Connection, principal: Principal, case_id: str) -> dict:
    async with read_transaction(conn):
        await consent.load_case(conn, principal, case_id, "read")
        snap = await consent.snapshot(conn, case_id)
        consent_ok = snap.is_effective("triage")
        clinical = principal.role in (Role.ANM, Role.MEDICAL_OFFICER) and consent_ok
        async with conn.execute(_PACKET_SQL + "WHERE p.case_id = ? ORDER BY p.seq", (case_id,)) as cur:
            packets = [dict(r) for r in await cur.fetchall()]
        events = await _events(conn, [p["referral_id"] for p in packets])
        if principal.role == Role.MEDICAL_OFFICER:
            blocked, latest_run = await _refer_blocked(conn, case_id, consent_ok)
        else:
            blocked, latest_run = "role", None
    now = _now()
    may_update = clinical  # the creating ANM or a medical officer (load_case "triage" is re-checked on every update)
    out: dict[str, Any] = {
        "case_id": case_id,
        "clinical_content_available": clinical,
        "can_refer": blocked is None,
        "refer_blocked": blocked,
        "latest_triage_run_id": latest_run,
        "overdue_threshold_hours": o.OVERDUE_HOURS,
        "referrals": [_view(p, events[p["referral_id"]], now, clinical=clinical, may_update=may_update) for p in packets],
    }
    if clinical:
        out["options"] = _options()
    return out


async def tracking(conn: aiosqlite.Connection, principal: Principal, overdue_only: bool) -> dict:
    """Open referrals in the caller's facility scope (docs/05 §5.2): metadata only, overdue first."""
    async with read_transaction(conn):
        cond, args = review_queue._scope_sql(principal.facilities)
        sql = _PACKET_SQL + (f"WHERE {cond} " if cond is not None else "") + "ORDER BY p.seq"
        async with conn.execute(sql, args) as cur:
            packets = [dict(r) for r in await cur.fetchall()]
        events = await _events(conn, [p["referral_id"] for p in packets])
    now = _now()
    items = [_view(p, events[p["referral_id"]], now, clinical=False, may_update=False)
             for p in packets if events[p["referral_id"]] and events[p["referral_id"]][-1]["status"] not in o.TERMINAL]
    for item in items:
        item.pop("status_history")
        item.pop("allowed_next")
    counts = {s: sum(1 for i in items if i["status"] == s) for s in ("referred", "in_transit", "reached", "seen")}
    counts["overdue"] = sum(1 for i in items if i["overdue"])
    if overdue_only:
        items = [i for i in items if i["overdue"]]
    items.sort(key=lambda i: (not i["overdue"], -_RANK[i["urgency"]], i["last_update_at"]))
    return {"generated_at": _iso(now), "overdue_threshold_hours": o.OVERDUE_HOURS, "counts": counts, "items": items}


async def _letter_for(conn: aiosqlite.Connection, packet: dict) -> dict:
    async with conn.execute("SELECT run_id, urgency, result_json, input_json, created_at FROM triage_runs WHERE run_id = ?",
                            (packet["triage_run_id"],)) as cur:
        row = dict(await cur.fetchone())
    run = {**row, "result": json.loads(row["result_json"]), "input": json.loads(row["input_json"]) if row["input_json"] else None}
    # Review actions up to the moment of referral only: the letter shows the review the referral was made on.
    made = _parse(packet["created_at"])
    reviews = [e for e in await _review_events(conn, packet["triage_run_id"]) if _parse(e["created_at"]) <= made]
    state = _review_state(run, reviews, _parse(packet["created_at"]))
    events = (await _events(conn, [packet["referral_id"]]))[packet["referral_id"]]
    return letter_mod.build(packet, events, run, state)


async def letter(conn: aiosqlite.Connection, principal: Principal, case_id: str, referral_id: str, request_id: str | None) -> dict:
    denial: consent.ConsentNotEffective | None = None
    async with read_transaction(conn):
        await consent.load_case(conn, principal, case_id, "triage")
        packet = await _packet(conn, case_id, referral_id)
        try:
            await consent.require(conn, case_id, "triage")
        except consent.ConsentNotEffective as exc:
            denial = exc
        else:
            return await _letter_for(conn, packet)
    raise await consent.audit_denied(conn, principal, case_id, denial, request_id)


# ── Writes ───────────────────────────────────────────────────────────────────────────────────────────────────────
async def _guarded(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id: str | None, fn):
    denial: consent.ConsentNotEffective | None = None
    try:
        async with transaction(conn):
            return await fn()
    except consent.ConsentNotEffective as exc:
        denial = exc
    raise await consent.audit_denied(conn, principal, case_id, denial, request_id)


async def _insert_event(conn: aiosqlite.Connection, *, referral_id: str, case_id: str, status: str, detail_code: str | None,
                        principal: Principal, created_at: str) -> None:
    await conn.execute(
        "INSERT INTO referral_events (event_id, referral_id, case_id, status, detail_code, actor_id, actor_role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), referral_id, case_id, status, detail_code, principal.user_id, principal.role.value, created_at),
    )


async def create(conn: aiosqlite.Connection, principal: Principal, case_id: str, body: ReferralCreate, request_id: str | None) -> dict:
    async def _do() -> str:
        if principal.role != Role.MEDICAL_OFFICER:  # defence in depth: the route already requires a medical officer
            raise ApiError(403, "FORBIDDEN", "Only a medical officer can refer a patient")
        case = await consent.load_case(conn, principal, case_id, "triage")
        snap = await consent.require(conn, case_id, "triage")
        runs = await review_queue._latest_runs(conn, None, case_id)
        if not runs:
            raise ApiError(409, "NO_TRIAGE_RUN", "This case has no recorded triage run to refer from")
        run = runs[0]
        if run["run_id"] != str(body.triage_run_id):
            raise ApiError(409, "STALE_TRIAGE_RUN", "A newer triage run exists for this case; review and sign it off first",
                           {"latest_triage_run_id": run["run_id"]})
        reviews = await _review_events(conn, run["run_id"])
        sign_off = next((e for e in reviews if e["kind"] == "sign_off"), None)
        if sign_off is None:
            raise ApiError(409, "SIGN_OFF_REQUIRED", "Sign off the latest triage run before referring the patient")
        if await _open_referral(conn, case_id):
            raise ApiError(409, "REFERRAL_OPEN", "This case already has an open referral; record how it ended before referring again")
        if body.to_facility == case["facility_code"]:
            raise ApiError(409, "SAME_FACILITY", "The referral facility must be a different facility")
        state = review_queue._review_state(run, reviews, _now())
        referral_id, created_at = str(uuid.uuid4()), _iso(_now())
        await conn.execute(
            "INSERT INTO referral_packets (referral_id, case_id, triage_run_id, sign_off_event_id, from_facility, to_facility, urgency, "
            "reason_code, transport_mode, escort_required, travel_minutes, insurance_scheme, consent_seq, actor_id, actor_role, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (referral_id, case_id, run["run_id"], sign_off["event_id"], case["facility_code"], body.to_facility, state["priority_urgency"],
             body.reason_code, body.transport_mode, int(body.escort_required), body.travel_minutes, body.insurance_scheme,
             snap.authz_seq, principal.user_id, principal.role.value, created_at),
        )
        await _insert_event(conn, referral_id=referral_id, case_id=case_id, status="referred", detail_code=None,
                            principal=principal, created_at=created_at)
        await audit.record(conn, principal=principal, action="referral_created", outcome="success", case_id=case_id, request_id=request_id,
                           details=audit.ReferralCreatedDetails(referral_id=referral_id, triage_run_id=run["run_id"],
                                                                urgency=state["priority_urgency"], to_facility=body.to_facility))
        return referral_id

    referral_id = await _guarded(conn, principal, case_id, request_id, _do)
    return await _one(conn, principal, case_id, referral_id)


async def record_status(conn: aiosqlite.Connection, principal: Principal, case_id: str, referral_id: str, body: StatusUpdate,
                        request_id: str | None) -> dict:
    async def _do() -> None:
        if principal.role not in (Role.ANM, Role.MEDICAL_OFFICER):
            raise ApiError(403, "FORBIDDEN", "Only the health worker or a medical officer can update a referral")
        await consent.load_case(conn, principal, case_id, "triage")  # the creating or handling ANM, or a medical officer
        await _packet(conn, case_id, referral_id)
        await consent.require(conn, case_id, "triage")
        events = (await _events(conn, [referral_id]))[referral_id]
        current = events[-1]["status"]
        if body.expected_status != current:
            raise ApiError(409, "STALE_REFERRAL_STATUS", "The referral changed since you loaded it; reload and try again",
                           {"current_status": current})
        if body.status not in o.TRANSITIONS[current]:
            raise ApiError(409, "INVALID_TRANSITION", f"A referral cannot go from '{current}' to '{body.status}'",
                           {"allowed_next": list(o.TRANSITIONS[current])})
        await _insert_event(conn, referral_id=referral_id, case_id=case_id, status=body.status, detail_code=body.detail_code,
                            principal=principal, created_at=_iso(_now()))
        await audit.record(conn, principal=principal, action="referral_status_recorded", outcome="success", case_id=case_id,
                           request_id=request_id,
                           details=audit.ReferralStatusDetails(referral_id=referral_id, from_status=current, to_status=body.status,
                                                               has_detail=body.detail_code is not None))

    await _guarded(conn, principal, case_id, request_id, _do)
    return await _one(conn, principal, case_id, referral_id)


async def fhir_export(conn: aiosqlite.Connection, principal: Principal, case_id: str, referral_id: str, request_id: str | None) -> dict:
    """The referral as a FHIR R4 document Bundle. Data leaving SEHAT in a portable form, so it is audited."""
    async def _do() -> dict:
        if principal.role != Role.MEDICAL_OFFICER:
            raise ApiError(403, "FORBIDDEN", "Only a medical officer can export a referral")
        await consent.load_case(conn, principal, case_id, "triage")
        packet = await _packet(conn, case_id, referral_id)
        await consent.require(conn, case_id, "triage")
        bundle = fhir.build_bundle(await _letter_for(conn, packet))
        await audit.record(conn, principal=principal, action="referral_exported", outcome="success", case_id=case_id, request_id=request_id,
                           details=audit.ReferralExportedDetails(referral_id=referral_id, format="fhir_r4"))
        return bundle

    return await _guarded(conn, principal, case_id, request_id, _do)


async def _one(conn: aiosqlite.Connection, principal: Principal, case_id: str, referral_id: str) -> dict:
    view = await case_referrals(conn, principal, case_id)
    return next(r for r in view["referrals"] if r["referral_id"] == referral_id)
