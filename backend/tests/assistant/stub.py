"""A scripted chat service for the assistant tests (docs/24).

It is a real Semantic Kernel chat client: the kernel's own function-calling loop, its allow-list of tools and the
assistant's filters all run. Only the model's replies are scripted. No network, no model.

A script is a list of steps, one per model turn:
- a list of tool names ("get_visit_facts" or a fully qualified "plugin-function") asks for those tool calls;
- a string is the model's final reply;
- a callable gets the chat history so far and returns either of the above.
"""

from types import SimpleNamespace
from typing import Any, ClassVar

from pydantic import Field
from semantic_kernel.connectors.ai.chat_completion_client_base import ChatCompletionClientBase
from semantic_kernel.connectors.ai.function_calling_utils import update_settings_from_function_call_configuration
from semantic_kernel.connectors.ai.open_ai import AzureChatPromptExecutionSettings
from semantic_kernel.contents import AuthorRole, ChatMessageContent, FunctionCallContent, FunctionResultContent


class StubChat(ChatCompletionClientBase):
    SUPPORTS_FUNCTION_CALLING: ClassVar[bool] = True

    script: list[Any] = Field(default_factory=list)
    advertised: list[list[str]] = Field(default_factory=list)  # the tool names offered to the model, per turn
    tool_results: list[str] = Field(default_factory=list)  # what the tools returned to the model, in order
    user_messages: list[str] = Field(default_factory=list)
    turns: int = 0

    def get_prompt_execution_settings_class(self):
        return AzureChatPromptExecutionSettings

    def _update_function_choice_settings_callback(self):
        return update_settings_from_function_call_configuration

    def _reset_function_choice_settings(self, settings) -> None:
        settings.tool_choice = None
        settings.tools = None

    async def _inner_get_chat_message_contents(self, chat_history, settings) -> list[ChatMessageContent]:
        self.turns += 1
        self.advertised.append(sorted(t["function"]["name"] for t in (getattr(settings, "tools", None) or [])))
        self.user_messages = [m.content for m in chat_history.messages if m.role == AuthorRole.USER]
        self.tool_results = [str(i.result) for m in chat_history.messages for i in m.items if isinstance(i, FunctionResultContent)]
        if not self.script:
            raise RuntimeError("the stub was called more often than scripted")
        step = self.script.pop(0)
        if callable(step):
            step = step(chat_history)
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
        if isinstance(step, list):
            calls = [FunctionCallContent(id=f"call_{self.turns}_{i}", name=n if "-" in n else f"visit-{n}", arguments="{}")
                     for i, n in enumerate(step)]
            return [ChatMessageContent(role=AuthorRole.ASSISTANT, items=calls, metadata={"usage": usage})]
        return [ChatMessageContent(role=AuthorRole.ASSISTANT, content=step, metadata={"usage": usage})]
