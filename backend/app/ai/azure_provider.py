"""Azure OpenAI structured provider via Semantic Kernel (docs/16 §2, §7). Config-gated: it is built only when
AI_PROVIDER=azure and app.config has accepted AI_CLOUD_ENABLED=1, AI_CLOUD_SYNTHETIC_DATA_ONLY=1, complete
credentials and an allowed endpoint host. It never falls back to another provider; any failure is an
abstaining pass, and if every pass fails the request returns 502.

Not exercised against a live deployment in this build (no credentials); covered by a mocked contract test.
"""

import anyio

from app.ai.adapter import StructuredProvider
from app.ai.prompts import PROMPT_VERSION, SYSTEM, user_message
from app.ai.schemas import ExtractionOutput, strict_json_schema


class AzureProvider(StructuredProvider):
    name = "azure"
    kind = "Azure OpenAI chat model (structured output)"
    cloud = True

    def __init__(self, settings, service=None):
        from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion

        self.model_id = settings.azure_openai_deployment_name
        self.timeout_s = settings.ai_timeout_s
        self.service = service or AzureChatCompletion(
            service_id="sehat-ai-extraction",
            api_key=settings.azure_openai_api_key,
            endpoint=settings.azure_openai_endpoint,
            deployment_name=settings.azure_openai_deployment_name,
            api_version=settings.azure_openai_api_version,
        )
        self.schema = {"type": "json_schema", "json_schema": {"name": "sehat_extraction", "schema": strict_json_schema(ExtractionOutput), "strict": True}}

    def execution_settings(self, temperature: float):
        from semantic_kernel.connectors.ai.open_ai import AzureChatPromptExecutionSettings

        return AzureChatPromptExecutionSettings(temperature=temperature, response_format=self.schema, max_tokens=2000)

    async def _generate(self, segments, *, temperature: float, pass_index: int) -> str:
        from semantic_kernel.contents import ChatHistory

        history = ChatHistory(system_message=SYSTEM)
        history.add_user_message(user_message(segments))
        with anyio.fail_after(self.timeout_s):
            reply = await self.service.get_chat_message_content(history, self.execution_settings(temperature))
        if reply is None or not isinstance(reply.content, str):
            raise RuntimeError("empty provider reply")
        return reply.content

    def describe(self) -> dict:
        return {**super().describe(), "prompt_version": PROMPT_VERSION}
