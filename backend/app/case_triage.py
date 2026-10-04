"""Case-bound triage (docs/11 §D): the first consent-dependent write.

Inside ONE write transaction: case access → scenario match → consent(triage) → pure rules engine →
append-only triage_runs row → audit event. BEGIN IMMEDIATE holds the write lock, so a withdrawal
committed before this transaction starts is always seen, and a withdrawal arriving during it waits
and commits afterwards (the run records the consent event it relied on).

The rules engine itself (`app.rules`) is untouched and has no database, consent or privacy imports.
"""

import json
import uuid
from datetime import datetime, timezone

import aiosqlite

from app import audit, consent
from app.auth import Principal
from app.database import transaction
from app.errors import ApiError
from app.rules import TriageInput, TriageResult, evaluate_triage


async def record_run(conn: aiosqlite.Connection, principal: Principal, case: aiosqlite.Row, data: TriageInput, snap: consent.ConsentSnapshot,
                     request_id: str | None, correction: tuple[str, str, str | None] | None = None) -> tuple[str, TriageResult, str]:
    """Inside the caller's write transaction, after case access and the triage-consent check: the scenario match,
    the pure rules engine, the append-only triage_runs row and its audit event. Shared by intake triage and a
    reviewer's correction (app.review_queue.correct), so a corrected value goes through exactly the same rules.
    `correction` = (corrected run id, reason code, reason text) for a reviewer correction, else None."""
    case_id = case["case_id"]
    if case["scenario"] != data.scenario.value:
        raise ApiError(409, "SCENARIO_MISMATCH", "Triage scenario does not match the case scenario")
    result = evaluate_triage(data)
    run_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    await conn.execute(
        "INSERT INTO triage_runs (run_id, case_id, consent_seq, urgency, result_json, engine_version, ruleset_version, actor_id, created_at, input_json, "
        "corrects_run_id, correction_reason_code, correction_reason_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, case_id, snap.latest_seq["triage"], result.urgency.value, result.model_dump_json(), result.engine_version, result.ruleset_version,
         principal.user_id, created_at, data.model_dump_json(), *(correction or (None, None, None))),
    )
    await audit.record(
        conn,
        principal=principal,
        action="triage_recorded",
        outcome="success",
        case_id=case_id,
        request_id=request_id,
        details=audit.TriageRecordedDetails(
            run_id=run_id,
            urgency=result.urgency.value,
            rule_ids=[t.rule_id for t in result.triggered_rules],
            engine_version=result.engine_version,
            ruleset_version=result.ruleset_version,
        ),
    )
    return run_id, result, created_at


NO_RUN = "none"  # `expected_run_id=none`: the caller believes the case has no triage run yet


async def check_expected_run(conn: aiosqlite.Connection, case_id: str, expected_run_id: str | None) -> None:
    """Optimistic concurrency for re-triage, inside the caller's write transaction (BEGIN IMMEDIATE holds the write
    lock, so this check and the insert that follows cannot interleave with another writer, on any connection).
    The first run needs no token. Once a run exists the caller must name the run it based its request on: a missing
    token → 409 EXPECTED_RUN_REQUIRED, a token that is not the latest run → 409 STALE_TRIAGE_RUN. Nothing is written
    in either case, so a stale screen can never silently supersede a newer run or a reviewer's work on it."""
    async with conn.execute("SELECT run_id FROM triage_runs WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)) as cur:
        row = await cur.fetchone()
    latest = row["run_id"] if row else None
    if expected_run_id is None:
        if latest is None:
            return
        raise ApiError(409, "EXPECTED_RUN_REQUIRED", "This case already has a triage result. Reload it and resubmit with the result you are replacing",
                       {"latest_triage_run_id": latest})
    if (latest or NO_RUN) != expected_run_id:
        raise ApiError(409, "STALE_TRIAGE_RUN", "A newer triage result exists for this case; nothing was saved. Reload and review it first",
                       {"latest_triage_run_id": latest})


async def run_case_triage(conn: aiosqlite.Connection, principal: Principal, case_id: str, data: TriageInput, request_id: str | None,
                          expected_run_id: str | None = None) -> tuple[str, TriageResult]:
    denial: consent.ConsentNotEffective | None = None
    try:
        async with transaction(conn):
            case = await consent.load_case(conn, principal, case_id, "triage")
            if case["scenario"] != data.scenario.value:
                raise ApiError(409, "SCENARIO_MISMATCH", "Triage scenario does not match the case scenario")
            snap = await consent.require(conn, case_id, "triage")
            await check_expected_run(conn, case_id, expected_run_id)
            run_id, result, _ = await record_run(conn, principal, case, data, snap, request_id)
            return run_id, result
    except consent.ConsentNotEffective as exc:
        denial = exc  # the protected transaction rolled back; record the denial separately
    raise await consent.audit_denied(conn, principal, case_id, denial, request_id)


async def run_input(conn: aiosqlite.Connection, principal: Principal, case_id: str, run_id: str, request_id: str | None) -> TriageInput | None:
    """The stored rules-engine input of a run (None for runs recorded before inputs were stored). Same access
    and consent as case triage."""
    denial: consent.ConsentNotEffective | None = None
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "triage")
            await consent.require(conn, case_id, "triage")
            async with conn.execute("SELECT input_json FROM triage_runs WHERE case_id = ? AND run_id = ?", (case_id, run_id)) as cur:
                row = await cur.fetchone()
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Triage run not found")
    return TriageInput.model_validate_json(row["input_json"]) if row["input_json"] else None


async def list_runs(conn: aiosqlite.Connection, case_id: str) -> list[dict]:
    async with conn.execute("SELECT run_id, consent_seq, urgency, engine_version, ruleset_version, created_at, result_json FROM triage_runs WHERE case_id = ? ORDER BY seq", (case_id,)) as cur:
        rows = await cur.fetchall()
    return [{**{k: r[k] for k in ("run_id", "consent_seq", "urgency", "engine_version", "ruleset_version", "created_at")}, "result": json.loads(r["result_json"])} for r in rows]
