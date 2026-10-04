"""Cases, consent and case-bound triage (docs/06 §2.2, docs/11). Thin wrappers over services."""

import uuid
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Query, Request

from app import case_triage, consent
from app.auth import Principal, Role, get_current_principal, require_roles
from app.consent_notice import Language, Notice, get_notice
from app.database import get_db
from app.rules import TriageInput

router = APIRouter(tags=["cases"])

_case_creators = require_roles(Role.PATIENT, Role.ANM)
_triage_roles = require_roles(Role.ANM, Role.MEDICAL_OFFICER)


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("/consent/notice", response_model=Notice)
async def consent_notice(language: Language = Query("en"), _: Principal = Depends(get_current_principal)) -> Notice:
    """Notice text, version and review status. Hindi/Odia are unreviewed draft translations."""
    return get_notice(language)


@router.post("/cases", status_code=201)
async def create_case(body: consent.CaseCreate, request: Request, principal: Principal = Depends(_case_creators), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await consent.create_case(db, principal, body, _rid(request))


@router.get("/cases/{case_id}")
async def get_case(case_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    row = await consent.load_case(db, principal, str(case_id), "read")
    snap = await consent.snapshot(db, str(case_id))
    async with db.execute("SELECT run_id FROM triage_runs WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (str(case_id),)) as cur:
        latest = await cur.fetchone()
    return {
        "case_id": row["case_id"],
        "patient_token": row["patient_token"],
        "scenario": row["scenario"],
        "facility_code": row["facility_code"],
        "status": row["status"],
        "is_creator": row["created_by"] == principal.user_id,
        "consent": consent.state_view(snap),
        # An id only (no clinical content): the token a re-triage must send as `expected_run_id`.
        "latest_triage_run_id": latest["run_id"] if latest else None,
    }


@router.post("/cases/{case_id}/consent")
async def record_consent(case_id: uuid.UUID, body: consent.ConsentDecision, request: Request, principal: Principal = Depends(_case_creators), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    snap = await consent.record_decision(db, principal, str(case_id), body, _rid(request))
    return {"case_id": str(case_id), "consent": consent.state_view(snap)}


@router.post("/cases/{case_id}/consent/withdraw")
async def withdraw_consent(case_id: uuid.UUID, body: consent.WithdrawRequest, request: Request, principal: Principal = Depends(_case_creators), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    snap = await consent.withdraw(db, principal, str(case_id), body, _rid(request))
    return {"case_id": str(case_id), "consent": consent.state_view(snap)}


@router.get("/cases/{case_id}/consent")
async def consent_history(case_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    await consent.load_case(db, principal, str(case_id), "read")
    snap = await consent.snapshot(db, str(case_id))
    return {"case_id": str(case_id), "consent": consent.state_view(snap), "history": await consent.history(db, str(case_id))}


@router.post("/cases/{case_id}/triage")
async def triage_case(case_id: uuid.UUID, body: TriageInput, request: Request,
                      expected_run_id: str | None = Query(None, pattern=r"^(none|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"),
                      principal: Principal = Depends(_triage_roles), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    """`expected_run_id`: the latest run the caller saw (`none` before the first run). Required once a run exists
    (docs/17 §3b); a stale or missing token is a 409 and nothing is written."""
    run_id, result = await case_triage.run_case_triage(db, principal, str(case_id), body, _rid(request), expected_run_id)
    return {"case_id": str(case_id), "run_id": run_id, "result": result.model_dump(mode="json")}


@router.get("/cases/{case_id}/triage/runs/{run_id}/counterfactuals")
async def run_counterfactuals(case_id: uuid.UUID, run_id: uuid.UUID, request: Request, principal: Principal = Depends(_triage_roles), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    """Counterfactuals for a recorded case triage run, from its stored input (docs/16 §7)."""
    from app.rules.counterfactual import counterfactuals

    data = await case_triage.run_input(db, principal, str(case_id), str(run_id), _rid(request))
    if data is None:
        return {"case_id": str(case_id), "run_id": str(run_id), "status": "unavailable", "reason": "triage run recorded before inputs were stored"}
    return {"case_id": str(case_id), "run_id": str(run_id), "status": "computed", **counterfactuals(data)}
