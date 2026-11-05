"""Patient registry (P1, docs/implementation/p1-patient-registry.md): link a visit (case) to a patient by a SEHAT
patient code the patient holds. No name, no ID number, no fuzzy matching: a code the patient shows, typed by a health
worker who confirms the link. Nothing is merged; a wrong link is undone by an append-only unlink event.

- Consent: a link needs `link_visits` (and therefore `triage`) consent in effect on the visit being linked. A linked
  visit is shown with the patient's other visits only while its own `link_visits` consent is in effect.
- Access: creating or confirming a link is the visit's ANM (`load_case(..., "write")`); unlinking a wrong link may
  also be done by a medical officer (`"triage"`). Facility scope applies to the visit AND to the patient's home
  facility; a patient outside the account's scope looks exactly like an unknown code (same 404).
- The patient code is a locator, so it is never written to the audit log or to application logs. A check character
  catches a mistyped code before any lookup (single-character substitutions are always detected).
"""

import secrets
import uuid
from datetime import datetime, timezone
from typing import Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from app import audit, consent
from app.auth import Principal, Role
from app.database import read_transaction, transaction
from app.errors import ApiError, not_found

# Crockford-style alphabet: no I, L, O, U (easily confused or offensive); 32 symbols.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CONFUSABLE = {"O": "0", "I": "1", "L": "1"}
_WEIGHTS = (1, 3, 5, 7, 9, 11, 13)  # odd, so invertible mod 32: every single-character substitution changes the check
CODE_PREFIX = "SP-"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class CodeBody(_Body):
    patient_code: str = Field(pattern=r"^[A-Za-z0-9 -]{8,16}$")


class LinkBody(CodeBody):
    confirm: Literal[True]  # the health worker confirms the code on the patient's slip matches this patient


class UnlinkBody(_Body):
    reason_code: Literal["linked_in_error", "patient_request"]
    confirm: Literal[True]


def _check_char(body: str) -> str:
    return ALPHABET[sum(w * ALPHABET.index(c) for w, c in zip(_WEIGHTS, body)) % 32]


def new_code() -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(7))  # 35 random bits
    full = body + _check_char(body)
    return f"{CODE_PREFIX}{full[:4]}-{full[4:]}"


def normalize_code(text: str) -> str | None:
    """Canonical "SP-XXXX-XXXX", or None when the code is malformed or its check character is wrong (a typo)."""
    t = "".join(ch for ch in text.upper() if ch.isalnum())
    if t.startswith("SP") and len(t) == 10:
        t = t[2:]
    t = "".join(_CONFUSABLE.get(ch, ch) for ch in t)
    if len(t) != 8 or any(ch not in ALPHABET for ch in t) or _check_char(t[:7]) != t[7]:
        return None
    return f"{CODE_PREFIX}{t[:4]}-{t[4:]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


async def current_link(conn: aiosqlite.Connection, case_id: str) -> aiosqlite.Row | None:
    """The case's latest link event if it is a link (the case is linked), else None."""
    async with conn.execute("SELECT * FROM patient_link_events WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)) as cur:
        row = await cur.fetchone()
    return row if row is not None and row["action"] == "link" else None


async def _patient_by_code(conn, principal: Principal, code_text: str) -> aiosqlite.Row:
    code = normalize_code(code_text)
    if code is None:
        raise ApiError(400, "PATIENT_CODE_INVALID", "This code is not valid. Check each character on the slip and type it again.")
    async with conn.execute("SELECT * FROM patients WHERE patient_code = ?", (code,)) as cur:
        row = await cur.fetchone()
    if row is None or not principal.may_access_facility(row["facility_code"]):
        raise not_found()  # unknown, or outside this account's facilities: indistinguishable
    return row


async def _visits(conn, principal: Principal, patient_id: str) -> list[dict]:
    """The patient's currently linked visits that this account may read and whose `link_visits` consent is in effect.
    Ids, dates and scenario only: clinical content stays behind each visit's own endpoints and checks."""
    async with conn.execute(
        "SELECT e.case_id FROM patient_link_events e WHERE e.patient_id = ? AND e.seq = "
        "(SELECT MAX(seq) FROM patient_link_events x WHERE x.case_id = e.case_id) AND e.action = 'link' ORDER BY e.seq",
        (patient_id,),
    ) as cur:
        case_ids = [r["case_id"] for r in await cur.fetchall()]
    out = []
    for cid in case_ids:
        try:
            row = await consent.load_case(conn, principal, cid, "read")
        except ApiError:
            continue
        if not (await consent.snapshot(conn, cid)).is_effective("link_visits"):
            continue
        out.append({"case_id": cid, "visit_date": row["created_at"][:10], "facility_code": row["facility_code"], "scenario": row["scenario"]})
    return out


async def _write_gate(conn, principal: Principal, case_id: str) -> consent.ConsentSnapshot:
    """The visit's ANM, with `link_visits` consent in effect (raises ConsentNotEffective)."""
    if principal.role != Role.ANM:
        raise ApiError(403, "FORBIDDEN", "Role does not have permission")
    await consent.load_case(conn, principal, case_id, "write")
    return await consent.require(conn, case_id, "link_visits")


async def _guarded(conn, principal: Principal, case_id: str, request_id, fn):
    """Run `fn` in one write transaction behind the gate; a missing consent is audited (consent_denied) and refused."""
    denial = None
    result = None
    try:
        async with transaction(conn):
            snap = await _write_gate(conn, principal, case_id)
            result = await fn(snap)
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    return result


async def _link(conn, principal: Principal, case_id: str, patient_id: str, basis: str, snap: consent.ConsentSnapshot, request_id, action: str) -> str:
    event_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO patient_link_events (event_id, patient_id, case_id, action, basis, consent_seq, actor_id, actor_role, created_at) "
        "VALUES (?, ?, ?, 'link', ?, ?, ?, ?, ?)",
        (event_id, patient_id, case_id, basis, snap.latest_seq["link_visits"], principal.user_id, principal.role.value, _now()),
    )
    await audit.record(conn, principal=principal, action=action, outcome="success", case_id=case_id, request_id=request_id,  # type: ignore[arg-type]
                       details=audit.PatientLinkDetails(patient_id=patient_id, link_event_id=event_id, basis=basis))  # type: ignore[arg-type]
    return event_id


async def register_new(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id=None) -> dict:
    """A first visit: create a patient with a fresh code and link this visit to it."""
    async def fn(snap):
        if await current_link(conn, case_id) is not None:
            raise ApiError(409, "VISIT_ALREADY_LINKED", "This visit is already linked to a patient. Unlink it first if that link is wrong.")
        async with conn.execute("SELECT facility_code FROM cases WHERE case_id = ?", (case_id,)) as cur:
            facility = (await cur.fetchone())["facility_code"]
        patient_id = str(uuid.uuid4())
        for _ in range(5):  # a collision in 35 random bits is very unlikely; never reuse an existing code
            code = new_code()
            async with conn.execute("SELECT 1 FROM patients WHERE patient_code = ?", (code,)) as cur:
                if await cur.fetchone() is None:
                    break
        else:
            raise ApiError(503, "CODE_UNAVAILABLE", "Could not issue a code; try again")
        await conn.execute("INSERT INTO patients (patient_id, patient_code, facility_code, created_by, created_by_role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (patient_id, code, facility, principal.user_id, principal.role.value, _now()))
        await _link(conn, principal, case_id, patient_id, "new_code", snap, request_id, "patient_registered")
        return {"case_id": case_id, "patient_code": code, "linked": True,
                "note": "Write this code on a slip for the patient. Shown again at a later visit, it links that visit to this one."}
    return await _guarded(conn, principal, case_id, request_id, fn)


async def lookup(conn: aiosqlite.Connection, principal: Principal, case_id: str, body: CodeBody, request_id=None) -> dict:
    """Before linking: is this a known patient, and how many linked visits can this account see? Read-only. Only behind
    the same gate as linking, so it is not a free oracle for codes."""
    async def fn(_snap):
        p = await _patient_by_code(conn, principal, body.patient_code)
        visits = await _visits(conn, principal, p["patient_id"])
        return {"patient_code": p["patient_code"], "visible_visits": len(visits), "first_visit_date": visits[0]["visit_date"] if visits else None,
                "already_linked_here": any(v["case_id"] == case_id for v in visits)}
    return await _guarded(conn, principal, case_id, request_id, fn)


async def link_existing(conn: aiosqlite.Connection, principal: Principal, case_id: str, body: LinkBody, request_id=None) -> dict:
    """A return visit: link this visit to the patient whose code the health worker typed and confirmed."""
    async def fn(snap):
        p = await _patient_by_code(conn, principal, body.patient_code)
        cur_link = await current_link(conn, case_id)
        if cur_link is not None:
            if cur_link["patient_id"] == p["patient_id"]:
                return {"case_id": case_id, "patient_code": p["patient_code"], "linked": True, "unchanged": True}
            raise ApiError(409, "VISIT_ALREADY_LINKED", "This visit is already linked to a different patient. Unlink it first if that link is wrong.")
        await _link(conn, principal, case_id, p["patient_id"], "patient_code", snap, request_id, "patient_linked")
        return {"case_id": case_id, "patient_code": p["patient_code"], "linked": True}
    return await _guarded(conn, principal, case_id, request_id, fn)


async def unlink(conn: aiosqlite.Connection, principal: Principal, case_id: str, body: UnlinkBody, request_id=None) -> dict:
    """Undo a link (wrong patient, or the patient asks). The visit's ANM or a medical officer; no consent needed, so a
    wrong link can always be undone. The link stays in the history; nothing is deleted."""
    if principal.role not in (Role.ANM, Role.MEDICAL_OFFICER):
        raise ApiError(403, "FORBIDDEN", "Role does not have permission")
    async with transaction(conn):
        await consent.load_case(conn, principal, case_id, "write" if principal.role == Role.ANM else "triage")
        cur_link = await current_link(conn, case_id)
        if cur_link is None:
            raise ApiError(409, "VISIT_NOT_LINKED", "This visit is not linked to a patient")
        event_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO patient_link_events (event_id, patient_id, case_id, action, reason_code, actor_id, actor_role, created_at) VALUES (?, ?, ?, 'unlink', ?, ?, ?, ?)",
            (event_id, cur_link["patient_id"], case_id, body.reason_code, principal.user_id, principal.role.value, _now()),
        )
        await audit.record(conn, principal=principal, action="patient_unlinked", outcome="success", case_id=case_id, request_id=request_id,
                           details=audit.PatientLinkDetails(patient_id=cur_link["patient_id"], link_event_id=event_id, reason_code=body.reason_code))
    return {"case_id": case_id, "linked": False}


async def view(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id=None) -> dict:
    """This visit's link and the patient's other visits this account may see. Requires read access to this visit and
    `triage` consent; the other visits are listed only while this visit's own `link_visits` consent is in effect."""
    if principal.role not in (Role.ANM, Role.MEDICAL_OFFICER):
        raise ApiError(403, "FORBIDDEN", "Role does not have permission")
    denial = None
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "write" if principal.role == Role.ANM else "triage")
            await consent.require(conn, case_id, "triage")
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    async with read_transaction(conn):
        snap = await consent.snapshot(conn, case_id)
        link = await current_link(conn, case_id)
        if link is None:
            return {"case_id": case_id, "linked": False, "link_visits_consent": snap.effective("link_visits"), "patient_code": None, "other_visits": []}
        async with conn.execute("SELECT patient_code FROM patients WHERE patient_id = ?", (link["patient_id"],)) as cur:
            code = (await cur.fetchone())["patient_code"]
        shown = snap.is_effective("link_visits")
        others = [v for v in await _visits(conn, principal, link["patient_id"]) if v["case_id"] != case_id] if shown else []
    return {"case_id": case_id, "linked": True, "link_visits_consent": snap.effective("link_visits"), "patient_code": code,
            "linked_by_role": link["actor_role"], "linked_at": link["created_at"], "basis": link["basis"], "other_visits": others,
            "other_visits_hidden_reason": None if shown else "link_visits_consent_not_in_effect"}
