from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Request

from app.database import TABLES, existing_tables, get_db
from app.services.kernel import llm_status

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request, db: aiosqlite.Connection = Depends(get_db)) -> dict[str, Any]:
    missing = set(TABLES) - await existing_tables(db)
    return {
        "status": "ok",
        "database": "ok" if not missing else f"missing tables: {sorted(missing)}",
        "llm": llm_status(request.app.state.kernel),
    }
