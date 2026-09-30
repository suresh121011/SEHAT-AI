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


async def run_case_triage(conn: aiosqlite.Connection, principal: Principal, case_id: str, data: TriageInput, request_id: str | None) -> tuple[str, TriageResult]:
    denial: consent.ConsentNotEffective | None = None
    try:
        async with transaction(conn):
            case = await consent.load_case(conn, principal, case_id, "triage")
            if case["scenario"] != data.scenario.value:
                raise ApiError(409, "SCENARIO_MISMATCH", "Triage scenario does not match the case scenario")
            snap = await consent.require(conn, case_id, "triage")
            result = evaluate_triage(data)
            run_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO triage_runs (run_id, case_id, consent_seq, urgency, result_json, engine_version, ruleset_version, actor_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    case_id,
                    snap.latest_seq["triage"],
                    result.urgency.value,
                    result.model_dump_json(),
                    result.engine_version,
                    result.ruleset_version,
                    principal.user_id,
                    datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                ),
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
            return run_id, result
    except consent.ConsentNotEffective as exc:
        denial = exc  # the protected transaction rolled back; record the denial separately
    raise await consent.audit_denied(conn, principal, case_id, denial, request_id)


async def list_runs(conn: aiosqlite.Connection, case_id: str) -> list[dict]:
    async with conn.execute("SELECT run_id, consent_seq, urgency, engine_version, ruleset_version, created_at, result_json FROM triage_runs WHERE case_id = ? ORDER BY seq", (case_id,)) as cur:
        rows = await cur.fetchall()
    return [{**{k: r[k] for k in ("run_id", "consent_seq", "urgency", "engine_version", "ruleset_version", "created_at")}, "result": json.loads(r["result_json"])} for r in rows]
