"""The one place an Azure chat service is built (docs/plans/semantic-kernel-orchestration-plan.md step 1, owner decision
K1 of 2026-11-12). It lives here, not in app.services, so the OCR package can use it without reaching the triage engine
(app.services.plugins imports it; tests/privacy/test_boundary.py forbids that path).

Every Azure user takes its chat service from `azure_chat_service`: the Semantic Kernel (app.services.kernel), the
extraction provider (app.ai.azure_provider) and the image backend (app.ocr.medgemma). One set of rules for all:
- AI_CLOUD_ENABLED=1: the switch for every cloud AI call; credentials alone never build a service;
- AI_CLOUD_SYNTHETIC_DATA_ONLY=1: redaction has known misses (docs/16 §8), so only synthetic data may leave;
- key, endpoint and deployment all set;
- an https endpoint whose host is on AZURE_OPENAI_HOST_SUFFIXES (app.config). Before this factory the kernel sent the
  API key to whatever address the settings named; only the extraction and image paths checked the host.
Otherwise there is no service and a short reason code; there is never a fallback to another provider.
"""

from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion

from app.config import Settings, _azure_endpoint

AZURE_SERVICE_ID = "azure-openai-chat"


def azure_unavailable_reason(settings: Settings) -> str | None:
    """None when Azure may be used; otherwise a short reason code (never the value of a setting)."""
    if not settings.ai_cloud_enabled:
        return "cloud_disabled"
    if not settings.ai_cloud_synthetic_data_only:
        return "synthetic_only_off"
    if not settings.azure_openai_configured:
        return "credentials_incomplete"
    try:
        _azure_endpoint(settings.azure_openai_endpoint)
    except RuntimeError:
        return "endpoint_not_allowed"
    return None


def azure_chat_service(settings: Settings, service_id: str = AZURE_SERVICE_ID) -> AzureChatCompletion | None:
    """The only construction site of an Azure chat service in the app (tests/services/test_kernel_factory.py scans)."""
    if azure_unavailable_reason(settings) is not None:
        return None
    return AzureChatCompletion(
        service_id=service_id,
        api_key=settings.azure_openai_api_key,
        endpoint=_azure_endpoint(settings.azure_openai_endpoint),
        deployment_name=settings.azure_openai_deployment_name,
        api_version=settings.azure_openai_api_version or None,
    )


def initial_azure_status(settings: Settings) -> dict:
    """What /health reports before (or instead of) the startup check."""
    reason = azure_unavailable_reason(settings)
    return {"state": "not_configured" if reason else "not_checked", "reason": reason, "checked_at": None, "latency_ms": None}
