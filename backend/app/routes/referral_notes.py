"""Referral closure tracking (docs/21; docs/06 §2.5, §4). Thin wrappers over `app.referral_notes.service`.

Roles are checked here before any case lookup (so a refused role reveals nothing about a case), and again in the
service. Case routes then apply case access, facility scope and consent. The paths are new: the unregistered
work-in-progress router `routes/referrals.py` (prefix `/referrals`) is neither imported nor shadowed.
"""

import uuid
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Request

from app.auth import Principal, Role, require_roles
from app.database import get_db
from app.referral_notes import service

router = APIRouter(tags=["referral"])

_viewers = require_roles(Role.ANM, Role.MEDICAL_OFFICER, Role.SUPERVISOR)
_clinical = require_roles(Role.ANM, Role.MEDICAL_OFFICER)
_referrer = require_roles(Role.MEDICAL_OFFICER)
_trackers = require_roles(Role.MEDICAL_OFFICER, Role.SUPERVISOR)


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("/cases/{case_id}/referrals")
async def case_referrals(case_id: uuid.UUID, principal: Principal = Depends(_viewers),
                         db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.case_referrals(db, principal, str(case_id))


@router.post("/cases/{case_id}/referrals", status_code=201)
async def create_referral(case_id: uuid.UUID, body: service.ReferralCreate, request: Request, principal: Principal = Depends(_referrer),
                          db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.create(db, principal, str(case_id), body, _rid(request))


@router.post("/cases/{case_id}/referrals/{referral_id}/status")
async def record_status(case_id: uuid.UUID, referral_id: uuid.UUID, body: service.StatusUpdate, request: Request,
                        principal: Principal = Depends(_clinical), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.record_status(db, principal, str(case_id), str(referral_id), body, _rid(request))


@router.get("/cases/{case_id}/referrals/{referral_id}/letter")
async def referral_letter(case_id: uuid.UUID, referral_id: uuid.UUID, request: Request, principal: Principal = Depends(_clinical),
                          db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.letter(db, principal, str(case_id), str(referral_id), _rid(request))


@router.get("/cases/{case_id}/referrals/{referral_id}/fhir")
async def referral_fhir(case_id: uuid.UUID, referral_id: uuid.UUID, request: Request, principal: Principal = Depends(_referrer),
                        db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.fhir_export(db, principal, str(case_id), str(referral_id), _rid(request))


@router.get("/referral/active")
async def active_referrals(principal: Principal = Depends(_trackers), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.tracking(db, principal, overdue_only=False)


@router.get("/referral/overdue")
async def overdue_referrals(principal: Principal = Depends(_trackers), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.tracking(db, principal, overdue_only=True)
