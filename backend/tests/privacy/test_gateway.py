"""AI-assist gateway: only redacted text reaches an adapter; consent re-checked (docs/11 §A, §D, §I)."""

import asyncio
import logging
import sqlite3

import pytest

from app import consent
from app.auth import Principal, Role, demo_user_id
from app.consent_notice import NOTICE_VERSION
from app.database import _connect, run_migrations
from app.errors import ApiError
from app.privacy.adapters import AiDraft, BaseLlmAdapter, NullLlmAdapter
from app.privacy.gateway import submit_for_ai_assist
from app.privacy.pii import PiiRedactionError, RedactedText

RAW = "Patient Ramesh Kumar, phone +91 98765 43210, Aadhaar 2345 6789 0123. BP 118/76, fever for 3 days."
RAW_VALUES = ("Ramesh", "98765", "43210", "2345 6789 0123")
ANM = Principal(user_id=demo_user_id("anm_demo"), username="anm_demo", role=Role.ANM)


class RecordingAdapter(BaseLlmAdapter):
    def __init__(self, on_call=None, fail=False):
        self.calls: list[str] = []
        self.on_call = on_call
        self.fail = fail

    async def _complete(self, text: str) -> AiDraft:
        self.calls.append(text)
        if self.on_call:
            await self.on_call()
        if self.fail:
            raise RuntimeError(f"upstream error echoing {text}")
        return AiDraft(text="summary", source="fake", available=True)


async def _setup(tmp_path, ai=True, decision="grant"):
    db = tmp_path / "g.db"
    a = await _connect(db)
    b = await _connect(db)
    await run_migrations(a)
    cid = (await consent.create_case(a, ANM, consent.CaseCreate(scenario="opd", facility_code="PHC-1"), None))["case_id"]
    await consent.record_decision(a, ANM, cid, consent.ConsentDecision(decision=decision, include_ai_assist=ai, language="en", notice_version=NOTICE_VERSION), None)
    return db, a, b, cid


def _db_dump(db) -> str:
    with sqlite3.connect(db) as c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return "\n".join(str(row) for t in tables for row in c.execute(f"SELECT * FROM {t}"))


def _audit_actions(db, cid) -> list[str]:
    with sqlite3.connect(db) as c:
        return [r[0] for r in c.execute("SELECT action FROM audit_log WHERE case_id=? ORDER BY seq", (cid,))]


def run(coro):
    return asyncio.run(coro)


def test_adapter_receives_only_redacted_text_and_nothing_raw_is_stored(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)

    async def go():
        db, a, b, cid = await _setup(tmp_path)
        adapter = RecordingAdapter()
        draft = await submit_for_ai_assist(a, ANM, cid, RAW, adapter)
        await a.close()
        await b.close()
        return db, cid, adapter, draft

    db, cid, adapter, draft = run(go())
    assert len(adapter.calls) == 1
    sent = adapter.calls[0]
    assert all(v not in sent for v in RAW_VALUES)
    assert "BP 118/76" in sent and "fever for 3 days" in sent  # clinical content preserved
    assert draft.text == "summary" and draft.clinical_use_allowed is False
    dump = _db_dump(db) + caplog.text
    assert all(v not in dump for v in RAW_VALUES)  # not persisted, audited or logged
    assert _audit_actions(db, cid)[-2:] == ["pii_redacted", "ai_output_returned"]


def test_adapter_rejects_raw_strings():
    with pytest.raises(TypeError):
        run(NullLlmAdapter().complete(RAW))  # type: ignore[arg-type]


def test_null_adapter_is_marked_unavailable(tmp_path):
    async def go():
        db, a, b, cid = await _setup(tmp_path)
        d = await submit_for_ai_assist(a, ANM, cid, "fever for 3 days", NullLlmAdapter())
        await a.close()
        await b.close()
        return d

    draft = run(go())
    assert draft.available is False and draft.source == "null_adapter" and draft.text == ""
    assert "urgency" not in AiDraft.model_fields  # AI output can never carry an urgency


def test_no_ai_assist_consent_means_adapter_never_called(tmp_path):
    async def go():
        db, a, b, cid = await _setup(tmp_path, ai=False)
        adapter = RecordingAdapter()
        with pytest.raises(ApiError) as exc:
            await submit_for_ai_assist(a, ANM, cid, RAW, adapter)
        await a.close()
        await b.close()
        return exc.value, adapter, db, cid

    err, adapter, db, cid = run(go())
    assert err.code == "CONSENT_REQUIRED" and adapter.calls == []
    assert _audit_actions(db, cid)[-1] == "consent_denied"


@pytest.mark.parametrize("text,code", [("मरीज़ को बुखार है", "AI_INPUT_UNSUPPORTED_LANGUAGE"), ("number is nine 8 seven 6 5 4 3 2", "PII_DETECTED")])
def test_redaction_failure_blocks_adapter_and_is_sanitized(tmp_path, caplog, text, code):
    caplog.set_level(logging.DEBUG)

    async def go():
        db, a, b, cid = await _setup(tmp_path)
        adapter = RecordingAdapter()
        with pytest.raises(PiiRedactionError) as exc:
            await submit_for_ai_assist(a, ANM, cid, text, adapter)
        await a.close()
        await b.close()
        return exc.value, adapter, db, cid

    err, adapter, db, cid = run(go())
    assert err.code == code and adapter.calls == []
    assert err.__context__ is None and err.__cause__ is None
    assert text not in str(err) and text not in caplog.text and text not in _db_dump(db)
    assert _audit_actions(db, cid)[-1] == "ai_request_blocked"


def _withdraw_on(conn, cid, purpose):
    async def _go():
        await consent.withdraw(conn, ANM, cid, consent.WithdrawRequest(purpose=purpose), None)

    return _go


@pytest.mark.parametrize("purpose", ["ai_assist", "triage"])  # D4(a) and D4(d): triage-only withdrawal also counts
def test_d4_withdrawal_during_adapter_call_discards_output(tmp_path, purpose):
    async def go():
        db, a, b, cid = await _setup(tmp_path)
        adapter = RecordingAdapter(on_call=_withdraw_on(b, cid, purpose))
        with pytest.raises(ApiError) as exc:
            await submit_for_ai_assist(a, ANM, cid, "fever for 3 days", adapter)
        await a.close()
        await b.close()
        return exc.value, adapter, db, cid

    err, adapter, db, cid = run(go())
    assert err.code == "CONSENT_WITHDRAWN"
    assert len(adapter.calls) == 1  # the in-flight call happened; its output was not returned
    assert _audit_actions(db, cid)[-1] == "ai_output_discarded"


def test_d4b_withdraw_then_regrant_during_call_still_discards(tmp_path):
    async def go():
        db, a, b, cid = await _setup(tmp_path)

        async def withdraw_and_regrant():
            await consent.withdraw(b, ANM, cid, consent.WithdrawRequest(purpose="ai_assist"), None)
            await consent.record_decision(b, ANM, cid, consent.ConsentDecision(decision="grant", include_ai_assist=True, language="en", notice_version=NOTICE_VERSION), None)

        with pytest.raises(ApiError) as exc:
            await submit_for_ai_assist(a, ANM, cid, "fever for 3 days", RecordingAdapter(on_call=withdraw_and_regrant))
        await a.close()
        await b.close()
        return exc.value

    assert run(go()).code == "CONSENT_WITHDRAWN"


def test_d4c_withdrawal_is_not_blocked_while_redaction_runs(tmp_path, monkeypatch):
    """Redaction holds no DB lock: a withdrawal alongside a slow redaction commits promptly, and the
    request then ends 409 without calling the adapter."""
    import time

    import app.privacy.gateway as gw

    original = gw._process_raw

    def slow_process(raw):
        time.sleep(0.5)
        return original(raw)

    monkeypatch.setattr(gw, "_process_raw", slow_process)

    async def go():
        db, a, b, cid = await _setup(tmp_path)
        b_fast = await _connect(db, timeout=0.2)
        adapter = RecordingAdapter()
        task = asyncio.create_task(submit_for_ai_assist(a, ANM, cid, "fever for 3 days", adapter))
        await asyncio.sleep(0.1)  # gateway is now inside redaction (worker thread)
        t0 = time.monotonic()
        await consent.withdraw(b_fast, ANM, cid, consent.WithdrawRequest(purpose="ai_assist"), None)
        elapsed = time.monotonic() - t0
        with pytest.raises(ApiError) as exc:
            await task
        for c in (a, b, b_fast):
            await c.close()
        return elapsed, exc.value, adapter

    elapsed, err, adapter = run(go())
    assert elapsed < 1.0
    assert err.code == "CONSENT_WITHDRAWN" and adapter.calls == []


def test_adapter_error_is_wrapped_and_t2_still_audits(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)

    async def go():
        db, a, b, cid = await _setup(tmp_path)
        with pytest.raises(ApiError) as exc:
            await submit_for_ai_assist(a, ANM, cid, "fever for 3 days", RecordingAdapter(fail=True))
        await a.close()
        await b.close()
        return exc.value, db, cid

    err, db, cid = run(go())
    assert err.code == "AI_ADAPTER_ERROR" and err.__context__ is None
    assert "upstream error" not in caplog.text
    assert _audit_actions(db, cid)[-1] == "ai_request_blocked"


def test_adapter_must_subclass_base():
    class Rogue:
        async def complete(self, payload):
            return payload

    async def go(tmp_path):
        db, a, b, cid = await _setup(tmp_path)
        try:
            await submit_for_ai_assist(a, ANM, cid, "fever", Rogue())  # type: ignore[arg-type]
        finally:
            await a.close()
            await b.close()

    import pathlib
    import tempfile

    with tempfile.TemporaryDirectory() as d, pytest.raises(TypeError):
        run(go(pathlib.Path(d)))


def test_redacted_text_type_is_exact():
    class Fake(RedactedText):  # subclass cannot bypass the exact-type check
        pass

    with pytest.raises(TypeError):
        Fake(object(), "x", 0)
