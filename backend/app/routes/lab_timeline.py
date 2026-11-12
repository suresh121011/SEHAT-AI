"""Lab timeline across reports and linked visits (docs/22). Thin wrapper over `app.lab_timeline.service`. The role is
checked before any case lookup; the service applies case access, facility scope and consent."""

import uuid
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Request

from app.auth import Principal, Role, require_roles
from app.database import get_db
from app.lab_timeline import service

router = APIRouter(tags=["lab-timeline"])

_clinical = require_roles(Role.ANM, Role.MEDICAL_OFFICER)


@router.get("/cases/{case_id}/lab-timeline")
async def lab_timeline(case_id: uuid.UUID, request: Request, principal: Principal = Depends(_clinical),
                       db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.lab_timeline(db, principal, str(case_id), getattr(request.state, "request_id", None))
