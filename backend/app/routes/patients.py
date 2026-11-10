"""Patient registry routes (P1, docs/implementation/p1-patient-registry.md). Thin wrappers over app.patient_registry.
Codes travel only in POST bodies (never in a URL, so never in access logs)."""

import uuid
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Request

from app import patient_registry as reg
from app import ratelimit
from app.auth import Principal, get_current_principal
from app.database import get_db

router = APIRouter(tags=["patients"])


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("/cases/{case_id}/patient")
async def get_patient_link(case_id: uuid.UUID, request: Request, principal: Principal = Depends(get_current_principal),
                           db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await reg.view(db, principal, str(case_id), _rid(request))


@router.post("/cases/{case_id}/patient/new", status_code=201)
async def new_patient(case_id: uuid.UUID, request: Request, principal: Principal = Depends(get_current_principal),
                      db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await reg.register_new(db, principal, str(case_id), _rid(request))


@router.post("/cases/{case_id}/patient/lookup")
async def lookup_patient(case_id: uuid.UUID, body: reg.CodeBody, request: Request, principal: Principal = Depends(get_current_principal),
                         db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    await ratelimit.check_code_entry(db, request, principal)  # before the code is looked up (A4)
    return await reg.lookup(db, principal, str(case_id), body, _rid(request))


@router.post("/cases/{case_id}/patient/link")
async def link_patient(case_id: uuid.UUID, body: reg.LinkBody, request: Request, principal: Principal = Depends(get_current_principal),
                       db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    await ratelimit.check_code_entry(db, request, principal)  # before the code is looked up (A4)
    return await reg.link_existing(db, principal, str(case_id), body, _rid(request))


@router.post("/cases/{case_id}/patient/unlink")
async def unlink_patient(case_id: uuid.UUID, body: reg.UnlinkBody, request: Request, principal: Principal = Depends(get_current_principal),
                         db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await reg.unlink(db, principal, str(case_id), body, _rid(request))
