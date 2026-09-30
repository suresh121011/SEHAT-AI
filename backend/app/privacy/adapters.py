"""LLM adapter contract (docs/11 §A, §I). Phase 3 has NO live LLM: the default is NullLlmAdapter.

Adapters receive only a `RedactedText` (runtime-checked). The type check is an application
safeguard, not a sandbox against malicious in-process code. Adapter output is untrusted, carries no
urgency field, and is never used to lower a deterministic urgency (see app.rules.enforce_raise_only).
"""

from typing import Literal, final

from pydantic import BaseModel, ConfigDict

from app.privacy.pii import RedactedText


class AiDraft(BaseModel):
    """Untrusted AI output. No urgency field by design."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    text: str
    source: str
    available: bool
    clinical_use_allowed: Literal[False] = False


class BaseLlmAdapter:
    """Subclass and implement `_complete`. Future adapters must not receive any side context."""

    @final
    async def complete(self, payload: RedactedText) -> AiDraft:
        if type(payload) is not RedactedText:
            raise TypeError("LLM adapters accept only RedactedText")
        draft = await self._complete(payload.text)
        if type(draft) is not AiDraft:
            raise TypeError("LLM adapters must return AiDraft")
        return draft

    async def _complete(self, text: str) -> AiDraft:  # pragma: no cover - interface
        raise NotImplementedError


class NullLlmAdapter(BaseLlmAdapter):
    """Default adapter: no model is called. Output is explicitly marked unavailable."""

    async def _complete(self, text: str) -> AiDraft:
        return AiDraft(text="", source="null_adapter", available=False)
