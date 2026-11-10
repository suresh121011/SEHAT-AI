"""Patient identity binding (docs/security/post-remediation-hardening.md A3; owner decision H3).

A real patient login (OIDC, role `patient`) is bound to a registered patient (`patients.patient_id`) only through an
explicit confirmation: the visit's ANM issues a single-use binding code (valid 15 minutes) for the patient that visit
is linked to, and the logged-in patient enters it. Nothing is matched by name, symptoms or AI, and nothing is merged.

Once bound, the patient may read the visits linked to them whose `link_visits` consent is in effect (enforced in
`app.consent.load_case`). Past visits are listed as past visits: dates and scenario, never as current symptoms.
A shared demo patient account can never be bound (it is not one person).

The binding code is a credential: only its SHA-256 is stored and it never reaches a log or the audit log.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from app import audit, consent
from app.auth import Principal, Role
from app.database import read_transaction, transaction
from app.errors import ApiError
from app.patient_registry import ALPHABET, _check_char, current_link

CODE_PREFIX = "SB-"
CODE_TTL = timedelta(minutes=15)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class BindBody(_Body):
    binding_code: str = Field(pattern=r"^[A-Za-z0-9 -]{8,16}$")


class UnbindBody(_Body):
    confirm: Literal[True]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="microseconds")


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def new_code() -> str:
    import secrets

    body = "".join(secrets.choice(ALPHABET) for _ in range(7))  # 35 random bits, single use, 15 minutes, rate-limited
    full = body + _check_char(body)
    return f"{CODE_PREFIX}{full[:4]}-{full[4:]}"


def normalize(text: str) -> str | None:
    t = "".join(ch for ch in text.upper() if ch.isalnum())
    if t.startswith("SB") and len(t) == 10:
        t = t[2:]
    t = t.replace("O", "0").replace("I", "1").replace("L", "1")
    if len(t) != 8 or any(ch not in ALPHABET for ch in t) or _check_char(t[:7]) != t[7]:
        return None
    return f"{CODE_PREFIX}{t[:4]}-{t[4:]}"


async def bound_patient(conn: aiosqlite.Connection, user_id: str) -> str | None:
    """The patient this login is currently bound to (its latest identity event is a bind), else None."""
    async with conn.execute("SELECT action, patient_id FROM patient_identity_events WHERE user_id = ? ORDER BY seq DESC LIMIT 1", (user_id,)) as cur:
        r = await cur.fetchone()
    return r["patient_id"] if r is not None and r["action"] == "bind" else None


async def _session_identity(conn, principal: Principal) -> tuple[str, str] | None:
    """(issuer, subject) of an OIDC patient session, or None (demo, staff, or no session)."""
    if principal.role != Role.PATIENT or principal.auth_mode != "oidc" or principal.session_id is None:
        return None
    async with conn.execute("SELECT issuer, subject FROM sessions WHERE session_id = ?", (principal.session_id,)) as cur:
        r = await cur.fetchone()
    return (r["issuer"], r["subject"]) if r is not None and r["issuer"] else None


async def _audit(conn, principal, result: str, request_id, case_id=None, **ids) -> None:
    await audit.record(conn, principal=principal, action={"issued": "patient_binding_code_issued", "unbound": "patient_identity_unbound",
                                                          "not_bound": "patient_identity_unbound"}.get(result, "patient_identity_bound"),
                       outcome="success" if result in ("issued", "bound", "already_bound", "unbound") else "denied",
                       case_id=case_id, request_id=request_id, details=audit.PatientIdentityDetails(result=result, **ids))  # type: ignore[arg-type]


async def issue_code(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id=None) -> dict:
    """The visit's ANM issues a binding code for the patient this visit is linked to (needs `link_visits` consent)."""
    if principal.role != Role.ANM:
        raise ApiError(403, "FORBIDDEN", "Role does not have permission")
    denial = None
    out = None
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "write")
            await consent.require(conn, case_id, "link_visits")
            link = await current_link(conn, case_id)
            if link is None:
                raise ApiError(409, "VISIT_NOT_LINKED", "Link this visit to a patient first")
            code, now, binding_id = new_code(), _now(), str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO patient_binding_codes (binding_id, code_sha256, patient_id, case_id, issued_by, issued_by_role, issued_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (binding_id, _hash(code), link["patient_id"], case_id, principal.user_id, principal.role.value, _iso(now), _iso(now + CODE_TTL)),
            )
            await _audit(conn, principal, "issued", request_id, case_id, patient_id=link["patient_id"], binding_id=binding_id)
            out = {"binding_code": code, "expires_at": _iso(now + CODE_TTL), "single_use": True,
                   "note": "Give this code to the patient only. They enter it once, after signing in, to see their own visits."}
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    return out  # type: ignore[return-value]


_INVALID = ("BINDING_CODE_INVALID", "This code is not valid, has expired or was already used. Ask the health worker for a new one.")


async def bind(conn: aiosqlite.Connection, principal: Principal, body: BindBody, request_id=None) -> dict:
    """A signed-in (OIDC) patient enters the code. Unknown, expired and used codes all look the same."""
    ident = await _session_identity(conn, principal)
    if ident is None:
        async with transaction(conn):
            await _audit(conn, principal, "identity_required", request_id)
        raise ApiError(403, "IDENTITY_REQUIRED", "Sign in with your own account to link your record")
    code = normalize(body.binding_code)
    result: tuple | None = None
    async with transaction(conn):
        row = None
        if code is not None:
            async with conn.execute("SELECT * FROM patient_binding_codes WHERE code_sha256 = ?", (_hash(code),)) as cur:
                row = await cur.fetchone()
        if row is None or row["used_at"] is not None or _iso(_now()) >= row["expires_at"]:
            await _audit(conn, principal, "code_invalid", request_id)
            result = ("invalid",)
        else:
            current = await bound_patient(conn, principal.user_id)
            if current is not None and current != row["patient_id"]:
                await _audit(conn, principal, "bound_to_other", request_id)
                result = ("other",)
            else:
                cur = await conn.execute("UPDATE patient_binding_codes SET used_at = ?, used_by = ? WHERE binding_id = ? AND used_at IS NULL",
                                         (_iso(_now()), principal.user_id, row["binding_id"]))
                if cur.rowcount != 1:  # used concurrently by someone else a moment ago
                    await _audit(conn, principal, "code_invalid", request_id)
                    result = ("invalid",)
                elif current == row["patient_id"]:
                    await _audit(conn, principal, "already_bound", request_id, patient_id=current, binding_id=row["binding_id"])
                    result = ("ok", current, True)
                else:
                    await conn.execute(
                        "INSERT INTO patient_identity_events (event_id, user_id, issuer, subject, patient_id, action, binding_id, actor_id, actor_role, created_at) "
                        "VALUES (?, ?, ?, ?, ?, 'bind', ?, ?, ?, ?)",
                        (str(uuid.uuid4()), principal.user_id, ident[0], ident[1], row["patient_id"], row["binding_id"], principal.user_id,
                         principal.role.value, _iso(_now())))
                    await _audit(conn, principal, "bound", request_id, patient_id=row["patient_id"], binding_id=row["binding_id"])
                    result = ("ok", row["patient_id"], False)
    if result[0] == "invalid":
        raise ApiError(404, *_INVALID)
    if result[0] == "other":
        raise ApiError(409, "ALREADY_BOUND", "This account is already linked to a different record. Unlink it first if that link is wrong.")
    return {"bound": True, "unchanged": result[2]}


async def _unbind(conn, principal: Principal, user_id: str, reason: str, request_id) -> bool:
    async with conn.execute("SELECT issuer, subject, patient_id, action FROM patient_identity_events WHERE user_id = ? ORDER BY seq DESC LIMIT 1",
                            (user_id,)) as cur:
        last = await cur.fetchone()
    if last is None or last["action"] != "bind":
        await _audit(conn, principal, "not_bound", request_id)
        return False
    await conn.execute(
        "INSERT INTO patient_identity_events (event_id, user_id, issuer, subject, patient_id, action, reason_code, actor_id, actor_role, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'unbind', ?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, last["issuer"], last["subject"], last["patient_id"], reason, principal.user_id, principal.role.value, _iso(_now())))
    await _audit(conn, principal, "unbound", request_id, patient_id=last["patient_id"], reason_code=reason)
    return True


async def unbind_self(conn: aiosqlite.Connection, principal: Principal, request_id=None) -> dict:
    if principal.role != Role.PATIENT:
        raise ApiError(403, "FORBIDDEN", "Role does not have permission")
    async with transaction(conn):
        done = await _unbind(conn, principal, principal.user_id, "patient_request", request_id)
    return {"unbound": done}


async def admin_unbind(conn: aiosqlite.Connection, principal: Principal, user_id: str, request_id=None) -> dict:
    async with transaction(conn):
        done = await _unbind(conn, principal, user_id, "admin", request_id)
    return {"unbound": done}


async def my_record(conn: aiosqlite.Connection, principal: Principal) -> dict:
    """The signed-in patient's own linked visits. Every visit is a past record with its own date; nothing here says a
    symptom is current. Clinical content stays behind each visit's own endpoints and checks."""
    if principal.role != Role.PATIENT:
        raise ApiError(403, "FORBIDDEN", "Role does not have permission")
    async with read_transaction(conn):
        patient_id = await bound_patient(conn, principal.user_id) if principal.auth_mode == "oidc" else None
        visits = []
        if patient_id is not None:
            async with conn.execute(
                "SELECT e.case_id FROM patient_link_events e WHERE e.patient_id = ? AND e.seq = "
                "(SELECT MAX(seq) FROM patient_link_events x WHERE x.case_id = e.case_id) AND e.action = 'link' ORDER BY e.seq", (patient_id,)) as cur:
                case_ids = [r["case_id"] for r in await cur.fetchall()]
            for cid in case_ids:
                try:
                    row = await consent.load_case(conn, principal, cid, "read", probe=True)
                except ApiError:
                    continue
                visits.append({"case_id": cid, "visit_date": row["created_at"][:10], "scenario": row["scenario"], "is_past_visit": True})
    if patient_id is not None:
        async with transaction(conn):
            await audit.record(conn, principal=principal, action="patient_record_viewed", outcome="success",
                               details=audit.PatientRecordViewedDetails(visit_count=len(visits)))
    return {"bound": patient_id is not None, "visits": visits,
            "note": "Each visit is a record of that day. It does not describe how you are now."}
