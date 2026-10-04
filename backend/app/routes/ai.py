"""Phase 6 AI extraction, review and note routes (docs/16 §10). Thin wrappers over app.ai services.

Nothing here submits triage or writes `triage_runs`. Roles: the ANM who created the case, or a medical
officer (`consent.load_case(..., "triage")`); `triage` and `ai_assist` consent must both be in effect.
"""

import uuid
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Request

from app.ai import extract, note, providers, review
from app.auth import Principal, Role, get_current_principal, require_roles
from app.config import Settings, get_settings
from app.database import get_db

router = APIRouter(tags=["ai"])

_reviewers = require_roles(Role.ANM, Role.MEDICAL_OFFICER)


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _provider(request: Request):
    return getattr(request.app.state, "ai_provider", None)


@router.get("/ai/capabilities")
async def capabilities(request: Request, _: Principal = Depends(get_current_principal), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        **providers.status(_provider(request)),
        "maker_passes": settings.ai_maker_passes,
        "translation": "enabled" if getattr(request.app.state, "translator", None) is not None else "disabled",
        "input_languages": ["en"] + (["hi", "or"] if getattr(request.app.state, "translator", None) is not None else []),
        "note": "AI output is untrusted: schema-checked, grounded in quotes, voted across passes and reviewed field by field. Urgency comes from the rules engine; AI may only raise it.",
    }


@router.post("/cases/{case_id}/ai/extractions", status_code=201)
async def create_extraction(case_id: uuid.UUID, body: extract.ExtractionRequest, request: Request, principal: Principal = Depends(_reviewers),
                            settings: Settings = Depends(get_settings), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    provider = providers.require_provider(_provider(request))
    return await extract.create(db, principal, str(case_id), body, provider, settings.ai_maker_passes, _rid(request), getattr(request.app.state, "translator", None))


@router.get("/cases/{case_id}/ai/extractions")
async def list_extractions(case_id: uuid.UUID, request: Request, principal: Principal = Depends(_reviewers), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await extract.list_runs(db, principal, str(case_id), _rid(request))


@router.get("/cases/{case_id}/ai/extractions/{extraction_id}")
async def get_extraction(case_id: uuid.UUID, extraction_id: uuid.UUID, request: Request, principal: Principal = Depends(_reviewers),
                         db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await extract.view(db, principal, str(case_id), str(extraction_id), _rid(request))


@router.post("/cases/{case_id}/ai/fields/{field_id}/review")
async def review_field(case_id: uuid.UUID, field_id: uuid.UUID, body: review.ReviewBody, request: Request, principal: Principal = Depends(_reviewers),
                       db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await review.review(db, principal, str(case_id), str(field_id), body, _rid(request))


@router.get("/cases/{case_id}/ai/reviewed")
async def reviewed_values(case_id: uuid.UUID, request: Request, principal: Principal = Depends(_reviewers), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await review.reviewed(db, principal, str(case_id), _rid(request))


@router.post("/cases/{case_id}/ai/notes", status_code=201)
async def draft_note(case_id: uuid.UUID, body: note.NoteRequest, request: Request, principal: Principal = Depends(_reviewers),
                     db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await note.draft(db, principal, str(case_id), str(body.extraction_id), _rid(request))


@router.get("/cases/{case_id}/ai/notes")
async def list_notes(case_id: uuid.UUID, request: Request, principal: Principal = Depends(_reviewers), db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    return await note.list_notes(db, principal, str(case_id), _rid(request))
