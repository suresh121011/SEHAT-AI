from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Request

from app.database import PRIVACY_TABLES, TABLES, VOICE_TABLES, existing_tables, get_db
from app.ai.providers import status as ai_status
from app.services.kernel import llm_status

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request, db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    missing = set(TABLES + PRIVACY_TABLES + VOICE_TABLES) - await existing_tables(db)
    return {
        "status": "ok",
        "database": "ok" if not missing else f"missing tables: {sorted(missing)}",
        "llm": llm_status(request.app.state.kernel),
        "ai_provider": ai_status(getattr(request.app.state, "ai_provider", None))["provider"],
    }
