"""Provider selection (docs/16 §2). Explicit, from settings only; never a fallback between providers."""

from app.ai.adapter import StructuredProvider
from app.config import Settings
from app.errors import ApiError


def build_provider(settings: Settings) -> StructuredProvider | None:
    if settings.ai_provider == "fake":
        from app.ai.fake_provider import FakeProvider

        return FakeProvider(mode=settings.ai_fake_mode)
    if settings.ai_provider == "azure":
        from app.ai.azure_provider import AzureProvider

        return AzureProvider(settings)
    return None


def require_provider(provider: StructuredProvider | None) -> StructuredProvider:
    if provider is None:
        raise ApiError(503, "AI_NOT_CONFIGURED", "No AI provider is configured (AI_PROVIDER=none); enter values manually")
    return provider


def status(provider: StructuredProvider | None) -> dict:
    if provider is None:
        return {"provider": "none", "provider_kind": "no model is called", "model_id": None, "cloud": False}
    return provider.describe()
