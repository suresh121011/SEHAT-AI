"""Provider selection (docs/16 §2). Explicit, from settings only; never a fallback between providers."""

from app.ai.adapter import StructuredProvider, provenance
from app.config import Settings
from app.errors import ApiError


def build_provider(settings: Settings, verify_sha: bool = True) -> StructuredProvider | None:
    if settings.ai_provider == "fake":
        from app.ai.fake_provider import FakeProvider

        return FakeProvider(mode=settings.ai_fake_mode)
    if settings.ai_provider == "azure":
        from app.ai.azure_provider import AzureProvider

        return AzureProvider(settings)
    if settings.ai_provider == "local":
        from app.ai.local_models import installed, verified
        from app.ai.local_provider import LocalLlamaProvider

        if not installed(settings.local_llm_model_dir, settings.local_llm_model):  # refuse to start, never fall back
            raise RuntimeError(f"AI_PROVIDER=local but {settings.local_llm_model} is not installed: run scripts/download_local_llm.py {settings.local_llm_model}")
        if verify_sha and not verified(settings.local_llm_model_dir, settings.local_llm_model):
            raise RuntimeError(f"AI_PROVIDER=local but the {settings.local_llm_model} file does not match its pinned SHA-256; download it again")
        return LocalLlamaProvider(settings)
    return None


def require_provider(provider: StructuredProvider | None) -> StructuredProvider:
    if provider is None:
        raise ApiError(503, "AI_NOT_CONFIGURED", "No AI provider is configured (AI_PROVIDER=none); enter values manually")
    return provider


async def require_ready(provider: StructuredProvider) -> None:
    """Providers that run on a separate local server are checked before any case data is processed, so an absent
    or wrong model is a clear 503 rather than three failed passes. Never falls back to another provider."""
    readiness = getattr(provider, "readiness", None)
    if readiness is not None and (reason := await readiness()) is not None:
        raise ApiError(503, "LOCAL_MODEL_UNAVAILABLE", "The local AI model is not available; enter values manually", {"reason": reason})


def status(provider: StructuredProvider | None) -> dict:
    if provider is None:
        return {"provider": "none", "provider_kind": "no model is called", "model_id": None, "cloud": False, "provenance": provenance("none", None)}
    return provider.describe()


async def capability(provider: StructuredProvider | None) -> dict:
    """`status` plus a live readiness check (a loopback call for the local provider; no network otherwise)."""
    if provider is None:
        return {**status(None), "ready": False, "not_ready_reason": "not_configured"}
    readiness = getattr(provider, "readiness", None)
    reason = await readiness() if readiness is not None else None
    return {**status(provider), "ready": reason is None, "not_ready_reason": reason}
