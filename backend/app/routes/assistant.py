"""The fenced clinical assistant (docs/24). A medical officer only; the role is checked before any case lookup and the
service applies case access, facility scope and consent. The request carries a question id from a closed list,
never free text."""

import uuid
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from app.assistant import service
from app.assistant.questions import QuestionId
from app.auth import Principal, Role, require_roles
from app.database import get_db

router = APIRouter(tags=["assistant"])

_mo = require_roles(Role.MEDICAL_OFFICER)


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: QuestionId


@router.get("/cases/{case_id}/assistant")
async def assistant_view(case_id: uuid.UUID, request: Request, principal: Principal = Depends(_mo),
                         db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.view(db, principal, str(case_id), getattr(request.state, "request_id", None))


@router.post("/cases/{case_id}/assistant/answers", status_code=201)
async def assistant_ask(case_id: uuid.UUID, body: AskBody, request: Request, principal: Principal = Depends(_mo),
                        db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.ask(db, principal, str(case_id), body.question_id, getattr(request.state, "request_id", None))
