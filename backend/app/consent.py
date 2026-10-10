"""Case access, consent state and consent actions (docs/11 §B, §K).

Service functions take a connection so routes stay thin and race tests can drive two connections.

- Case access is checked separately from consent. Unauthorized and non-existent cases get the same
  404, and no audit event is written for them.
- Facility isolation (docs/17 §8): a case outside `Principal.facilities` (server config ACCOUNT_FACILITIES) is that
  same 404; creating a case in a facility outside the scope is a 403 and writes nothing.
- Consent is an append-only history; the effective state per purpose is the latest event by seq.
  `ai_assist` and `voice_cloud` are only effective while `triage` is effective.
- A decision records every purpose explicitly: optional purposes not opted into are recorded as
  `declined` (an omitted `include_voice_cloud` therefore declines cloud speech processing).
- Actor identity and confirmation method come from the authenticated principal, never the client.
  A principal is an application account (shared, password-less demo accounts in this prototype),
  not a verified patient; `staff_attested_verbal` is the ANM account's attestation.
"""

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from app import audit
from app.auth import Principal, Role
from app.consent_notice import NOTICE_VERSION, Language, Purpose, get_notice
from app.database import transaction as _write
from app.errors import ApiError, consent_required, not_found
from app.rules.models import Scenario

logger = logging.getLogger("sehat.consent")

PURPOSES: tuple[Purpose, ...] = ("triage", "ai_assist", "voice_cloud")
DEPENDENT_PURPOSES: tuple[Purpose, ...] = ("ai_assist", "voice_cloud")  # require triage
State = Literal["not_provided", "granted", "declined", "withdrawn"]
AccessMode = Literal["consent", "write", "triage", "read"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class CaseCreate(_Body):
    scenario: Scenario
    facility_code: str = Field(pattern=r"^[A-Z0-9-]{3,32}$")


class ConsentDecision(_Body):
    decision: Literal["grant", "decline"]
    include_ai_assist: bool = Field(default=False, strict=True)
    include_voice_cloud: bool = Field(default=False, strict=True)
    language: Language
    notice_version: str = Field(max_length=32)


class WithdrawRequest(_Body):
    purpose: Purpose


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _method_for(principal: Principal) -> str:
    if principal.role == Role.PATIENT:
        return "patient_button"
    if principal.role == Role.ANM:
        return "staff_attested_verbal"
    raise ApiError(403, "FORBIDDEN", "Role cannot record consent")


# ── Case access ──────────────────────────────────────────────────────────


async def load_case(conn: aiosqlite.Connection, principal: Principal, case_id: str, mode: AccessMode) -> aiosqlite.Row:
    """Return the case if `principal` may access it in `mode`; otherwise the same 404 as a missing case."""
    async with conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)) as cur:
        row = await cur.fetchone()
    if row is None or not principal.may_access_facility(row["facility_code"]):
        raise not_found()  # outside the account's facility scope looks exactly like a missing case (docs/17 §8)
    creator = row["created_by"] is not None and row["created_by"] == principal.user_id
    # The ANM who took over a patient-started case (`hand_over`) works on it as its creating ANM would, except consent:
    # recording or withdrawing consent stays with the account that created the case.
    handler = principal.role == Role.ANM and row["handled_by"] is not None and row["handled_by"] == principal.user_id
    allowed = {
        "consent": creator,
        "write": creator or handler,
        "triage": ((creator or handler) and principal.role == Role.ANM) or principal.role == Role.MEDICAL_OFFICER,
        "read": creator or handler or principal.role in (Role.MEDICAL_OFFICER, Role.SUPERVISOR),
    }[mode]
    if not allowed:
        raise not_found()
    return row


async def create_case(conn: aiosqlite.Connection, principal: Principal, body: CaseCreate, request_id: str | None) -> dict:
    if not principal.may_access_facility(body.facility_code):
        # Nothing is written. No audit event: the audit schema has no access-denial action, and unauthorized case
        # access is likewise not audited (module docstring). The facility code is not logged.
        logger.info("case_create_denied reason=facility_not_permitted")
        raise ApiError(403, "FORBIDDEN", "facility not permitted for this account")
    case_id = str(uuid.uuid4())
    patient_token = f"PT-{secrets.token_hex(6).upper()}"  # random, opaque, pseudonymous (not anonymous)
    async with _write(conn):
        await conn.execute(
            "INSERT INTO cases (case_id, patient_token, facility_code, scenario, status, created_by, created_by_role) VALUES (?, ?, ?, ?, 'open', ?, ?)",
            (case_id, patient_token, body.facility_code, body.scenario.value, principal.user_id, principal.role.value),
        )
        await audit.record(
            conn,
            principal=principal,
            action="case_created",
            outcome="success",
            case_id=case_id,
            request_id=request_id,
            details=audit.CaseCreatedDetails(scenario=body.scenario.value, facility_code=body.facility_code),
        )
    return {"case_id": case_id, "patient_token": patient_token, "scenario": body.scenario.value, "facility_code": body.facility_code, "status": "open"}


class HandoverRequest(_Body):
    patient_token: str = Field(pattern=r"^PT-[0-9A-F]{12}$")


async def hand_over(conn: aiosqlite.Connection, principal: Principal, body: HandoverRequest, request_id: str | None) -> dict:
    """An ANM takes over a case a patient account started, by the case code the patient shows them (docs/11 §3a).

    Only cases created by a patient account, inside the ANM's facility scope, and only while triage consent is in
    effect. A case is taken over once: the same ANM may repeat the call (idempotent, no new audit row); any other
    ANM gets 409. Unknown codes, codes outside the facility scope and staff-created cases all get the same 404 with
    no audit row, so the endpoint does not reveal which codes exist. The case code is a bearer secret (48 random
    bits): whoever holds it, with an ANM account in scope, can take the case over."""
    if principal.role != Role.ANM:
        raise ApiError(403, "FORBIDDEN", "Role does not have permission")
    denial: ConsentNotEffective | None = None
    case_id = ""
    async with _write(conn):
        async with conn.execute("SELECT * FROM cases WHERE patient_token = ?", (body.patient_token,)) as cur:
            rows = await cur.fetchall()
        if len(rows) != 1 or rows[0]["created_by_role"] != Role.PATIENT.value or not principal.may_access_facility(rows[0]["facility_code"]):
            raise not_found()
        row = rows[0]
        case_id = row["case_id"]
        if row["handled_by"] is not None and row["handled_by"] != principal.user_id:
            raise ApiError(409, "CASE_ALREADY_HANDED_OVER", "Another health worker has already taken over this case")
        try:
            await require(conn, case_id, "triage")
        except ConsentNotEffective as exc:
            denial = exc
        if denial is None and row["handled_by"] is None:
            await conn.execute("UPDATE cases SET handled_by = ?, handled_at = ? WHERE case_id = ? AND handled_by IS NULL", (principal.user_id, _now(), case_id))
            await audit.record(conn, principal=principal, action="case_handed_over", outcome="success", case_id=case_id, request_id=request_id,
                               details=audit.CaseHandoverDetails(scenario=row["scenario"], facility_code=row["facility_code"]))
    if denial is not None:
        raise await audit_denied(conn, principal, case_id, denial, request_id)
    return {"case_id": case_id, "patient_token": row["patient_token"], "scenario": row["scenario"], "facility_code": row["facility_code"]}


# ── Consent state ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConsentSnapshot:
    raw: dict[str, State]  # latest action per purpose
    latest_seq: dict[str, int | None]
    authz_seq: int  # latest consent event seq for the case across all purposes (0 if none)

    def effective(self, purpose: Purpose) -> State:
        if purpose in DEPENDENT_PURPOSES and self.raw["triage"] != "granted":
            return self.raw["triage"] if self.raw[purpose] == "granted" else self.raw[purpose]
        return self.raw[purpose]

    def is_effective(self, purpose: Purpose) -> bool:
        return self.effective(purpose) == "granted"


async def snapshot(conn: aiosqlite.Connection, case_id: str) -> ConsentSnapshot:
    raw: dict[str, State] = {p: "not_provided" for p in PURPOSES}
    latest: dict[str, int | None] = {p: None for p in PURPOSES}
    async with conn.execute(
        "SELECT purpose, action, seq FROM consent_events WHERE case_id = ? ORDER BY seq", (case_id,)
    ) as cur:
        async for row in cur:
            raw[row["purpose"]] = row["action"]
            latest[row["purpose"]] = row["seq"]
    authz = max((s for s in latest.values() if s is not None), default=0)
    return ConsentSnapshot(raw=raw, latest_seq=latest, authz_seq=authz)


async def granted_notice_version(conn: aiosqlite.Connection, case_id: str, purpose: Purpose = "triage") -> str | None:
    """Notice version of the latest event for `purpose` if that event is a grant, else None (Phase 5 upload
    gate: a document may be uploaded only under a notice that discloses documents)."""
    async with conn.execute(
        "SELECT action, notice_version FROM consent_events WHERE case_id = ? AND purpose = ? ORDER BY seq DESC LIMIT 1", (case_id, purpose)
    ) as cur:
        row = await cur.fetchone()
    return row["notice_version"] if row and row["action"] == "granted" else None


DeniedState = Literal["not_provided", "declined", "withdrawn"]


class ConsentNotEffective(Exception):
    def __init__(self, purpose: Purpose, state: DeniedState):
        self.purpose = purpose
        self.state = state


async def require(conn: aiosqlite.Connection, case_id: str, purpose: Purpose) -> ConsentSnapshot:
    """Must be called inside the protected write transaction (so a withdrawal cannot interleave)."""
    snap = await snapshot(conn, case_id)
    if not snap.is_effective(purpose):
        state = snap.effective(purpose)
        raise ConsentNotEffective(purpose, "not_provided" if state == "granted" else state)  # "granted" is unreachable here
    return snap


async def audit_denied(conn: aiosqlite.Connection, principal: Principal, case_id: str, denial: ConsentNotEffective, request_id: str | None) -> ApiError:
    """Record a denial in its own transaction (after case access passed) and return the 403 to raise."""
    async with _write(conn):
        await audit.record(
            conn,
            principal=principal,
            action="consent_denied",
            outcome="denied",
            case_id=case_id,
            request_id=request_id,
            details=audit.ConsentDeniedDetails(purpose=denial.purpose, state=denial.state),
        )
    return consent_required()


# ── Consent actions ──────────────────────────────────────────────────────


async def _insert_event(conn, *, case_id, purpose, action, language, review_status, method, principal) -> int:
    cur = await conn.execute(
        "INSERT INTO consent_events (event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), case_id, purpose, action, NOTICE_VERSION, language, review_status, method, principal.user_id, principal.role.value, _now()),
    )
    return int(cur.lastrowid)


async def record_decision(conn: aiosqlite.Connection, principal: Principal, case_id: str, body: ConsentDecision, request_id: str | None) -> ConsentSnapshot:
    """A decision sets every purpose explicitly: grant → triage granted, each optional purpose granted
    only if opted in (else declined); decline → all declined."""
    method = _method_for(principal)
    notice = get_notice(body.language)
    if body.notice_version != notice.version:
        raise ApiError(409, "NOTICE_VERSION_STALE", "The consent notice has changed; please review the current version")
    if body.decision == "decline" and (body.include_ai_assist or body.include_voice_cloud):
        raise ApiError(400, "VALIDATION_ERROR", "Optional purposes cannot be granted when consent is declined")

    grant = body.decision == "grant"
    actions: tuple[tuple[Purpose, str], ...] = (
        ("triage", "granted" if grant else "declined"),
        ("ai_assist", "granted" if grant and body.include_ai_assist else "declined"),
        ("voice_cloud", "granted" if grant and body.include_voice_cloud else "declined"),
    )
    async with _write(conn):
        await load_case(conn, principal, case_id, "consent")
        for purpose, action in actions:
            await _insert_event(conn, case_id=case_id, purpose=purpose, action=action, language=body.language, review_status=notice.review_status, method=method, principal=principal)
        granted = [p for p, a in actions if a == "granted"]
        declined = [p for p, a in actions if a == "declined"]
        for action, purposes in (("consent_granted", granted), ("consent_declined", declined)):
            if purposes:
                await audit.record(
                    conn,
                    principal=principal,
                    action=action,  # type: ignore[arg-type]
                    outcome="success",
                    case_id=case_id,
                    request_id=request_id,
                    details=audit.ConsentChangeDetails(purposes=purposes, notice_version=notice.version, language=body.language, method=method),  # type: ignore[arg-type]
                )
        return await snapshot(conn, case_id)


async def withdraw(conn: aiosqlite.Connection, principal: Principal, case_id: str, body: WithdrawRequest, request_id: str | None) -> ConsentSnapshot:
    """Purpose-specific withdrawal. Withdrawing `triage` also records an explicit withdrawal (method
    `cascade_from_triage`) for each dependent purpose that was granted. Withdrawing a dependent
    purpose leaves triage consent unchanged."""
    method = _method_for(principal)
    async with _write(conn):
        await load_case(conn, principal, case_id, "consent")
        snap = await snapshot(conn, case_id)
        language = await _latest_language(conn, case_id)
        notice = get_notice(language)
        if not snap.is_effective(body.purpose):
            raise ApiError(409, "NOTHING_TO_WITHDRAW", "Consent for this purpose is not currently in effect")
        await _insert_event(conn, case_id=case_id, purpose=body.purpose, action="withdrawn", language=language, review_status=notice.review_status, method=method, principal=principal)
        purposes: list[str] = [body.purpose]
        if body.purpose == "triage":
            for dependent in DEPENDENT_PURPOSES:
                if snap.raw[dependent] == "granted":
                    await _insert_event(conn, case_id=case_id, purpose=dependent, action="withdrawn", language=language, review_status=notice.review_status, method="cascade_from_triage", principal=principal)
                    purposes.append(dependent)
        await audit.record(
            conn,
            principal=principal,
            action="consent_withdrawn",
            outcome="success",
            case_id=case_id,
            request_id=request_id,
            details=audit.ConsentChangeDetails(purposes=purposes, notice_version=notice.version, language=language, method=method),  # type: ignore[arg-type]
        )
        return await snapshot(conn, case_id)


async def history(conn: aiosqlite.Connection, case_id: str) -> list[dict]:
    """Consent history for display: actor role only (actor_id is a reversible pseudonym)."""
    async with conn.execute(
        "SELECT seq, purpose, action, notice_version, language, notice_review_status, method, actor_role, created_at FROM consent_events WHERE case_id = ? ORDER BY seq",
        (case_id,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def _latest_language(conn: aiosqlite.Connection, case_id: str) -> Language:
    """Withdrawal is recorded against the language of the consent being withdrawn."""
    async with conn.execute("SELECT language FROM consent_events WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)) as cur:
        row = await cur.fetchone()
    return row["language"] if row else "en"


def state_view(snap: ConsentSnapshot) -> dict[str, State]:
    return {p: snap.effective(p) for p in PURPOSES}

