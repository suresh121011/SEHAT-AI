"""AI_PROVIDER=local (docs/16 §2a): configuration, readiness, reply checks and the API path, with a mocked llama-server
(httpx.MockTransport). No model is downloaded or run here; the opt-in live test is at the end. Synthetic data only."""

import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx
import pytest

from app.ai import local_models
from app.ai.adapter import provenance
from app.ai.local_provider import API_KEY_FILE, LocalLlamaProvider
from app.ai.schemas import ExtractionOutput
from app.config import get_settings
from app.privacy.pii import redact_segments

KEY = "qwen3-4b-instruct-2507-q4km"
MODEL = local_models.MODELS[KEY]


def fake_install(model_dir: Path, key: str = KEY, api_key: str = "test-key") -> None:
    """A sparse file with the pinned size and a manifest entry: `installed()` checks size + manifest, not content."""
    m = local_models.MODELS[key]
    model_dir.mkdir(parents=True, exist_ok=True)
    with (model_dir / m.filename).open("wb") as f:
        f.truncate(m.size)
    (model_dir / local_models.MANIFEST).write_text(json.dumps({key: {"repo": m.repo, "revision": m.revision, "filename": m.filename, "sha256": m.sha256}}))
    (model_dir / API_KEY_FILE).parent.mkdir(exist_ok=True)
    (model_dir / API_KEY_FILE).write_text(api_key)


def reply(content: str, *, model: str = KEY, finish: str = "stop") -> dict:
    return {"model": model, "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content}}]}


GOOD = json.dumps({
    "chief_complaint": {"value": "fever", "evidence": [{"segment_id": "S1", "quote": "fever for 3 days"}]},
    "onset": None, "duration": {"value": "3 days", "evidence": [{"segment_id": "S1", "quote": "fever for 3 days"}]},
    "symptoms": [{"name": "fever", "negated": False, "evidence": [{"segment_id": "S1", "quote": "fever for 3 days"}]}],
    "measurements": [{"name": "spo2", "value": 91, "value2": None, "unit": "%", "evidence": [{"segment_id": "S2", "quote": "SpO2 91%"}]}],
    "medications": [], "red_flags": [], "urgency_suggestion": None,
})


class Server:
    """Mock llama-server. Records every request so tests can check what was sent."""

    def __init__(self, content: str = GOOD, *, models=(KEY,), status: int = 200, chat: dict | None = None):
        self.content, self.models, self.status, self.chat = content, models, status, chat
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != "Bearer test-key":
            return httpx.Response(401)
        if request.url.path == "/v1/models":
            return httpx.Response(self.status, json={"data": [{"id": m} for m in self.models]})
        return httpx.Response(self.status, json=self.chat or reply(self.content))


def settings(tmp_path, **over):
    from dataclasses import replace

    return replace(get_settings(), ai_provider="local", local_llm_model=KEY, local_llm_model_dir=tmp_path / "llm",
                   local_llm_url="http://127.0.0.1:8091", ai_timeout_s=5.0, **over)


def provider(tmp_path, server: Server, install=True) -> LocalLlamaProvider:
    if install:
        fake_install(tmp_path / "llm")
    return LocalLlamaProvider(settings(tmp_path), transport=httpx.MockTransport(server))


PROMPT_TEXT = [("S1", "Patient Ramesh Kumar reports fever for 3 days."), ("S2", "SpO2 91%.")]


# ── Configuration ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def env(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-length-for-hs256")
    monkeypatch.setenv("MEDGEMMA_ENABLED", "0")
    for k in ("AI_PROVIDER", "LOCAL_LLM_MODEL", "LOCAL_LLM_URL", "LOCAL_LLM_MODEL_DIR", "AI_FAKE_MODE", "AI_MAKER_PASSES"):
        monkeypatch.setenv(k, "")
    yield monkeypatch
    get_settings.cache_clear()


def test_local_settings_defaults(env):
    env.setenv("AI_PROVIDER", "local")
    s = get_settings()
    assert (s.ai_provider, s.local_llm_model, s.local_llm_url) == ("local", KEY, "http://127.0.0.1:8091")


@pytest.mark.parametrize("url", ["http://10.0.0.5:8091", "https://127.0.0.1:8091", "http://127.0.0.1", "http://127.0.0.1:8091/v1",
                                 "http://user:pw@127.0.0.1:8091", "http://127.0.0.1.evil.io:8091", "http://127.0.0.1:8091?x=1",
                                 "http://localhost:8091"])
def test_local_url_must_be_plain_loopback(env, url):
    env.setenv("AI_PROVIDER", "local")
    env.setenv("LOCAL_LLM_URL", url)
    with pytest.raises(RuntimeError) as exc:
        get_settings()
    assert str(exc.value) == "LOCAL_LLM_URL must be http://127.0.0.1:<port> (or http://[::1]:<port>); no path, credentials or query"  # value never echoed


def test_unknown_local_model_refused(env):
    env.setenv("LOCAL_LLM_MODEL", "llama-70b")
    with pytest.raises(RuntimeError, match="LOCAL_LLM_MODEL must be one of"):
        get_settings()


def test_build_refuses_when_model_not_installed(tmp_path):
    from app.ai.providers import build_provider

    with pytest.raises(RuntimeError, match="not installed: run scripts/download_local_llm.py"):
        build_provider(settings(tmp_path))


def test_build_refuses_when_file_hash_does_not_match(tmp_path):
    from app.ai.providers import build_provider

    fake_install(tmp_path / "llm")  # right size and manifest, wrong content
    with pytest.raises(RuntimeError, match="does not match its pinned SHA-256"):
        build_provider(settings(tmp_path))
    assert build_provider(settings(tmp_path), verify_sha=False).name == "local"


def test_local_timeout_defaults_to_60(env):
    env.setenv("AI_PROVIDER", "local")
    env.setenv("AI_TIMEOUT_S", "")
    assert get_settings().ai_timeout_s == 60.0


def test_describe_reports_smoke_gate(tmp_path):
    d = provider(tmp_path, Server()).describe()
    assert d["smoke_gate"] == "passed" and "alias only" in d["served_model_check"]


def test_installed_requires_manifest_size_and_revision(tmp_path):
    d = tmp_path / "llm"
    assert not local_models.installed(d, KEY)
    fake_install(d)
    assert local_models.installed(d, KEY)
    with (d / MODEL.filename).open("r+b") as f:
        f.truncate(MODEL.size - 1)
    assert not local_models.installed(d, KEY)
    fake_install(d)
    data = json.loads((d / local_models.MANIFEST).read_text())
    data[KEY]["revision"] = "0" * 40
    (d / local_models.MANIFEST).write_text(json.dumps(data))
    assert not local_models.installed(d, KEY)


# ── Readiness ────────────────────────────────────────────────────────────────────────────────────

def test_readiness_ok(tmp_path):
    assert asyncio.run(provider(tmp_path, Server()).readiness()) is None


@pytest.mark.parametrize("server,install,reason", [
    (Server(), False, "model_not_installed"),
    (Server(models=("gemma-4-e4b-it-q4_0",)), True, "wrong_model_loaded"),
    (Server(status=500), True, "server_error"),
])
def test_readiness_reasons(tmp_path, server, install, reason):
    p = provider(tmp_path, server, install=install)
    assert asyncio.run(p.readiness()) == reason


def test_warm_up_sends_one_synthetic_request_and_reports_a_reason_code(tmp_path):
    server = Server()
    p = provider(tmp_path, server)
    assert asyncio.run(p.warm_up()) == "ok" and p.describe()["warm_up"] == "ok"
    chats = [r for r in server.requests if r.url.path == "/v1/chat/completions"]
    assert len(chats) == 1 and "Warm-up text" in chats[0].content.decode()  # synthetic text only, never case data


@pytest.mark.parametrize("server,install,status", [
    (Server(), False, "skipped:model_not_installed"),
    (Server(content="not json"), True, "failed:schema_invalid"),
    (Server(chat={"model": "other", "choices": []}), True, "failed:RuntimeError"),
])
def test_warm_up_is_never_fatal(tmp_path, server, install, status):
    p = provider(tmp_path, server, install=install)
    assert asyncio.run(p.warm_up()) == status


def test_readiness_server_not_started_and_unreachable(tmp_path):
    fake_install(tmp_path / "llm")
    (tmp_path / "llm" / API_KEY_FILE).unlink()
    assert asyncio.run(LocalLlamaProvider(settings(tmp_path)).readiness()) == "server_not_started"
    fake_install(tmp_path / "llm")

    def refuse(request):
        raise httpx.ConnectError("refused")

    assert asyncio.run(LocalLlamaProvider(settings(tmp_path), transport=httpx.MockTransport(refuse)).readiness()) == "server_unreachable"


def test_wrong_api_key_is_auth_failure(tmp_path):
    fake_install(tmp_path / "llm", api_key="other")
    p = LocalLlamaProvider(settings(tmp_path), transport=httpx.MockTransport(Server()))
    assert asyncio.run(p.readiness()) == "server_auth_failed"


# ── Generation contract ──────────────────────────────────────────────────────────────────────────

def test_generate_sends_only_redacted_text_with_strict_schema(tmp_path):
    server = Server()
    p = provider(tmp_path, server)
    out = asyncio.run(p.generate(redact_segments(PROMPT_TEXT), temperature=0.2, pass_index=1))
    ExtractionOutput.model_validate_json(out.raw_json)
    assert (out.provider, out.model_id) == ("local", f"{KEY}@{MODEL.revision[:12]}")
    body = json.loads(server.requests[-1].content)
    sent = json.dumps(body)
    assert "Ramesh" not in sent and "Kumar" not in sent and "[PERSON_REDACTED]" in sent
    assert body["temperature"] == 0.2 and body["model"] == KEY
    assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["strict"] is True
    assert server.requests[-1].url.host == "127.0.0.1"


def test_generate_rejects_raw_strings(tmp_path):
    with pytest.raises(TypeError):
        asyncio.run(provider(tmp_path, Server()).generate("raw text", temperature=0.1, pass_index=0))  # type: ignore[arg-type]


@pytest.mark.parametrize("chat", [reply(GOOD, model="some-other-model"), reply(GOOD, finish="length"),
                                  {"model": KEY, "choices": []}, {"model": KEY, "choices": [{"finish_reason": "stop", "message": {"content": None}}]}])
def test_untrusted_replies_raise(tmp_path, chat):
    with pytest.raises(RuntimeError):
        asyncio.run(provider(tmp_path, Server(chat=chat)).generate(redact_segments(PROMPT_TEXT), temperature=0.1, pass_index=0))


def test_http_error_raises(tmp_path):
    with pytest.raises(RuntimeError):
        asyncio.run(provider(tmp_path, Server(status=503)).generate(redact_segments(PROMPT_TEXT), temperature=0.1, pass_index=0))


def test_proxy_environment_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.example:3128")
    p = provider(tmp_path, Server())
    assert p._client()._trust_env is False


def test_timeout_is_an_abstaining_pass_never_a_fallback(tmp_path):
    from app.ai import maker

    def slow(request):
        raise httpx.ReadTimeout("timeout")

    fake_install(tmp_path / "llm")
    p = LocalLlamaProvider(settings(tmp_path), transport=httpx.MockTransport(slow))
    with pytest.raises(RuntimeError, match="all provider passes failed"):
        asyncio.run(maker.run_passes(p, redact_segments(PROMPT_TEXT), 3))


def test_malformed_json_abstains(tmp_path):
    from app.ai import maker

    p = provider(tmp_path, Server(content="{not json"))
    res = asyncio.run(maker.run_passes(p, redact_segments(PROMPT_TEXT), 3))
    assert res.status == "insufficient_agreement" and {a["reason"] for a in res.abstentions} == {"schema_invalid"}


# ── Provenance ───────────────────────────────────────────────────────────────────────────────────

def test_provenance_shapes():
    assert provenance("local", f"{KEY}@a06e946bb6b6") == {"provider": "local", "model": f"{KEY}@a06e946bb6b6", "revision": "a06e946bb6b6", "mode": "local", "synthetic": False}
    assert provenance("fake", "fake-keyword-extractor")["synthetic"] is True and provenance("fake", "x")["mode"] == "fake"
    assert provenance("azure", "gpt-4o")["mode"] == "cloud"
    assert provenance("none", None)["mode"] == "none"


def test_describe_has_provenance_and_licence(tmp_path):
    d = provider(tmp_path, Server()).describe()
    assert d["cloud"] is False and d["provenance"]["mode"] == "local" and d["provenance"]["synthetic"] is False
    assert d["license"] == "Apache-2.0" and d["revision"] == MODEL.revision and "not a medical model" in d["provider_kind"]


# ── API path (mocked server) ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def local_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    model_dir = tmp_path / "llm"
    fake_install(model_dir)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-length-for-hs256")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AI_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_LLM_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("MEDGEMMA_ENABLED", "0")
    monkeypatch.setattr(local_models, "verified", lambda d, k: True)  # sparse test file: the real hash check is tested separately
    server = Server()
    real_init = LocalLlamaProvider.__init__

    def init(self, s, transport=None):
        real_init(self, s, transport=httpx.MockTransport(server))

    monkeypatch.setattr(LocalLlamaProvider, "__init__", init)
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as c:
        async def warmed():  # the warm-up runs as a task on the app's loop; wait for it before tests count requests
            await c.app.state.ai_warm_up

        c.portal.call(warmed)
        assert c.app.state.ai_provider.warm_up_status == "ok"
        warm = [q for q in server.requests if q.url.path == "/v1/chat/completions"]
        assert len(warm) == 1 and "Warm-up text" in warm[0].content.decode()  # synthetic text only
        server.requests.clear()
        yield c, server
    get_settings.cache_clear()


def _case_with_ai_consent(client):
    from tests.privacy.helpers import auth, grant, new_case, token_for

    token = token_for(client, "anm")
    case_id = new_case(client, token)
    grant(client, token, case_id, ai=True)
    return token, case_id, auth


def test_api_capabilities_and_extraction_with_provenance(local_client):
    client, server = local_client
    token, case_id, auth = _case_with_ai_consent(client)
    cap = client.get("/api/v1/ai/capabilities", headers=auth(token)).json()
    assert cap["provider"] == "local" and cap["ready"] is True and cap["provenance"]["mode"] == "local" and cap["cloud"] is False
    r = client.post(f"/api/v1/cases/{case_id}/ai/extractions", headers=auth(token), json={
        "idempotency_key": str(uuid.uuid4()), "intake_text": "Patient Ramesh Kumar reports fever for 3 days. SpO2 91%."})
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["provenance"] == provenance("local", f"{KEY}@{MODEL.revision[:12]}") and view["provider_is_fake"] is False
    assert view["maker"]["passes_valid"] == 3
    chats = [json.loads(q.content) for q in server.requests if q.url.path == "/v1/chat/completions"]
    assert len(chats) == 3 and all("Ramesh" not in json.dumps(c) for c in chats)


def test_api_model_down_is_503_before_any_case_text_is_processed(local_client):
    client, server = local_client
    token, case_id, auth = _case_with_ai_consent(client)
    server.models = ("something-else",)
    r = client.post(f"/api/v1/cases/{case_id}/ai/extractions", headers=auth(token), json={"idempotency_key": str(uuid.uuid4()), "intake_text": "fever"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "LOCAL_MODEL_UNAVAILABLE"
    assert not any(q.url.path == "/v1/chat/completions" for q in server.requests)
    cap = client.get("/api/v1/ai/capabilities", headers=auth(token)).json()
    assert cap["ready"] is False and cap["not_ready_reason"] == "wrong_model_loaded"


# ── Live (opt-in) ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.live
@pytest.mark.skipif(os.environ.get("RUN_LIVE_LOCAL_MODEL_TESTS") != "1", reason="set RUN_LIVE_LOCAL_MODEL_TESTS=1 with scripts/start_local_llm.py running")
def test_live_local_model_extracts_grounded_values():
    from app.ai import maker

    get_settings.cache_clear()
    p = LocalLlamaProvider(get_settings())
    assert asyncio.run(p.readiness()) is None
    res = asyncio.run(maker.run_passes(p, redact_segments([("S1", "Fever for 3 days."), ("S2", "Temperature 39.4 C. Pulse 112.")]), 3))
    assert res.status == "completed" and res.passes_valid >= 2
    assert any(f.key == "temp" for f in res.fields)


def test_local_prompt_asks_for_compact_json_and_schema_keeps_constraints(tmp_path):
    server = Server()
    p = provider(tmp_path, server)
    asyncio.run(p.generate(redact_segments(PROMPT_TEXT), temperature=0.1, pass_index=0))
    body = json.loads(server.requests[-1].content)
    assert "single line" in body["messages"][0]["content"] and p.prompt_version.endswith("+compact")
    schema = json.dumps(body["response_format"]["json_schema"]["schema"])
    assert '"minItems": 1' in schema and '"pattern": "^S[0-9]{1,3}$"' in schema
