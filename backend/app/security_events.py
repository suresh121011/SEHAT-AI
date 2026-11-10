"""Security events noted during a request and written after it (docs/security/post-remediation-hardening.md A5).

An access denial is decided inside the request's own transaction, which then rolls back with the 404, taking any
audit row written there with it. A second write connection opened mid-request could wait on that transaction's lock.
So a denial is noted here and the middleware writes it once the request is finished, in its own transaction.

Only denials for a case that exists are noted. An unknown case id writes nothing, so random ids neither reveal
anything nor flood the log. The response is identical either way.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from app import audit
from app.auth import Principal
from app.config import get_settings
from app.database import _connect, transaction

_pending: ContextVar[list | None] = ContextVar("sehat_security_events", default=None)


@dataclass(frozen=True)
class Denial:
    principal: Principal
    case_id: str
    reason: str  # "facility_scope" | "not_permitted"
    mode: str


def start() -> list:
    events: list = []
    _pending.set(events)
    return events


def note_denial(principal: Principal, case_id: str, reason: str, mode: str) -> None:
    events = _pending.get()
    if events is not None and len(events) < 50:  # bounded per request
        events.append(Denial(principal, case_id, reason, mode))


async def flush(events: list, request_id: str | None) -> None:
    if not events:
        return
    conn = await _connect(get_settings().database_path)
    try:
        async with transaction(conn):
            for e in events:
                await audit.record(conn, principal=e.principal, action="access_denied", outcome="denied", case_id=e.case_id, request_id=request_id,
                                   details=audit.AccessDeniedDetails(reason=e.reason, mode=e.mode))  # type: ignore[arg-type]
    finally:
        await conn.close()
