"""Local open-weights structured provider: a pinned GGUF model on a loopback-only llama-server (docs/16 §2a).

- Same contract as every provider: input is a `RedactedPrompt` (case text is redacted even though it never leaves
  this machine), output is raw JSON that the server validates, grounds and votes on. No authority over urgency.
- The server is started separately by `scripts/start_local_llm.py` (pinned file, SHA-256 checked, 127.0.0.1 only,
  random API key in a 0600 file, no web UI, no /slots endpoint, no verbose prompt logging, offline).
- Every reply must come from the pinned model alias; anything else (another model on the port, a truncated
  reply, a non-JSON body) is an abstaining pass. There is never a fallback to another provider or to the fake.
- httpx ignores proxy environment variables here (`trust_env=False`), so redacted text cannot be routed off
  the machine by an HTTP(S)_PROXY setting; LOCAL_LLM_URL itself is restricted to loopback in app.config.
"""

import httpx

from app.ai.adapter import StructuredProvider
from app.ai.local_models import MODELS, installed
from app.ai.prompts import COMPACT_JSON, PROMPT_VERSION, SYSTEM, user_message
from app.ai.schemas import ExtractionOutput, strict_json_schema

API_KEY_FILE = "run/api_key"
MAX_TOKENS = 2000


class LocalModelUnavailable(Exception):
    """Reason code only (never model output or case text)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class LocalLlamaProvider(StructuredProvider):
    name = "local"
    kind = "local open-weights model on this machine (llama.cpp); general-purpose, not a medical model"
    cloud = False
    # Stored with each run: the shared prompt + COMPACT_JSON. Listing the red-flag enum values in the prompt was measured
    # (docs/16 §2a.4): red-flag recall 0/15 → 5/15 but grounding 77 % → 52 % and max latency 26 → 67 s, below the agreed
    # gate, so it is not used. Red flags are candidates only; the rules engine always asks the red-flag screen itself.
    prompt_version = f"{PROMPT_VERSION}+compact"

    def __init__(self, settings, transport: httpx.AsyncBaseTransport | None = None):
        self.model = MODELS[settings.local_llm_model]
        self.model_id = f"{self.model.key}@{self.model.revision[:12]}"
        self.model_dir = settings.local_llm_model_dir
        self.url = settings.local_llm_url
        self.timeout_s = settings.ai_timeout_s
        self._transport = transport
        self.warm_up_status = "not_run"  # not_run | ok | skipped:<readiness reason> | failed:<reason code>
        self.schema = {"type": "json_schema", "json_schema": {"name": "sehat_extraction", "schema": strict_json_schema(ExtractionOutput, keep_constraints=True), "strict": True}}

    def _client(self) -> httpx.AsyncClient:
        try:
            key = (self.model_dir / API_KEY_FILE).read_text().strip()
        except OSError:
            raise LocalModelUnavailable("server_not_started") from None
        return httpx.AsyncClient(base_url=self.url, timeout=self.timeout_s, trust_env=False, follow_redirects=False,
                                 headers={"Authorization": f"Bearer {key}"}, transport=self._transport)

    async def readiness(self) -> str | None:
        """None when the pinned model is installed and the server answers with it; else a reason code."""
        if not installed(self.model_dir, self.model.key):
            return "model_not_installed"
        try:
            async with self._client() as client:
                resp = await client.get("/v1/models", timeout=3.0)  # capability checks must stay quick
        except LocalModelUnavailable as exc:
            return exc.reason
        except httpx.HTTPError:
            return "server_unreachable"
        if resp.status_code == 401:
            return "server_auth_failed"
        if resp.status_code != 200:
            return "server_error"
        try:
            ids = {m.get("id") for m in resp.json().get("data", [])}
        except (ValueError, AttributeError):
            return "server_error"
        return None if self.model.key in ids else "wrong_model_loaded"

    async def _generate(self, segments, *, temperature: float, pass_index: int) -> str:
        body = {
            "model": self.model.key,
            "messages": [{"role": "system", "content": f"{SYSTEM}\n{COMPACT_JSON}"}, {"role": "user", "content": user_message(segments)}],
            "temperature": temperature,
            "max_tokens": MAX_TOKENS,
            "response_format": self.schema,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        async with self._client() as client:
            resp = await client.post("/v1/chat/completions", json=body)
        if resp.status_code != 200:
            raise RuntimeError("local model error")
        data = resp.json()
        if data.get("model") != self.model.key:
            raise RuntimeError("unexpected model")
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content")
        if choice.get("finish_reason") != "stop" or not isinstance(content, str):
            raise RuntimeError("incomplete reply")
        return content

    async def warm_up(self) -> str:
        """One SYNTHETIC request through the real path (schema grammar, model alias, finish reason) at startup, so the
        first case does not pay the model/grammar load. Never fatal and never case data: every case is still gated by
        `readiness`. Records a reason code only (docs/16 §2a)."""
        from pydantic import ValidationError

        from app.ai.schemas import ExtractionOutput
        from app.privacy.pii import redact_segments

        if (reason := await self.readiness()) is not None:
            self.warm_up_status = f"skipped:{reason}"
            return self.warm_up_status
        try:
            reply = await self.generate(redact_segments([("S1", "Warm-up text. Temperature 37 C.")]), temperature=0.1, pass_index=0)
            ExtractionOutput.model_validate_json(reply.raw_json)
            self.warm_up_status = "ok"
        except ValidationError:
            self.warm_up_status = "failed:schema_invalid"
        except Exception as exc:  # reason code only: never model output
            self.warm_up_status = f"failed:{type(exc).__name__}"
        return self.warm_up_status

    def describe(self) -> dict:
        return {**super().describe(), "warm_up": self.warm_up_status, "prompt_version": self.prompt_version, "revision": self.model.revision, "license": self.model.license,
                "smoke_gate": self.model.gate, "file_sha256_verified_at_startup": True,
                "served_model_check": "alias only: the server is trusted to have loaded the verified file (docs/16 §2a.3)"}
