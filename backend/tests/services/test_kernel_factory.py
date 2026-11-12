"""One Azure chat factory (docs/plans/semantic-kernel-orchestration-plan.md, step 1; owner decision K1, 2026-11-12).

`app/ai/azure_chat.py` is the only place an Azure chat service is built. Every user of Azure (the kernel, the extraction
provider, the image backend) gets it from there, under the same rules: cloud switch on, synthetic-data-only on,
credentials complete, and an endpoint host on the allow-list. The startup check reports Azure truthfully: a short
reason code, never a provider message or an address. No network is used here: the chat service is stubbed."""

import ast
import asyncio
import dataclasses
from pathlib import Path

import pytest

from app.config import AZURE_OPENAI_HOST_SUFFIXES, get_settings
from app.ai import azure_chat as ac
from app.services import kernel as k

APP = Path(__file__).resolve().parents[2] / "app"
KEY = "sk-sentinel-not-real-0001"


def _s(**over):
    base = dict(azure_openai_api_key=KEY, azure_openai_endpoint="https://sehat-demo.openai.azure.com/", azure_openai_deployment_name="chat",
                azure_openai_api_version="2024-08-01-preview", ai_cloud_enabled=True, ai_cloud_synthetic_data_only=True)
    return dataclasses.replace(get_settings(), **{**base, **over})


# ── one construction site ─────────────────────────────────────────────────────────────────────────────────────────


def test_an_azure_chat_service_is_built_only_in_the_kernel_module():
    offenders = []
    for p in APP.rglob("*.py"):
        rel = p.relative_to(APP).as_posix()
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "AzureChatCompletion" and rel != "ai/azure_chat.py":
                offenders.append(rel)
    assert offenders == []


# ── the rules every Azure user now shares ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("over,reason", [
    ({"ai_cloud_enabled": False}, "cloud_disabled"),
    ({"ai_cloud_synthetic_data_only": False}, "synthetic_only_off"),
    ({"azure_openai_api_key": ""}, "credentials_incomplete"),
    ({"azure_openai_deployment_name": ""}, "credentials_incomplete"),
    ({"azure_openai_endpoint": "https://example.invalid/"}, "endpoint_not_allowed"),  # the old kernel sent the key here
    ({"azure_openai_endpoint": "http://sehat-demo.openai.azure.com/"}, "endpoint_not_allowed"),
    ({"azure_openai_endpoint": "https://openai.azure.com.evil.io/"}, "endpoint_not_allowed"),
    ({"azure_openai_endpoint": "https://sehat-demo.openai.azure.com/openai/deployments/x"}, "endpoint_not_allowed"),
])
def test_no_service_and_a_named_reason_when_a_rule_is_not_met(over, reason, monkeypatch):
    built = []
    monkeypatch.setattr(ac, "AzureChatCompletion", lambda **kw: built.append(kw))
    s = _s(**over)
    assert ac.azure_unavailable_reason(s) == reason
    assert ac.azure_chat_service(s) is None and built == []  # nothing is constructed, so the key goes nowhere
    assert k.llm_status(k.build_kernel(s)) == "not_configured"
    assert ac.initial_azure_status(s) == {"state": "not_configured", "reason": reason, "checked_at": None, "latency_ms": None}


@pytest.mark.parametrize("host", ["sehat-demo.openai.azure.com", "sehat-demo.cognitiveservices.azure.com", "sehat-demo.services.ai.azure.com"])
def test_each_allowed_azure_host_gets_one_service_with_a_normalised_endpoint(host, monkeypatch):
    built = []
    monkeypatch.setattr(ac, "AzureChatCompletion", lambda **kw: built.append(kw) or object())
    assert f".{host.split('.', 1)[1]}" in AZURE_OPENAI_HOST_SUFFIXES
    svc = ac.azure_chat_service(_s(azure_openai_endpoint=f"https://{host.upper()}"), "sehat-ai-extraction")
    assert svc is not None and len(built) == 1
    assert built[0]["endpoint"] == f"https://{host}/" and built[0]["service_id"] == "sehat-ai-extraction"
    assert built[0]["api_key"] == KEY and built[0]["deployment_name"] == "chat"


def test_extraction_and_image_backends_take_their_service_from_the_factory(monkeypatch):
    from app.ai.azure_provider import AzureProvider
    from app.ocr.medgemma import AzureVisionBackend

    asked = []
    monkeypatch.setattr(ac, "azure_chat_service", lambda settings, service_id=ac.AZURE_SERVICE_ID: asked.append(service_id) or f"svc:{service_id}")
    assert AzureProvider(_s()).service == "svc:sehat-ai-extraction"
    assert AzureVisionBackend(_s())._get_service() == "svc:sehat-ai-medical-image"
    assert asked == ["sehat-ai-extraction", "sehat-ai-medical-image"]


def test_a_backend_that_cannot_get_azure_fails_with_the_reason_and_never_falls_back():
    from app.ai.azure_provider import AzureProvider

    with pytest.raises(RuntimeError, match="endpoint_not_allowed"):
        AzureProvider(_s(azure_openai_endpoint="https://example.invalid/"))


# ── the startup check: truthful, short, no provider text ───────────────────────────────────────────────────────────


class _Err(Exception):
    def __init__(self, status_code=None, code=None):
        super().__init__("Error code: 404 - https://sehat-demo.openai.azure.com/openai/deployments/chat secret detail")
        self.status_code, self.code = status_code, code


class _Kernel:
    def __init__(self, reply=None, exc=None, delay=0.0):
        self.reply, self.exc, self.delay, self.prompts = reply, exc, delay, []

    async def invoke_prompt(self, prompt):
        self.prompts.append(prompt)
        await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.reply


@pytest.mark.parametrize("exc,reason", [
    (_Err(404, "DeploymentNotFound"), "deployment_not_found"),
    (_Err(401), "auth_failed"),
    (_Err(403), "auth_failed"),
    (_Err(429), "rate_limited"),
    (_Err(404), "not_found"),
    (RuntimeError("boom"), "error"),
])
def test_a_failed_check_reports_a_short_reason_and_never_the_provider_message(exc, reason):
    wrapped = RuntimeError("Error occurred while invoking function")
    wrapped.__cause__ = exc  # Semantic Kernel wraps the provider error several times
    st = asyncio.run(k.probe_azure(_Kernel(exc=wrapped)))
    assert st["state"] == "failed" and st["reason"] == reason and st["checked_at"]
    assert "secret" not in str(st) and "azure.com" not in str(st)


def test_a_good_reply_is_ready_with_latency_and_the_prompt_is_fixed_and_synthetic():
    kern = _Kernel(reply="SEHAT kernel online")
    st = asyncio.run(k.probe_azure(kern))
    assert st["state"] == "ready" and st["reason"] is None and isinstance(st["latency_ms"], int)
    assert kern.prompts == [k.PROBE_PROMPT] and "patient" not in k.PROBE_PROMPT.lower()


def test_an_unexpected_reply_or_a_timeout_is_not_ready():
    assert asyncio.run(k.probe_azure(_Kernel(reply="something else")))["reason"] == "unexpected_reply"
    assert asyncio.run(k.probe_azure(_Kernel(reply="x", delay=0.2), timeout_s=0.05))["reason"] == "timeout"


def test_health_reports_the_azure_check_and_the_tests_never_reach_azure(client):
    body = client.get("/api/v1/health").json()
    assert body["llm"] == "not_configured"
    assert body["azure_chat"]["state"] == "not_configured" and body["azure_chat"]["reason"] == "cloud_disabled"
    client.app.state.azure_chat = {"state": "ready", "reason": None, "checked_at": "t", "latency_ms": 900}
    assert client.get("/api/v1/health").json()["azure_chat"]["state"] == "ready"
