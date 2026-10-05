"""Optional NVIDIA NeMo Guardrails layer around AI extraction (docs/16 §2b). Off unless GUARDRAILS_ENABLED=1.

What it is, honestly: NeMo Guardrails (`LLMRails.check_async`) runs an input rail on the redacted segments before
any provider call and an output rail on the voted values before anything is stored. Both rails are custom Python
actions that call the deterministic detectors in `app.ai.guard` (`check_input`, `check_output`). There is no LLM
self-check rail, no dialog rail and no embedding model, so NeMo adds no new *detection* of its own: it is a standard
rail framework over our own patterns, which are heuristic and can be evaded. Schema validation, grounding, MAKER
voting, raise-only urgency and per-field human review are unchanged and remain the main controls.

Fail closed: a blocked rail releases no AI output (422 GUARDRAIL_BLOCKED, nothing stored); a rail that errors,
times out, or disagrees with the detector it wraps releases nothing either (503 GUARDRAIL_UNAVAILABLE).

Privacy: NeMo Guardrails 0.24.1 sends usage telemetry to NVIDIA by default. Both opt-outs are forced here, before
the package is imported; a test checks this. The rails see redacted text only.
"""

import os

import anyio

from app.privacy.gateway import PolicyBlocked
from app.privacy.pii import RedactedPrompt

RAILS_VERSION = "sehat-rails-1"
TIMEOUT_S = 5.0

_YAML = """
colang_version: "1.0"
rails:
  input:
    flows: [sehat input check]
  output:
    flows: [sehat output check]
"""
_COLANG = """
define bot refuse to respond
  "blocked"

define subflow sehat input check
  $reason = execute sehat_input_check(text=$user_message)
  if $reason
    bot refuse to respond
    stop

define subflow sehat output check
  $reason = execute sehat_output_check(text=$bot_message)
  if $reason
    bot refuse to respond
    stop
"""


def _disable_telemetry() -> None:
    os.environ["NEMO_GUARDRAILS_NO_USAGE_STATS"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"


def _first(check, text: str) -> str | None:
    for line in text.split("\n"):
        if reason := check(line):
            return reason
    return None


def output_texts(res) -> list[str]:
    """Every model-produced string in a vote result: text values, symptom and medication names/doses, units, and the
    same inside candidates. Numbers and enum values (red flags, urgency levels) carry no free text."""
    out: list[str] = []

    def add(value) -> None:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for k in ("name", "dose", "frequency", "unit"):
                if isinstance(value.get(k), str):
                    out.append(value[k])

    for f in res.fields:
        if f.kind in ("red_flag", "urgency"):
            continue
        add(f.value)
        for c in f.candidates:
            add(c.get("value"))
    return [" ".join(t.split()) for t in out if t and t.strip()]


class Guardrails:
    def __init__(self):
        from app.ai import guard

        _disable_telemetry()
        from nemoguardrails import LLMRails, RailsConfig
        from nemoguardrails.rails.llm.options import RailStatus, RailType

        self._status, self._types = RailStatus, RailType
        self._guard = guard
        self.rails = LLMRails(RailsConfig.from_content(yaml_content=_YAML, colang_content=_COLANG))

        async def sehat_input_check(text: str = "") -> str | None:
            return _first(guard.check_input, text)

        async def sehat_output_check(text: str = "") -> str | None:
            return _first(guard.check_output, text)

        self.rails.register_action(sehat_input_check, "sehat_input_check")
        self.rails.register_action(sehat_output_check, "sehat_output_check")
        import importlib.metadata

        self.engine = f"nemoguardrails {importlib.metadata.version('nemoguardrails')}"

    async def _check(self, stage: str, text: str) -> None:
        detector = self._guard.check_input if stage == "input" else self._guard.check_output
        expected = _first(detector, text)
        message = {"role": "user" if stage == "input" else "assistant", "content": text}
        rail_type = self._types.INPUT if stage == "input" else self._types.OUTPUT
        try:
            with anyio.fail_after(TIMEOUT_S):
                result = await self.rails.check_async([message], rail_types=[rail_type])
            blocked = result.status == self._status.BLOCKED
        except Exception:
            raise PolicyBlocked(stage, "guardrail_error", status=503) from None
        if blocked != (expected is not None):  # the rail must agree with the detector it wraps; else trust neither
            raise PolicyBlocked(stage, "guardrail_inconsistent", status=503)
        if blocked:
            raise PolicyBlocked(stage, expected or "blocked")

    async def check_input(self, prompt: RedactedPrompt) -> None:
        text = "\n".join(" ".join(s.text.split()) for s in prompt.segments)
        if text:
            await self._check("input", text)

    async def check_output(self, res) -> None:
        texts = output_texts(res)
        if texts:
            await self._check("output", "\n".join(texts))

    async def warm_up(self) -> None:
        """One synthetic input and output check at startup, so the first real case does not pay NeMo's first-call cost.
        A failure here refuses startup (better than a 503 on the first case)."""
        await self.rails.check_async([{"role": "user", "content": "warm up"}], rail_types=[self._types.INPUT])
        await self.rails.check_async([{"role": "assistant", "content": "warm up"}], rail_types=[self._types.OUTPUT])

    def describe(self) -> dict:
        return {"enabled": True, "engine": self.engine, "rails_version": RAILS_VERSION, "detectors": "deterministic patterns (app/ai/guard.py)",
                "llm_self_check": False, "telemetry": "disabled", "adds_new_detection": False}


def build_guardrails(settings) -> Guardrails | None:
    """None when GUARDRAILS_ENABLED=0. Refuses to start when enabled but not installed (no silent skip)."""
    if not settings.guardrails_enabled:
        return None
    try:
        return Guardrails()
    except ImportError:
        raise RuntimeError("GUARDRAILS_ENABLED=1 but nemoguardrails is not installed: uv pip install -r backend/requirements-guardrails.txt") from None


def status(rails: Guardrails | None) -> dict:
    return rails.describe() if rails is not None else {"enabled": False}
