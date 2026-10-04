"""Phase 8 reviewer dashboard (docs/06 §2.4/§2.6, docs/17). Thin wrappers over `app.review_queue`.

Registered before the `/audit` router so `/audit/governance` is not captured by `GET /audit/{case_id}`.
The queue's urgency comes from the rules engine (raised-only by a reviewer override); no LLM output is used.
"""

import uuid
from datetime import datetime
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Query, Request

from app import review_queue
from app.auth import Principal, Role, require_roles
from app.database import get_db

router = APIRouter(tags=["review"])

_readers = require_roles(Role.MEDICAL_OFFICER, Role.SUPERVISOR)
_reviewer = require_roles(Role.MEDICAL_OFFICER)
_governance = require_roles(Role.SUPERVISOR, Role.ADMIN)


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _public(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if not k.startswith("_")}


@router.get("/triage/queue")
async def triage_queue(request: Request, facility_code: str | None = Query(None, pattern=r"^[A-Z0-9-]{3,32}$"), include_signed_off: bool = False,
                       principal: Principal = Depends(_readers), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return _public(await review_queue.queue(db, principal, facility_code, include_signed_off, _rid(request)))


@router.get("/triage/escalations")
async def triage_escalations(request: Request, facility_code: str | None = Query(None, pattern=r"^[A-Z0-9-]{3,32}$"),
                             principal: Principal = Depends(_readers), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return _public(await review_queue.escalations(db, principal, facility_code, _rid(request)))


@router.get("/triage/{case_id}")
async def triage_case_review(case_id: uuid.UUID, principal: Principal = Depends(_readers), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await review_queue.case_review(db, principal, str(case_id))


@router.patch("/triage/{case_id}/sign-off")
async def triage_sign_off(case_id: uuid.UUID, body: review_queue.SignOffBody, request: Request,
                          principal: Principal = Depends(_reviewer), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await review_queue.sign_off(db, principal, str(case_id), body, _rid(request))


@router.patch("/triage/{case_id}/override")
async def triage_override(case_id: uuid.UUID, body: review_queue.OverrideBody, request: Request,
                          principal: Principal = Depends(_reviewer), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await review_queue.override(db, principal, str(case_id), body, _rid(request))


@router.post("/triage/{case_id}/corrections", status_code=201)
async def triage_correction(case_id: uuid.UUID, body: review_queue.CorrectionBody, request: Request,
                            principal: Principal = Depends(_reviewer), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    """Correct the recorded input: a new rules-engine run bound to the run the reviewer saw (docs/17 §3a)."""
    return await review_queue.correct(db, principal, str(case_id), body, _rid(request))


@router.post("/triage/{case_id}/acknowledge")
async def triage_acknowledge(case_id: uuid.UUID, body: review_queue.AcknowledgeBody, request: Request,
                             principal: Principal = Depends(_reviewer), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await review_queue.acknowledge(db, principal, str(case_id), body, _rid(request))


@router.get("/audit/governance")
async def audit_governance(since: datetime | None = None, until: datetime | None = None,
                           principal: Principal = Depends(_governance), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    """Aggregates only (no case ids, patient tokens, reviewer ids or free text), within the caller's facility scope."""
    return await review_queue.governance(db, since, until, scope=principal.facilities)
