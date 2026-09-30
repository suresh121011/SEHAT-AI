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
    return {
        "case_id": row["case_id"],
        "patient_token": row["patient_token"],
        "scenario": row["scenario"],
        "facility_code": row["facility_code"],
        "status": row["status"],
        "is_creator": row["created_by"] == principal.user_id,
        "consent": consent.state_view(snap),
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
async def triage_case(case_id: uuid.UUID, body: TriageInput, request: Request, principal: Principal = Depends(_triage_roles), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    run_id, result = await case_triage.run_case_triage(db, principal, str(case_id), body, _rid(request))
    return {"case_id": str(case_id), "run_id": run_id, "result": result.model_dump(mode="json")}
