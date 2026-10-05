"""Phase 6 service-level tests: consent withdrawal mid-flight, the provider contract, config gating and the
Azure provider contract (mocked; no network). Synthetic data only."""

import asyncio
import json
import sqlite3
import uuid

import pytest

from app import consent
from app.ai import extract
from app.ai.adapter import StructuredProvider
from app.ai.azure_provider import AzureProvider
from app.ai.fake_provider import FakeProvider
from app.ai.schemas import ExtractionOutput
from app.auth import Principal, Role, demo_user_id
from app.config import get_settings
from app.consent_notice import NOTICE_VERSION
from app.database import _connect, run_migrations
from app.errors import ApiError
from app.privacy.pii import redact_segments

ANM = Principal(user_id=demo_user_id("anm_demo"), username="anm_demo", role=Role.ANM)


async def _setup(tmp_path):
    db = tmp_path / "s.db"
    a, b = await _connect(db), await _connect(db)
    await run_migrations(a)
    cid = (await consent.create_case(a, ANM, consent.CaseCreate(scenario="opd", facility_code="PHC-1"), None))["case_id"]
    await consent.record_decision(a, ANM, cid, consent.ConsentDecision(decision="grant", include_ai_assist=True, language="en", notice_version=NOTICE_VERSION), None)
    return db, a, b, cid


class WithdrawingProvider(FakeProvider):
    def __init__(self, conn, cid, purpose):
        super().__init__()
        self.conn, self.cid, self.purpose, self.done = conn, cid, purpose, False

    async def _generate(self, segments, *, temperature, pass_index):
        if not self.done:
            self.done = True
            await consent.withdraw(self.conn, ANM, self.cid, consent.WithdrawRequest(purpose=self.purpose), None)
        return await super()._generate(segments, temperature=temperature, pass_index=pass_index)


@pytest.mark.parametrize("purpose", ["ai_assist", "triage"])
def test_withdrawal_during_provider_passes_discards_and_persists_nothing(tmp_path, purpose):
    async def go():
        db, a, b, cid = await _setup(tmp_path)
        body = extract.ExtractionRequest(idempotency_key=uuid.uuid4(), intake_text="Fever for 3 days. BP 150/90.")
        with pytest.raises(ApiError) as exc:
            await extract.create(a, ANM, cid, body, WithdrawingProvider(b, cid, purpose), 3, None)
        await a.close()
        await b.close()
        return db, exc.value

    db, err = asyncio.run(go())
    assert err.status_code == 409 and err.code == "CONSENT_WITHDRAWN"
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM ai_extraction_runs").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM ai_fields").fetchone()[0] == 0
        actions = [r[0] for r in c.execute("SELECT action FROM audit_log ORDER BY seq")]
    assert "ai_output_discarded" in actions and "ai_extraction_recorded" not in actions


def test_provider_accepts_only_redacted_prompt():
    async def go():
        with pytest.raises(TypeError):
            await FakeProvider().generate("fever for 3 days", temperature=0.1, pass_index=0)  # type: ignore[arg-type]
        reply = await FakeProvider().generate(redact_segments([("S1", "fever for 3 days")]), temperature=0.1, pass_index=0)
        ExtractionOutput.model_validate_json(reply.raw_json)
        return reply

    reply = asyncio.run(go())
    assert reply.provider == "fake"


def test_provider_must_return_text():
    class Bad(StructuredProvider):
        async def _generate(self, segments, *, temperature, pass_index):
            return {"not": "text"}

    with pytest.raises(TypeError):
        asyncio.run(Bad().generate(redact_segments([("S1", "fever")]), temperature=0.1, pass_index=0))


# ── Configuration (env only) ─────────────────────────────────────────────────────────────────────

AZURE_ENV = {
    "AI_PROVIDER": "azure", "AI_CLOUD_ENABLED": "1", "AI_CLOUD_SYNTHETIC_DATA_ONLY": "1",
    "AZURE_OPENAI_API_KEY": "k" * 32, "AZURE_OPENAI_ENDPOINT": "https://sehat-demo.openai.azure.com/",
    "AZURE_OPENAI_DEPLOYMENT_NAME": "gpt-4o", "AZURE_OPENAI_API_VERSION": "2024-08-01-preview",
}


@pytest.fixture
def env(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-length-for-hs256")
    monkeypatch.setenv("MEDGEMMA_ENABLED", "0")  # a developer .env may enable it (it has its own tests)
    for k in list(AZURE_ENV) + ["AI_MAKER_PASSES", "AI_FAKE_MODE", "LOCAL_LLM_MODEL", "LOCAL_LLM_URL", "LOCAL_LLM_MODEL_DIR"]:
        # blank = unset (flags: "0"), and stops a developer's .env from filling it in
        monkeypatch.setenv(k, "0" if k in ("AI_CLOUD_ENABLED", "AI_CLOUD_SYNTHETIC_DATA_ONLY") else "")
    yield monkeypatch
    get_settings.cache_clear()


def test_default_provider_is_none(env):
    assert get_settings().ai_provider == "none"


def test_azure_settings_accepted_by_env_only(env):
    for k, v in AZURE_ENV.items():
        env.setenv(k, v)
    s = get_settings()
    assert s.ai_provider == "azure" and s.ai_cloud_enabled and s.ai_cloud_synthetic_data_only


@pytest.mark.parametrize("override,message", [
    ({"AI_CLOUD_ENABLED": "0"}, "AI_CLOUD_ENABLED=1"),
    ({"AI_CLOUD_SYNTHETIC_DATA_ONLY": "0"}, "AI_CLOUD_SYNTHETIC_DATA_ONLY=1"),
    ({"AZURE_OPENAI_API_KEY": ""}, "AZURE_OPENAI_API_KEY"),
    ({"AZURE_OPENAI_ENDPOINT": "https://evil.example.com/"}, "AZURE_OPENAI_ENDPOINT must be"),
    ({"AZURE_OPENAI_ENDPOINT": "http://sehat-demo.openai.azure.com/"}, "AZURE_OPENAI_ENDPOINT must be"),
    ({"AZURE_OPENAI_ENDPOINT": "https://user:pw@sehat-demo.openai.azure.com/"}, "AZURE_OPENAI_ENDPOINT must be"),
    ({"AZURE_OPENAI_ENDPOINT": "https://openai.azure.com.evil.io/"}, "AZURE_OPENAI_ENDPOINT must be"),
    ({"AI_PROVIDER": "openai"}, "AI_PROVIDER must be"),
    ({"AI_MAKER_PASSES": "1"}, "AI_MAKER_PASSES"),
    ({"AI_FAKE_MODE": "demo_disagreement"}, "AI_FAKE_MODE applies only"),
])
def test_azure_misconfiguration_refuses_to_start(env, override, message):
    for k, v in {**AZURE_ENV, **override}.items():
        env.setenv(k, v)
    with pytest.raises(RuntimeError, match=message) as exc:
        get_settings()
    assert "k" * 32 not in str(exc.value) and "evil" not in str(exc.value)


# ── Azure provider contract (mocked Semantic Kernel service; no network) ─────────────────────────


class FakeChat:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls = []

    async def get_chat_message_content(self, history, settings):
        self.calls.append((history, settings))

        class R:
            content = self.reply

        return R()


def test_azure_provider_sends_strict_schema_and_per_pass_temperature(env):
    for k, v in AZURE_ENV.items():
        env.setenv(k, v)
    s = get_settings()
    chat = FakeChat(json.dumps({"chief_complaint": None, "onset": None, "duration": None, "symptoms": [], "measurements": [], "medications": [], "red_flags": [], "urgency_suggestion": None}))
    p = AzureProvider(s, service=chat)
    prompt = redact_segments([("S1", "Patient Ramesh Kumar has fever")])

    async def go():
        for i, t in enumerate((0.1, 0.2, 0.3)):
            await p.generate(prompt, temperature=t, pass_index=i)

    asyncio.run(go())
    temps = [c[1].temperature for c in chat.calls]
    assert temps == [0.1, 0.2, 0.3]
    rf = chat.calls[0][1].response_format
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    sent = str(chat.calls[0][0])
    assert "Ramesh" not in sent and "[PERSON_REDACTED]" in sent
    assert p.describe()["cloud"] is True and p.describe()["provider"] == "azure"
