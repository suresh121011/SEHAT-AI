"""Semantic Kernel setup and the Azure connection check (docs/03 §Tech Stack; docs/plans/semantic-kernel-orchestration-plan.md
step 1, owner decision K1 of 2026-11-12).

The Azure chat service comes from the one factory, app.ai.azure_chat, under its rules (cloud switch, synthetic data only,
complete credentials, an allowed host). This module is still the only one that invokes a kernel
(tests/privacy/test_boundary.py). The LLM only extracts and summarises; urgency is set by deterministic rules.

Run `python -m app.services.kernel` from `backend/` to check the Azure connection.
"""

import asyncio
import time
from datetime import datetime, timezone

from semantic_kernel import Kernel

from app.ai.azure_chat import AZURE_SERVICE_ID, azure_chat_service, azure_unavailable_reason, initial_azure_status  # noqa: F401
from app.config import Settings, get_settings
from app.services.plugins import SehatPlugin

# Fixed and synthetic: the startup check never carries case data.
PROBE_PROMPT = "Reply with exactly: SEHAT kernel online"
PROBE_REPLY = "SEHAT kernel online"
PROBE_TIMEOUT_S = 20.0


def build_kernel(settings: Settings | None = None) -> Kernel:
    settings = settings or get_settings()
    kernel = Kernel()
    service = azure_chat_service(settings)
    if service is not None:
        kernel.add_service(service)
    kernel.add_plugin(SehatPlugin(), plugin_name="sehat")
    return kernel


def llm_status(kernel: Kernel) -> str:
    """Whether an Azure chat service is registered. Registered is not the same as working: see `probe_azure`."""
    try:
        kernel.get_service(AZURE_SERVICE_ID)
        return "configured"
    except Exception:
        return "not_configured"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _failure_reason(exc: BaseException) -> str:
    """A short code from the error chain. The provider's message is never kept: it can carry the address."""
    code, status, cur, seen = None, None, exc, set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        code = code or getattr(cur, "code", None)
        status = status or getattr(cur, "status_code", None)
        if isinstance(cur, TimeoutError):
            return "timeout"
        cur = cur.__cause__ or cur.__context__
    if code == "DeploymentNotFound":
        return "deployment_not_found"
    if status in (401, 403):
        return "auth_failed"
    if status == 429:
        return "rate_limited"
    if status == 404:
        return "not_found"
    return "error"


async def probe_azure(kernel, timeout_s: float = PROBE_TIMEOUT_S) -> dict:
    """One fixed synthetic prompt through the kernel. Ready only on the expected reply."""
    t0 = time.perf_counter()
    try:
        reply = await asyncio.wait_for(kernel.invoke_prompt(PROBE_PROMPT), timeout_s)
    except Exception as exc:  # noqa: BLE001 - reported as a code, never echoed
        return {"state": "failed", "reason": _failure_reason(exc), "checked_at": _now(), "latency_ms": None}
    latency = int((time.perf_counter() - t0) * 1000)
    if PROBE_REPLY.lower() not in str(reply).lower():
        return {"state": "failed", "reason": "unexpected_reply", "checked_at": _now(), "latency_ms": latency}
    return {"state": "ready", "reason": None, "checked_at": _now(), "latency_ms": latency}


async def _check() -> None:
    settings = get_settings()
    status = initial_azure_status(settings)
    if status["state"] == "not_checked":
        status = await probe_azure(build_kernel(settings))
    print(f"Azure chat: {status['state']}" + (f" ({status['reason']})" if status["reason"] else "")
          + (f" in {status['latency_ms']} ms" if status["latency_ms"] is not None else ""))


if __name__ == "__main__":
    asyncio.run(_check())
