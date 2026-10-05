"""Provider-agnostic structured-output contract (docs/16 §2). Extends the Phase 3 adapter rules: a
provider receives only a `RedactedPrompt` (runtime-checked) and returns raw JSON text; the server, never
the provider, validates it against the schema. Provider output is untrusted and carries no authority over
urgency (see app.rules.enforce_raise_only).
"""

from dataclasses import dataclass
from typing import final

from app.privacy.pii import RedactedPrompt


_CLOUD_PROVIDERS = frozenset({"azure"})


def provenance(provider: str, model_id: str | None) -> dict:
    """Where an AI output came from, in one shape for every provider (docs/16 §2). `synthetic` is true only for the
    fake (a keyword matcher, not a model). Derived from the stored provider name, so old rows get it too."""
    mode = {"fake": "fake", "local": "local", "none": "none"}.get(provider, "cloud" if provider in _CLOUD_PROVIDERS else "unknown")
    revision = model_id.split("@", 1)[1] if model_id and "@" in model_id else None
    return {"provider": provider, "model": model_id, "revision": revision, "mode": mode, "synthetic": provider == "fake"}


@dataclass(frozen=True)
class ProviderReply:
    raw_json: str
    provider: str
    model_id: str


class StructuredProvider:
    """Subclass and implement `_generate`. Implementations must not receive any side context."""

    name: str = "abstract"
    kind: str = "abstract"
    model_id: str = "none"
    cloud: bool = False

    @final
    async def generate(self, prompt: RedactedPrompt, *, temperature: float, pass_index: int) -> ProviderReply:
        if type(prompt) is not RedactedPrompt:
            raise TypeError("structured providers accept only RedactedPrompt")
        segments = tuple((s.segment_id, s.text) for s in prompt.segments)
        raw = await self._generate(segments, temperature=temperature, pass_index=pass_index)
        if not isinstance(raw, str):
            raise TypeError("structured providers must return JSON text")
        return ProviderReply(raw_json=raw, provider=self.name, model_id=self.model_id)

    async def _generate(self, segments: tuple[tuple[str, str], ...], *, temperature: float, pass_index: int) -> str:  # pragma: no cover
        raise NotImplementedError

    def describe(self) -> dict:
        return {"provider": self.name, "provider_kind": self.kind, "model_id": self.model_id, "cloud": self.cloud,
                "provenance": provenance(self.name, self.model_id)}
