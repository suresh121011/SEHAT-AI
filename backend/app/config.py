"""Runtime settings, loaded once from the repo-root `.env` (see `.env.example`)."""

import logging
import os
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
    )


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
