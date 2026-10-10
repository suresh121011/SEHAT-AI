"""Body map: where the patient points to pain or a problem (docs/06 §2.3, docs/11 §3a).

A patient-reported note for the health worker and the medical officer. It is not a diagnosis, is never triage input
and never changes urgency. Stored so it survives the handover from a patient account to the ANM; previously it lived
only in the browser tab.

- Write: the case creator or the ANM who took the case over (`load_case(..., "write")`), only while triage consent is
  in effect. Each save is an append-only event; the latest event is the current selection.
- Read: anyone who can read the case. Earlier selections stay readable after consent is withdrawn (docs/11 §4 D5).
- Regions come from a fixed list (same ids as `frontend/src/lib/bodyMap.ts`); anything else is a 400.
- Audit records the region count only.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Literal, get_args

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import audit, consent
from app.auth import Principal
from app.database import transaction

Region = Literal[
    "head_front", "neck_front", "chest_right", "chest_left", "abdomen_upper", "abdomen_lower", "pelvis_front",
    "arm_right_front", "arm_left_front", "hand_right_front", "hand_left_front", "leg_right_front", "leg_left_front",
    "foot_right_front", "foot_left_front", "head_back", "neck_back", "back_upper", "back_lower", "buttocks",
    "arm_left_back", "arm_right_back", "leg_left_back", "leg_right_back",
]
REGION_IDS: frozenset[str] = frozenset(get_args(Region))


class BodyMapUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    regions: list[Region] = Field(max_length=len(REGION_IDS))

    @field_validator("regions")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate body region")
        return v


def _view(row: aiosqlite.Row | None) -> dict:
    if row is None:
        return {"regions": [], "recorded_by_role": None, "recorded_at": None}
    return {"regions": json.loads(row["regions_json"]), "recorded_by_role": row["actor_role"], "recorded_at": row["created_at"]}


async def _latest(conn: aiosqlite.Connection, case_id: str) -> aiosqlite.Row | None:
    async with conn.execute("SELECT regions_json, actor_role, created_at FROM body_map_events WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)) as cur:
        return await cur.fetchone()


async def record(conn: aiosqlite.Connection, principal: Principal, case_id: str, body: BodyMapUpdate, request_id: str | None) -> dict:
    denial: consent.ConsentNotEffective | None = None
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "write")
            snap = await consent.require(conn, case_id, "triage")
            await conn.execute(
                "INSERT INTO body_map_events (event_id, case_id, regions_json, consent_seq, actor_id, actor_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), case_id, json.dumps(body.regions), snap.latest_seq["triage"], principal.user_id, principal.role.value,
                 datetime.now(timezone.utc).isoformat(timespec="microseconds")),
            )
            await audit.record(conn, principal=principal, action="body_map_recorded", outcome="success", case_id=case_id, request_id=request_id,
                               details=audit.BodyMapDetails(region_count=len(body.regions)))
            return _view(await _latest(conn, case_id))
    except consent.ConsentNotEffective as exc:
        denial = exc
    raise await consent.audit_denied(conn, principal, case_id, denial, request_id)


async def read(conn: aiosqlite.Connection, principal: Principal, case_id: str) -> dict:
    await consent.load_case(conn, principal, case_id, "read")
    return _view(await _latest(conn, case_id))
