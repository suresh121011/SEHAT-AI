"""Patient identity binding routes (docs/security/post-remediation-hardening.md A3). Codes travel only in POST bodies
(never in a URL, so never in access logs)."""

import uuid
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Request

from app import patient_identity as pid
from app import ratelimit
from app.auth import Principal, Role, get_current_principal, require_roles
from app.database import get_db

router = APIRouter(tags=["patient identity"])


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.post("/cases/{case_id}/patient/binding-code", status_code=201)
async def issue_binding_code(case_id: uuid.UUID, request: Request, principal: Principal = Depends(get_current_principal),
                             db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await pid.issue_code(db, principal, str(case_id), _rid(request))


@router.get("/me/patient-record")
async def my_patient_record(principal: Principal = Depends(get_current_principal), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await pid.my_record(db, principal)


@router.post("/me/patient-record/bind")
async def bind_my_record(body: pid.BindBody, request: Request, principal: Principal = Depends(get_current_principal),
                         db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    await ratelimit.check_code_entry(db, request, principal)  # before the code is looked up (A4)
    return await pid.bind(db, principal, body, _rid(request))


@router.post("/me/patient-record/unbind")
async def unbind_my_record(body: pid.UnbindBody, request: Request, principal: Principal = Depends(get_current_principal),
                           db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await pid.unbind_self(db, principal, _rid(request))


@router.post("/admin/users/{user_id}/patient-binding/unbind")
async def admin_unbind(user_id: uuid.UUID, request: Request, principal: Principal = Depends(require_roles(Role.ADMIN)),
                       db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await pid.admin_unbind(db, principal, str(user_id), _rid(request))
