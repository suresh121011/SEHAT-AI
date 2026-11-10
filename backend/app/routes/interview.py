"""Scripted voice interviewer routes (docs/plans/scripted-voice-interviewer.md). Answers travel only in POST bodies."""

import uuid
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Request

from app.auth import Principal, get_current_principal
from app.database import get_db
from app.interview import service

router = APIRouter(tags=["interview"])


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("/cases/{case_id}/interview")
async def get_interview(case_id: uuid.UUID, request: Request, principal: Principal = Depends(get_current_principal),
                        db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.view(db, principal, str(case_id), _rid(request))


@router.post("/cases/{case_id}/interview/answers", status_code=201)
async def record_answer(case_id: uuid.UUID, body: service.AnswerBody, request: Request, principal: Principal = Depends(get_current_principal),
                        db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await service.record(db, principal, str(case_id), body, _rid(request))
