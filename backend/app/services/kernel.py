"""Semantic Kernel orchestrator setup (docs/03 §Tech Stack, docs/09 §1.5).

The LLM only extracts and summarises; urgency is set by deterministic rules (Phase 2).
Supports multi-provider execution aligned with Cognizant TriZetto AI Gateway:
- Azure OpenAI (`AzureChatCompletion`)
- Google Gemini (`OpenAIChatCompletion` targeting Gemini OpenAI-compatible endpoint)
- Local LLM (`OpenAIChatCompletion` targeting loopback llama-server)
- Fake / Stub provider (for tests and offline development)
"""

import asyncio
import logging
from typing import Literal

import openai
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.chat_completion_client_base import ChatCompletionClientBase
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion, OpenAIChatCompletion

from app.config import Settings, get_settings
from app.services.plugins import (
    ClinicalHandoffPlugin,
    PatientContextPlugin,
    SafetyGuardPlugin,
    SehatPlugin,
    TriageRulesPlugin,
)

logger = logging.getLogger("sehat.services.kernel")

AZURE_SERVICE_ID = "azure-openai-chat"
GEMINI_SERVICE_ID = "gemini-chat"
LOCAL_SERVICE_ID = "local-llm-chat"
DEFAULT_SERVICE_ID = "default-chat"

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def build_kernel(settings: Settings | None = None) -> Kernel:
    """Instantiates a Microsoft Semantic Kernel instance configured with available providers and clinical plugins."""
    settings = settings or get_settings()
    kernel = Kernel()

    # 1. Register Azure OpenAI if credentials exist
    if settings.azure_openai_configured:
        kernel.add_service(
            AzureChatCompletion(
                service_id=AZURE_SERVICE_ID,
                api_key=settings.azure_openai_api_key,
                endpoint=settings.azure_openai_endpoint,
                deployment_name=settings.azure_openai_deployment_name,
                api_version=settings.azure_openai_api_version or None,
            )
        )

    # 2. Register Google Gemini if API key exists (via OpenAI-compatible endpoint, zero new deps)
    if settings.google_ai_api_key:
        try:
            gemini_async_client = openai.AsyncOpenAI(
                api_key=settings.google_ai_api_key,
                base_url=GEMINI_OPENAI_BASE_URL,
            )
            kernel.add_service(
                OpenAIChatCompletion(
                    service_id=GEMINI_SERVICE_ID,
                    ai_model_id=getattr(settings, "gemini_model", "gemini-2.0-flash"),
                    async_client=gemini_async_client,
                )
            )
        except Exception as exc:
            logger.warning("Failed to initialize Gemini service in Semantic Kernel: %s", exc)

    # 3. Register Local loopback LLM if configured
    if getattr(settings, "ai_provider", "") == "local" and getattr(settings, "local_llm_url", ""):
        try:
            local_async_client = openai.AsyncOpenAI(
                api_key="local-no-key-required",
                base_url=f"{settings.local_llm_url}/v1",
            )
            kernel.add_service(
                OpenAIChatCompletion(
                    service_id=LOCAL_SERVICE_ID,
                    ai_model_id=getattr(settings, "local_llm_model", "local-instruct"),
                    async_client=local_async_client,
                )
            )
        except Exception as exc:
            logger.warning("Failed to initialize Local LLM service in Semantic Kernel: %s", exc)

    # 4. Attach SEHAT Clinical Plugins
    kernel.add_plugin(SehatPlugin(), plugin_name="sehat")
    kernel.add_plugin(TriageRulesPlugin(), plugin_name="triage_rules")
    kernel.add_plugin(ClinicalHandoffPlugin(), plugin_name="clinical_handoff")
    kernel.add_plugin(PatientContextPlugin(), plugin_name="patient_context")
    kernel.add_plugin(SafetyGuardPlugin(), plugin_name="safety_guard")

    return kernel


def llm_status(kernel: Kernel) -> str:
    """Returns the primary configured LLM service status."""
    for service_id in (AZURE_SERVICE_ID, GEMINI_SERVICE_ID, LOCAL_SERVICE_ID):
        try:
            kernel.get_service(service_id)
            return "configured"
        except Exception:
            continue
    return "not_configured"


def get_active_service_id(kernel: Kernel) -> str | None:
    """Returns the first available ChatCompletion service ID, or None."""
    for service_id in (AZURE_SERVICE_ID, GEMINI_SERVICE_ID, LOCAL_SERVICE_ID):
        try:
            kernel.get_service(service_id)
            return service_id
        except Exception:
            continue
    return None


async def _smoke_test() -> None:
    kernel = build_kernel()
    status = llm_status(kernel)
    active = get_active_service_id(kernel)
    print(f"Semantic Kernel online: status={status}, active_service={active}")
    print(f"Loaded plugins: {list(kernel.plugins.keys())}")


if __name__ == "__main__":
    asyncio.run(_smoke_test())
