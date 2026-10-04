"""Runtime settings, loaded once from the repo-root `.env` (see `.env.example`)."""

import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

# Values copied verbatim from .env.example are treated as "not set".
_PLACEHOLDERS = {
    "",
    "your_azure_openai_api_key_here",
    "https://your-resource-name.openai.azure.com/",
    "your_sarvam_api_key_here",
    "change_this_to_a_long_random_secret",
}

logger = logging.getLogger("sehat.config")

# Environments where the password-less demo accounts may run. Anything else refuses to start.
DEMO_AUTH_ENVIRONMENTS = frozenset({"development", "test"})

# Hosts that may receive the Sarvam API key, patient audio and read-back text (docs/12 §2, §8). Kept in code
# on purpose: widening it is a reviewed change, not an environment edit.
SARVAM_ALLOWED_HOSTS = frozenset({"api.sarvam.ai"})

# Azure OpenAI endpoint host suffixes that may receive the API key and redacted case text (docs/16 §7).
# Kept in code on purpose, like SARVAM_ALLOWED_HOSTS.
AZURE_OPENAI_HOST_SUFFIXES = (".openai.azure.com", ".cognitiveservices.azure.com")
AI_PROVIDERS = ("none", "fake", "azure")


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, default).strip()
    return "" if value in _PLACEHOLDERS else value


def _flag(name: str, default: bool = False) -> bool:
    value = _env(name, "1" if default else "0").lower()
    if value not in ("0", "1", "true", "false", "yes", "no"):
        raise RuntimeError(f"{name} must be 0/1/true/false")
    return value in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    environment: str
    log_level: str
    database_path: Path
    cors_allowed_origins: list[str]
    jwt_secret_key: str
    jwt_algorithm: str
    jwt_expire_minutes: int
    azure_openai_api_key: str
    azure_openai_endpoint: str
    azure_openai_deployment_name: str
    azure_openai_api_version: str
    # Phase 4 voice (docs/12). Every component is off unless explicitly enabled; a disabled component
    # is never imported, loaded or called.
    voice_enabled: bool = False
    voice_vad_enabled: bool = True
    voice_local_asr_enabled: bool = False
    voice_local_model_dir: Path = REPO_ROOT / "models" / "voice"
    voice_cloud_stt_enabled: bool = False
    voice_tts_enabled: bool = False
    sarvam_api_key: str = field(default="", repr=False)  # never printed
    sarvam_base_url: str = "https://api.sarvam.ai"
    sarvam_timeout_s: float = 20.0
    voice_max_seconds: int = 30
    # Phase 5 OCR (docs/14). Off unless enabled. Local only: no document leaves this machine.
    ocr_enabled: bool = False
    ocr_model_dir: Path = REPO_ROOT / "models" / "ocr"
    ocr_document_dir: Path = REPO_ROOT / "data" / "documents"
    ocr_worker_python: Path = REPO_ROOT / ".venv-ocr" / "bin" / "python"
    ocr_surya_enabled: bool = False  # Surya OCR 2 via the local worker (printed, table-aware)
    ocr_chandra_enabled: bool = False  # Chandra OCR 2 via the local worker (handwritten / discharge)
    ocr_max_bytes: int = 10 * 1024 * 1024
    ocr_page_timeout_s: float = 180.0  # per worker call; on expiry the worker is killed
    ocr_rxnorm_db: Path = REPO_ROOT / "models" / "rxnorm" / "rxnorm.sqlite"
    # Retention of stored document content (docs/14 §3). Must be set explicitly when OCR is enabled:
    # a number of days, or "none" = kept until a reviewer deletes it. The duration itself is a product/legal
    # decision that is NOT made in code; there is no silent default.
    ocr_retention_days: int | None = None
    # Phase 6 AI extraction (docs/16). `none` = no model is called (AI endpoints answer 503). `fake` = the
    # deterministic offline keyword extractor (not an LLM). `azure` = Azure OpenAI via Semantic Kernel, only
    # with AI_CLOUD_ENABLED=1, complete credentials, an allowed host and AI_CLOUD_SYNTHETIC_DATA_ONLY=1.
    # There is never a fallback from one provider to another.
    ai_provider: str = "none"
    ai_cloud_enabled: bool = False
    ai_cloud_synthetic_data_only: bool = False
    ai_maker_passes: int = 3
    ai_fake_mode: str = "honest"  # honest | demo_disagreement (fake provider only; demo of a disputed value)
    ai_timeout_s: float = 30.0
    # Phase 6 P2: IndicTrans2 (indic → en), local only. Off unless enabled.
    translation_enabled: bool = False
    translation_model_dir: Path = REPO_ROOT / "models" / "translation"
    # Reserved for a later phase. Not implemented: enabling it refuses to start, so a switch can never
    # suggest that a check runs when it does not. (MAKER voting is part of Phase 6 extraction and needs no
    # switch; OCR verification still reports MAKER as `not_run`, see docs/14.)
    medgemma_enabled: bool = False  # X-ray/ECG description, deferred (docs/14 §11)
    # Facility isolation (docs/17 §8). Trusted server config, never client input. None = isolation OFF (every
    # account sees every facility, the earlier behaviour). Otherwise username → allowed facility codes, or None
    # for "*" (unrestricted). A username missing from an active mapping gets NO facility (see auth.facility_scope).
    account_facilities: dict[str, frozenset[str] | None] | None = None

    @property
    def facility_isolation(self) -> bool:
        return self.account_facilities is not None

    @property
    def sarvam_configured(self) -> bool:
        return bool(self.sarvam_api_key)

    @property
    def azure_openai_configured(self) -> bool:
        return bool(self.azure_openai_api_key and self.azure_openai_endpoint and self.azure_openai_deployment_name)


@lru_cache
def get_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env")

    environment = _env("ENVIRONMENT", "development")
    if environment not in DEMO_AUTH_ENVIRONMENTS:
        # Hard deployment block (docs/11): authentication is password-less shared demo accounts.
        raise RuntimeError("password-less demo authentication cannot run outside development/test")
    jwt_secret = _env("JWT_SECRET_KEY")
    if not jwt_secret:
        if environment != "development":
            raise RuntimeError("JWT_SECRET_KEY must be set outside development")
        # Per-process secret: tokens stop working after a restart, which is fine for local dev.
        jwt_secret = secrets.token_urlsafe(48)
        logger.warning("JWT_SECRET_KEY not set; using an ephemeral development secret")

    db_path = _path(_env("DATABASE_PATH", "./sehat.db"))

    return Settings(
        environment=environment,
        log_level=_env("LOG_LEVEL", "INFO"),
        database_path=db_path,
        cors_allowed_origins=[o.strip() for o in _env("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()],
        jwt_secret_key=jwt_secret,
        jwt_algorithm=_env("JWT_ALGORITHM", "HS256"),
        jwt_expire_minutes=int(_env("JWT_EXPIRE_MINUTES", "1440")),
        azure_openai_api_key=_env("AZURE_OPENAI_API_KEY"),
        azure_openai_endpoint=_env("AZURE_OPENAI_ENDPOINT"),
        azure_openai_deployment_name=_env("AZURE_OPENAI_DEPLOYMENT_NAME"),
        azure_openai_api_version=_env("AZURE_OPENAI_API_VERSION"),
        voice_enabled=_flag("VOICE_ENABLED"),
        voice_vad_enabled=_flag("VOICE_VAD_ENABLED", True),
        voice_local_asr_enabled=_flag("VOICE_LOCAL_ASR_ENABLED"),
        voice_local_model_dir=_path(_env("VOICE_LOCAL_MODEL_DIR", "./models/voice")),
        voice_cloud_stt_enabled=_flag("VOICE_CLOUD_STT_ENABLED"),
        voice_tts_enabled=_flag("VOICE_TTS_ENABLED"),
        sarvam_api_key=_env("SARVAM_API_KEY"),
        sarvam_base_url=_sarvam_base_url(_env("SARVAM_BASE_URL", "https://api.sarvam.ai")),
        sarvam_timeout_s=float(_env("SARVAM_TIMEOUT_S", "20")),
        voice_max_seconds=min(int(_env("VOICE_MAX_SECONDS", "30")), 30),  # Sarvam REST limit is <30 s
        ocr_enabled=_flag("OCR_ENABLED"),
        ocr_model_dir=_path(_env("OCR_MODEL_DIR", "./models/ocr")),
        ocr_document_dir=_path(_env("OCR_DOCUMENT_DIR", "./data/documents")),
        ocr_worker_python=_path(_env("OCR_WORKER_PYTHON", "./.venv-ocr/bin/python")),
        ocr_surya_enabled=_flag("OCR_SURYA_ENABLED"),
        ocr_chandra_enabled=_flag("OCR_CHANDRA_ENABLED"),
        ocr_max_bytes=min(int(_env("OCR_MAX_BYTES", str(10 * 1024 * 1024))), 20 * 1024 * 1024),
        ocr_page_timeout_s=float(_env("OCR_PAGE_TIMEOUT_S", "180")),
        ocr_rxnorm_db=_path(_env("OCR_RXNORM_DB", "./models/rxnorm/rxnorm.sqlite")),
        ocr_retention_days=_retention_days(_flag("OCR_ENABLED"), _env("OCR_RETENTION_DAYS")),
        **_ai_settings(),
        medgemma_enabled=_not_implemented("MEDGEMMA_ENABLED", "MedGemma image description (deferred)"),
        account_facilities=_account_facilities(_env("ACCOUNT_FACILITIES")),
    )


_FACILITY_CODE = re.compile(r"^[A-Z0-9-]{3,32}$")  # same pattern as CaseCreate.facility_code


def _account_facilities(value: str) -> dict[str, frozenset[str] | None] | None:
    """ACCOUNT_FACILITIES=user=CODE[,CODE...];user2=*  (docs/17 §8). Unset/blank = isolation off. Parsed strictly:
    any malformed entry, unknown account, duplicate account or bad facility code refuses to start. Refusals name
    the rule, never the offending value; the log line carries counts only."""
    if not value:
        return None
    from app.auth import DEMO_ACCOUNTS  # lazy: app.auth imports this module

    out: dict[str, frozenset[str] | None] = {}
    for entry in value.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        user, sep, codes_raw = entry.partition("=")
        user = user.strip()
        if not sep or not user:
            raise RuntimeError("ACCOUNT_FACILITIES entries must be username=FACILITY[,FACILITY...] or username=*, separated by ';'")
        if user not in DEMO_ACCOUNTS:
            raise RuntimeError("ACCOUNT_FACILITIES names an account that does not exist")
        if user in out:
            raise RuntimeError("ACCOUNT_FACILITIES lists an account more than once")
        codes = [c.strip() for c in codes_raw.split(",")]
        if codes == ["*"]:
            out[user] = None
            continue
        if not codes or any(not _FACILITY_CODE.fullmatch(c) for c in codes):
            raise RuntimeError("ACCOUNT_FACILITIES facility codes must match ^[A-Z0-9-]{3,32}$, or be a single '*'")
        out[user] = frozenset(codes)
    if not out:
        raise RuntimeError("ACCOUNT_FACILITIES is set but has no entries; leave it empty to turn facility isolation off")
    logger.info("facility isolation enforced: %d account(s) mapped, %d unrestricted", len(out), sum(1 for v in out.values() if v is None))
    return out


def _ai_settings() -> dict:
    """AI provider settings (docs/16 §7). Every refusal names exactly what is missing; values are never echoed."""
    provider = (_env("AI_PROVIDER") or "none").lower()  # blank = default
    if provider not in AI_PROVIDERS:
        raise RuntimeError("AI_PROVIDER must be one of: none, fake, azure")
    passes = _env("AI_MAKER_PASSES") or "3"
    if not passes.isdigit() or not 3 <= int(passes) <= 5:
        raise RuntimeError("AI_MAKER_PASSES must be a whole number from 3 to 5")
    cloud = _flag("AI_CLOUD_ENABLED")
    synthetic_only = _flag("AI_CLOUD_SYNTHETIC_DATA_ONLY")
    if provider == "azure":
        if not cloud:
            raise RuntimeError("AI_PROVIDER=azure requires AI_CLOUD_ENABLED=1 (redacted case text leaves this machine)")
        if not synthetic_only:
            raise RuntimeError("AI_PROVIDER=azure requires AI_CLOUD_SYNTHETIC_DATA_ONLY=1: PII redaction has known misses "
                               "(docs/16 §8), so only synthetic data may be sent to a cloud model in this build")
        missing = [n for n in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT_NAME", "AZURE_OPENAI_API_VERSION") if not _env(n)]
        if missing:
            raise RuntimeError(f"AI_PROVIDER=azure requires {', '.join(missing)}")
        _azure_endpoint(_env("AZURE_OPENAI_ENDPOINT"))
    fake_mode = (_env("AI_FAKE_MODE") or "honest").lower()
    if fake_mode not in ("honest", "demo_disagreement"):
        raise RuntimeError("AI_FAKE_MODE must be honest or demo_disagreement")
    if fake_mode != "honest" and provider != "fake":
        raise RuntimeError("AI_FAKE_MODE applies only to AI_PROVIDER=fake")
    timeout = float(_env("AI_TIMEOUT_S") or "30")
    if not 1 <= timeout <= 120:
        raise RuntimeError("AI_TIMEOUT_S must be between 1 and 120 seconds")
    return {
        "ai_provider": provider,
        "ai_cloud_enabled": cloud,
        "ai_cloud_synthetic_data_only": synthetic_only,
        "ai_maker_passes": int(passes),
        "ai_fake_mode": fake_mode,
        "ai_timeout_s": timeout,
        "translation_enabled": _flag("TRANSLATION_ENABLED"),
        "translation_model_dir": _path(_env("TRANSLATION_MODEL_DIR") or "./models/translation"),
    }


def _azure_endpoint(value: str) -> str:
    """AZURE_OPENAI_ENDPOINT must be https://<resource><allowed suffix> (optionally a trailing `/`). The value
    itself is not echoed."""
    allowed = ", ".join("*" + s for s in AZURE_OPENAI_HOST_SUFFIXES)
    error = RuntimeError(f"AZURE_OPENAI_ENDPOINT must be https://<resource> with host in: {allowed} (no path, port, credentials, query)")
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise error
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise error from None
    host = (parts.hostname or "").lower()
    if (
        parts.scheme.lower() != "https"
        or not any(host.endswith(sfx) and len(host) > len(sfx) for sfx in AZURE_OPENAI_HOST_SUFFIXES)
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise error
    return f"https://{host}/"


def _not_implemented(name: str, feature: str) -> bool:
    """Placeholder flag for a later phase: parsed like any flag (so "0" is off), refused if switched on."""
    if _flag(name):
        raise RuntimeError(f"{name}=1 but {feature} is not implemented in this build; set {name}=0")
    return False


def _retention_days(ocr_enabled: bool, value: str) -> int | None:
    """OCR_RETENTION_DAYS: a positive integer, or `none` (keep until deleted). Required when OCR is enabled."""
    v = value.strip().lower()
    if not v:
        if ocr_enabled:
            raise RuntimeError("OCR_RETENTION_DAYS must be set when OCR_ENABLED=1: a number of days, or 'none' to keep documents until a reviewer deletes them")
        return None
    if v == "none":
        return None
    if not v.isdigit() or int(v) < 1:
        raise RuntimeError("OCR_RETENTION_DAYS must be a positive whole number of days, or 'none'")
    return int(v)


def _sarvam_base_url(value: str) -> str:
    """`SARVAM_BASE_URL` must be plain `https://<allowed host>` (optionally `:443` or a trailing `/`).
    Returns the normalised URL, so nothing else from the variable reaches a request. Refuses to start
    otherwise; the value itself is not echoed (it could hold credentials)."""
    allowed = ", ".join(sorted(SARVAM_ALLOWED_HOSTS))
    error = RuntimeError(f"SARVAM_BASE_URL must be https://<host> with host in: {allowed} (no path, port, credentials, query)")
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise error
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise error from None
    host = (parts.hostname or "").lower()
    if (
        parts.scheme.lower() != "https"
        or host not in SARVAM_ALLOWED_HOSTS
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise error
    return f"https://{host}"


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path
