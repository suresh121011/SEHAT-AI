"""Semantic Kernel orchestrator setup (docs/03 §Tech Stack, docs/09 §1.5).

The LLM only extracts and summarises; urgency is set by deterministic rules (Phase 2).
Run `python -m app.services.kernel` from `backend/` to smoke-test the Azure OpenAI connection.
"""

import asyncio

from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion

from app.config import Settings, get_settings
from app.services.plugins import SehatPlugin

AZURE_SERVICE_ID = "azure-openai-chat"


def build_kernel(settings: Settings | None = None) -> Kernel:
    settings = settings or get_settings()
    kernel = Kernel()
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
    kernel.add_plugin(SehatPlugin(), plugin_name="sehat")
    return kernel


def llm_status(kernel: Kernel) -> str:
    try:
        kernel.get_service(AZURE_SERVICE_ID)
        return "configured"
    except Exception:
        return "not_configured"


async def _smoke_test() -> None:
    kernel = build_kernel()
    if llm_status(kernel) != "configured":
        print("Azure OpenAI is not configured. Set AZURE_OPENAI_* in sehat-ai/.env.")
        return
    result = await kernel.invoke_prompt("Reply with exactly: SEHAT kernel online")
    print(result)


if __name__ == "__main__":
    asyncio.run(_smoke_test())
