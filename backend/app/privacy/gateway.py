"""The single approved path from case text to an LLM adapter (docs/11 §A, §D).

    T1  [txn] case access → ai_assist consent effective → capture authz_seq (latest consent event)
        redact outside any transaction, in a worker thread (no DB lock held during NLP)
    T1b [txn] consent unchanged? → audit pii_redacted (count only)            else 409, adapter not called
        adapter.complete(RedactedText)   (errors wrapped; in-flight calls cannot be cancelled)
    T2  [txn] consent unchanged? → audit ai_output_returned                     else discard, 409

`submit_structured` (Phase 6, docs/16) is the same T1/T1b/T2 flow for a list of segments: each segment is
redacted independently (any failure blocks the whole request), the caller's `run` performs the provider
passes, and the caller's `persist` stores the result inside the T2 transaction, after the consent re-check.

Guarantee: output is only returned if consent for `ai_assist` (and therefore `triage`) was continuously
unchanged from T1 to T2 by consent-event sequence. Not guaranteed: what an external processor did with an
in-flight request. Raw text is never persisted or audited; the gateway drops its reference after redaction.
"""

from collections.abc import Awaitable, Callable
from typing import TypeVar

import aiosqlite
import anyio

from app import audit, consent
from app.auth import Principal
from app.database import transaction
from app.errors import ApiError
from app.privacy.adapters import AiDraft, BaseLlmAdapter
from app.privacy.pii import PiiRedactionError, RedactedPrompt, _process_raw, _process_segments

T = TypeVar("T")
R = TypeVar("R")


def _withdrawn() -> ApiError:
    return ApiError(409, "CONSENT_WITHDRAWN", "Consent changed while the request was being processed; output discarded")


async def _still_authorized(conn: aiosqlite.Connection, case_id: str, authz_seq: int) -> bool:
    snap = await consent.snapshot(conn, case_id)
    return snap.authz_seq == authz_seq and snap.is_effective("ai_assist")


async def _record(conn, principal, case_id, request_id, action, outcome, details) -> None:
    async with transaction(conn):
        await audit.record(conn, principal=principal, action=action, outcome=outcome, case_id=case_id, request_id=request_id, details=details)


async def submit_for_ai_assist(
    conn: aiosqlite.Connection,
    principal: Principal,
    case_id: str,
    raw_text: str,
    adapter: BaseLlmAdapter,
    request_id: str | None = None,
) -> AiDraft:
    if not isinstance(adapter, BaseLlmAdapter):
        raise TypeError("adapter must subclass BaseLlmAdapter")

    # T1: authorize (case access first, so unknown/foreign cases leave no audit trace).
    denial: consent.ConsentNotEffective | None = None
    authz_seq = 0
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "triage")
            authz_seq = (await consent.require(conn, case_id, "ai_assist")).authz_seq
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)

    # Redact outside any transaction and off the event loop.
    outcome = await anyio.to_thread.run_sync(_process_raw, raw_text)
    del raw_text
    if outcome.redacted is None:
        reason = outcome.reason or "internal_error"
        await _record(conn, principal, case_id, request_id, "ai_request_blocked", "failure", audit.ReasonDetails(reason_code=reason))
        raise PiiRedactionError(reason)
    redacted = outcome.redacted

    # T1b: consent unchanged since T1? Then audit the redaction and proceed.
    proceed = False
    async with transaction(conn):
        if await _still_authorized(conn, case_id, authz_seq):
            proceed = True
            await audit.record(conn, principal=principal, action="pii_redacted", outcome="success", case_id=case_id, request_id=request_id, details=audit.PiiRedactedDetails(redacted_total=redacted.redacted_total))
        else:
            await audit.record(conn, principal=principal, action="ai_request_blocked", outcome="denied", case_id=case_id, request_id=request_id, details=audit.ReasonDetails(reason_code="consent_changed"))
    if not proceed:
        raise _withdrawn()

    # Adapter call (errors wrapped; nothing from the exception is kept).
    draft: AiDraft | None = None
    adapter_failed = False
    try:
        draft = await adapter.complete(redacted)
    except Exception:
        adapter_failed = True

    # T2: always runs — re-check consent before returning anything.
    returned = False
    async with transaction(conn):
        if not await _still_authorized(conn, case_id, authz_seq):
            await audit.record(conn, principal=principal, action="ai_output_discarded", outcome="denied", case_id=case_id, request_id=request_id, details=audit.ReasonDetails(reason_code="consent_changed"))
        elif adapter_failed or draft is None:
            await audit.record(conn, principal=principal, action="ai_request_blocked", outcome="failure", case_id=case_id, request_id=request_id, details=audit.ReasonDetails(reason_code="adapter_error"))
        else:
            returned = True
            await audit.record(conn, principal=principal, action="ai_output_returned", outcome="success", case_id=case_id, request_id=request_id, details=audit.ReasonDetails(reason_code="null_adapter" if not draft.available else "returned"))
    if returned and draft is not None:
        return draft
    if adapter_failed and await _still_authorized(conn, case_id, authz_seq):
        raise ApiError(502, "AI_ADAPTER_ERROR", "The AI adapter failed; no output returned")
    raise _withdrawn()


async def submit_structured(
    conn: aiosqlite.Connection,
    principal: Principal,
    case_id: str,
    raw_segments: list[tuple[str, str]],
    run: Callable[[RedactedPrompt], Awaitable[T]],
    persist: Callable[[aiosqlite.Connection, RedactedPrompt, T, int], Awaitable[R]],
    request_id: str | None = None,
) -> R:
    """Phase 6 structured path. `run(prompt)` calls the provider (no DB lock held); `persist(conn, prompt,
    result, authz_seq)` runs inside the T2 write transaction only if consent is unchanged since T1."""
    denial: consent.ConsentNotEffective | None = None
    authz_seq = 0
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "triage")
            authz_seq = (await consent.require(conn, case_id, "ai_assist")).authz_seq
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)

    outcome = await anyio.to_thread.run_sync(_process_segments, raw_segments)
    del raw_segments
    if outcome.prompt is None:
        reason = outcome.reason or "internal_error"
        await _record(conn, principal, case_id, request_id, "ai_request_blocked", "failure", audit.ReasonDetails(reason_code=reason))
        raise PiiRedactionError(reason)
    prompt = outcome.prompt

    proceed = False
    async with transaction(conn):
        if await _still_authorized(conn, case_id, authz_seq):
            proceed = True
            await audit.record(conn, principal=principal, action="pii_redacted", outcome="success", case_id=case_id, request_id=request_id, details=audit.PiiRedactedDetails(redacted_total=prompt.redacted_total))
        else:
            await audit.record(conn, principal=principal, action="ai_request_blocked", outcome="denied", case_id=case_id, request_id=request_id, details=audit.ReasonDetails(reason_code="consent_changed"))
    if not proceed:
        raise _withdrawn()

    result = None
    failed = False
    try:
        result = await run(prompt)
    except Exception:
        failed = True

    persisted = None
    returned = False
    async with transaction(conn):
        if not await _still_authorized(conn, case_id, authz_seq):
            await audit.record(conn, principal=principal, action="ai_output_discarded", outcome="denied", case_id=case_id, request_id=request_id, details=audit.ReasonDetails(reason_code="consent_changed"))
        elif failed:
            await audit.record(conn, principal=principal, action="ai_request_blocked", outcome="failure", case_id=case_id, request_id=request_id, details=audit.ReasonDetails(reason_code="adapter_error"))
        else:
            persisted = await persist(conn, prompt, result, authz_seq)
            returned = True
            await audit.record(conn, principal=principal, action="ai_output_returned", outcome="success", case_id=case_id, request_id=request_id, details=audit.ReasonDetails(reason_code="returned"))
    if returned:
        return persisted
    if failed and await _still_authorized(conn, case_id, authz_seq):
        raise ApiError(502, "AI_ADAPTER_ERROR", "The AI provider failed; no output returned and no fallback provider was used")
    raise _withdrawn()
