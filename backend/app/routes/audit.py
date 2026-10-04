"""Audit read and verification (supervisor only; docs/11 §H). No write paths are exposed.

`GET /audit/{case_id}` goes through the same case-access check as every case read (`consent.load_case`, "read"):
a missing case or one outside the supervisor's facility scope is the same 404 (docs/17 §8).
`POST /audit/verify` is deliberately global: it recomputes the hash chain over ALL rows (a chain cannot be verified
per facility) and returns only ok / first bad seq / verified-through seq, never case content or case ids.
"""

import json
import logging
import uuid
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Request

from app import audit, consent
from app.auth import Principal, Role, require_roles
from app.database import get_db, transaction

router = APIRouter(prefix="/audit", tags=["audit"])
logger = logging.getLogger("sehat.audit")

_supervisor = require_roles(Role.SUPERVISOR)


@router.get("/{case_id}")
async def case_audit(case_id: uuid.UUID, principal: Principal = Depends(_supervisor), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    """Events for one case. actor_id is omitted: it is a reversible pseudonym of the demo username."""
    await consent.load_case(db, principal, str(case_id), "read")  # 404 for missing or out-of-scope cases
    async with db.execute(
        "SELECT seq, timestamp, actor_role, action, outcome, details_json FROM audit_log WHERE case_id = ? ORDER BY seq",
        (str(case_id),),
    ) as cur:
        rows = await cur.fetchall()
    return {
        "case_id": str(case_id),
        "events": [
            {"seq": r["seq"], "timestamp": r["timestamp"], "actor_role": r["actor_role"], "action": r["action"], "outcome": r["outcome"], "details": json.loads(r["details_json"])}
            for r in rows
        ],
    }


@router.post("/verify", response_model=audit.VerifyResult)
async def verify(request: Request, principal: Principal = Depends(_supervisor), db: aiosqlite.Connection = Depends(get_db)) -> audit.VerifyResult:
    """Recompute the chain, then record one `audit_verified` event (not covered by this verification)."""
    result = await audit.verify_chain(db)
    if not result.ok:
        logger.warning("audit_chain_verification_failed first_bad_seq=%s", result.first_bad_seq)
    async with transaction(db):
        await audit.record(
            db,
            principal=principal,
            action="audit_verified",
            outcome="success" if result.ok else "failure",
            details=audit.AuditVerifiedDetails(verified_through_seq=result.verified_through_seq, ok=result.ok),
            request_id=request.state.request_id,
        )
    return result
