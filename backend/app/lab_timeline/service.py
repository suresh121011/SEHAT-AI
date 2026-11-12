"""Lab timeline service (docs/22). Gathers checked values from this visit and the patient's linked visits and hands
them to the pure builder.

Same gate as the checked-values view (`app.ocr.service.reviewed`): the visit's ANM or a medical officer, case access
and facility scope, triage consent in effect (a refusal is audited) and re-checked inside the read snapshot. Other visits are included only when this visit is
linked to a patient record, this visit's `link_visits` consent is in effect, and for each other visit the viewer may
read it and its own `link_visits` consent is in effect (`app.patient_registry._visits`). Values come from the same
rule as `reviewed`: identity-checked documents, values a person confirmed or corrected. Read only; no urgency.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app import consent, patient_registry
from app.auth import Principal, Role
from app.database import read_transaction, transaction
from app.errors import ApiError
from app.lab_timeline import timeline
from app.ocr import service as ocr_service

OTHER_VISITS_TEXT = {
    "shown": None,
    "not_linked": "This visit is not linked to a patient record, so only this visit's reports are shown.",
    "consent_not_in_effect": "Other visits are not shown: consent to link visits is not in effect for this visit.",
}


async def _visit(conn: aiosqlite.Connection, case_id: str, visit_date: str, current: bool) -> dict[str, Any]:
    views = await ocr_service.completed_document_views(conn, case_id)
    values, unresolved = ocr_service.reviewed_items(views)
    documents = [{"document_id": d["document_id"], "created_at": d["created_at"], "dates": d.get("dates")} for d in views if d["status"] != "deleted"]
    return {"case_id": case_id, "visit_date": visit_date, "current": current, "documents": documents, "values": values, "unresolved": unresolved}


async def lab_timeline(conn: aiosqlite.Connection, principal: Principal, case_id: str, request_id: str | None) -> dict[str, Any]:
    if principal.role not in (Role.ANM, Role.MEDICAL_OFFICER):  # defence in depth: the route checks the role first
        raise ApiError(403, "FORBIDDEN", "Role does not have permission")
    denial: consent.ConsentNotEffective | None = None
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "triage")
            await consent.require(conn, case_id, "triage")
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)

    async with read_transaction(conn):
        await ocr_service._consent_still_effective(conn, case_id)  # a withdrawal after the gate returns nothing (409)
        async with conn.execute("SELECT created_at FROM cases WHERE case_id = ?", (case_id,)) as cur:
            created = (await cur.fetchone())["created_at"]
        visits = [await _visit(conn, case_id, created[:10], True)]
        link = await patient_registry.current_link(conn, case_id)
        if link is None:
            others = "not_linked"
        elif not (await consent.snapshot(conn, case_id)).is_effective("link_visits"):
            others = "consent_not_in_effect"
        else:
            others = "shown"
            for v in await patient_registry._visits(conn, principal, link["patient_id"]):
                if v["case_id"] == case_id:
                    continue
                try:  # the same access as this visit's own clinical views, not only "read"
                    await consent.load_case(conn, principal, v["case_id"], "triage", probe=True)
                except ApiError:
                    continue
                visits.append(await _visit(conn, v["case_id"], v["visit_date"], False))
    return {"case_id": case_id, "generated_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "linked": link is not None, "other_visits": others, "other_visits_text": OTHER_VISITS_TEXT[others],
            **timeline.build(visits)}
