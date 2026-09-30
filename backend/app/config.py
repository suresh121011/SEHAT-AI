"""Runtime settings, loaded once from the repo-root `.env` (see `.env.example`)."""

import logging
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

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


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, default).strip()
    return "" if value in _PLACEHOLDERS else value


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

    db_path = Path(_env("DATABASE_PATH", "./sehat.db"))
    if not db_path.is_absolute():
        db_path = REPO_ROOT / db_path

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
    )
