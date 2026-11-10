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
    "your_google_ai_api_key_here",
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
AI_PROVIDERS = ("none", "fake", "azure", "local")
# AI_PROVIDER=local talks to a llama-server on this machine only (docs/16 §2a). Loopback hosts only, in code.
LOCAL_LLM_HOSTS = frozenset({"127.0.0.1", "::1"})  # literal IPs only: "localhost" resolves through /etc/hosts
MEDGEMMA_BACKENDS = ("fake", "google_ai", "azure", "local")
# A local medical-vision model is not installable in this build (docs/18 §4a): MEDGEMMA_BACKEND=local is refused by name.
# MEDGEMMA_BACKEND=local (docs/18 §4a): MedGemma 1.5 4B converted here to MLX 8-bit from the official, SHA-checked weights,
# run by the local OCR worker (.venv-ocr, Unix socket, egress blocked). Fixed path, like the Chandra build (docs/14).
MEDGEMMA_LOCAL_DIR = REPO_ROOT / "models" / "medgemma" / "medgemma-1.5-4b-it-mlx-8bit"
MEDGEMMA_LOCAL_NOT_INSTALLED = ("MEDGEMMA_BACKEND=local but the local MedGemma build is not installed: accept the model terms on Hugging Face, "
                                "then run backend/scripts/download_medgemma_local.py (docs/18 §4a). No fallback to fake or cloud")


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
    jwt_secret_key: str = field(repr=False)  # never printed (S-1)
    jwt_algorithm: str
    jwt_expire_minutes: int
    azure_openai_api_key: str = field(repr=False)  # never printed (S-1)
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
    # `local` = a pinned open-weights model on a loopback-only llama-server (docs/16 §2a); case text stays on
    # this machine but is still redacted first. There is never a fallback from one provider to another.
    ai_provider: str = "none"
    ai_cloud_enabled: bool = False
    ai_cloud_synthetic_data_only: bool = False
    ai_maker_passes: int = 3
    ai_fake_mode: str = "honest"  # honest | demo_disagreement (fake provider only; demo of a disputed value)
    ai_timeout_s: float = 30.0
    # Phase 6 P2: IndicTrans2 (indic → en), local only. Off unless enabled.
    translation_enabled: bool = False
    translation_model_dir: Path = REPO_ROOT / "models" / "translation"
    # AI_PROVIDER=local (docs/16 §2a): model key from app.ai.local_models, its directory, and the loopback server.
    local_llm_model: str = "qwen3-4b-instruct-2507-q4km"
    local_llm_model_dir: Path = REPO_ROOT / "models" / "llm"
    local_llm_url: str = "http://127.0.0.1:8091"
    # Optional NeMo Guardrails layer around extraction (docs/16 §2b); needs requirements-guardrails.txt.
    guardrails_enabled: bool = False
    # Deterministic red-flag keyword suggester (docs/16 §2c): candidates labelled keyword_rule, not AI output. On by default.
    ai_keyword_red_flags: bool = True
    # Medical image visual-findings description (architecture §10A, docs/18). Off unless enabled. `fake` = canned
    # offline outputs (not a model). `google_ai` / `azure` send the image to a cloud model: only with
    # AI_CLOUD_ENABLED=1, AI_CLOUD_SYNTHETIC_DATA_ONLY=1 and credentials, and per request only with ai_assist consent
    # and a synthetic-image attestation. Never a fallback between backends.
    medgemma_enabled: bool = False
    medgemma_backend: str = "google_ai"
    medgemma_model: str = "gemini-3.8-flash"
    google_ai_api_key: str = field(default="", repr=False)  # never printed
    medgemma_timeout_s: float = 30.0
    # Facility isolation (docs/17 §8). Trusted server config, never client input. None = isolation OFF (every
    # account sees every facility, the earlier behaviour). Otherwise username → allowed facility codes, or None
    # for "*" (unrestricted). A username missing from an active mapping gets NO facility (see auth.facility_scope).
    account_facilities: dict[str, frozenset[str] | None] | None = None
    # Server-managed sessions (docs/security/post-remediation-hardening.md A2, owner decision H5). The idle timeout
    # slides with use; the absolute lifetime never does.
    # Identity (docs/security/post-remediation-hardening.md A1, owner decision H1). `demo`: the synthetic-data demo
    # accounts. `oidc`: an OpenID Connect provider; demo login is then switched off entirely (no fallback).
    auth_mode: str = "demo"
    oidc_issuer: str = ""
    oidc_client_id: str = ""  # the expected `aud`
    oidc_jwks_url: str = ""  # empty = from the issuer's discovery document
    oidc_allowed_algs: tuple[str, ...] = ("RS256", "ES256")
    oidc_clock_skew_s: int = 60
    oidc_role_claim: str = "sehat_role"
    oidc_facility_claim: str = "sehat_facilities"
    # Patient-code entry rate limits (A4). Placeholders, not threat-modelled production thresholds: tune per deployment.
    rate_code_per_minute_principal: int = 10
    rate_code_per_hour_principal: int = 60
    rate_code_per_minute_source: int = 30
    rate_code_per_hour_source: int = 300
    rate_login_per_minute_source: int = 30
    rate_login_per_hour_source: int = 300
    rate_block_minutes: int = 15
    trusted_proxies: frozenset[str] = frozenset()
    session_staff_idle_minutes: int = 30
    session_staff_absolute_hours: int = 12
    session_patient_idle_minutes: int = 15
    session_patient_absolute_hours: int = 2

    @property
    def facility_isolation(self) -> bool:
        # OIDC sessions always carry the facilities the identity provider asserted (A1); demo uses ACCOUNT_FACILITIES.
        return self.auth_mode == "oidc" or self.account_facilities is not None

    @property
    def sarvam_configured(self) -> bool:
        return bool(self.sarvam_api_key)

    @property
    def azure_openai_configured(self) -> bool:
        return bool(self.azure_openai_api_key and self.azure_openai_endpoint and self.azure_openai_deployment_name)


@lru_cache
def get_settings() -> Settings:
    # SEHAT_DOTENV=0 reads the process environment only. The test suite sets it, so a developer .env (for example
    # MEDGEMMA_ENABLED=1) cannot change test results or refill a variable a test removed.
    if os.getenv("SEHAT_DOTENV", "1") != "0":
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

    if len(jwt_secret.encode()) < 32:  # S-2: HS256 needs at least a 256-bit key; never echo the value
        raise RuntimeError("JWT_SECRET_KEY must be at least 32 bytes (run backend/scripts/rotate_jwt_secret.py)")
    jwt_algorithm = _env("JWT_ALGORITHM", "HS256")
    if jwt_algorithm not in ("HS256", "HS384", "HS512"):
        raise RuntimeError("JWT_ALGORITHM must be HS256, HS384 or HS512")
    cors = [o.strip() for o in _env("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
    if "*" in cors:
        raise RuntimeError("CORS_ALLOWED_ORIGINS must list exact origins, not *")

    # F2 / owner decision D-A: facility isolation fails closed. Running every account across every facility must be
    # an explicit choice (ALLOW_UNSCOPED_DEMO=true), never the silent default.
    auth_mode, oidc = _auth_settings()
    account_facilities = _account_facilities(_env("ACCOUNT_FACILITIES"))
    if auth_mode == "oidc" and (account_facilities is not None or _flag("ALLOW_UNSCOPED_DEMO")):
        # Unsafe mixed configuration: the demo account mapping or demo opt-out next to real identities.
        raise RuntimeError("AUTH_MODE=oidc cannot be combined with ACCOUNT_FACILITIES or ALLOW_UNSCOPED_DEMO (demo-only settings)")
    if auth_mode == "demo" and account_facilities is None and not _flag("ALLOW_UNSCOPED_DEMO"):
        raise RuntimeError("facility isolation is off: set ACCOUNT_FACILITIES (see .env.example), or ALLOW_UNSCOPED_DEMO=true "
                           "to run an unscoped single-facility demo knowingly")

    db_path = _path(_env("DATABASE_PATH", "./sehat.db"))

    return Settings(
        environment=environment,
        log_level=_env("LOG_LEVEL", "INFO"),
        database_path=db_path,
        cors_allowed_origins=cors,
        jwt_secret_key=jwt_secret,
        jwt_algorithm=jwt_algorithm,
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
        **_medgemma_settings(_flag("OCR_ENABLED")),
        account_facilities=account_facilities,
        auth_mode=auth_mode,
        **oidc,
        rate_code_per_minute_principal=_bounded_int("RATE_CODE_PER_MINUTE_PRINCIPAL", 10, 1, 1000),
        rate_code_per_hour_principal=_bounded_int("RATE_CODE_PER_HOUR_PRINCIPAL", 60, 1, 10000),
        rate_code_per_minute_source=_bounded_int("RATE_CODE_PER_MINUTE_SOURCE", 30, 1, 1000),
        rate_code_per_hour_source=_bounded_int("RATE_CODE_PER_HOUR_SOURCE", 300, 1, 100000),
        rate_login_per_minute_source=_bounded_int("RATE_LOGIN_PER_MINUTE_SOURCE", 30, 1, 1000),
        rate_login_per_hour_source=_bounded_int("RATE_LOGIN_PER_HOUR_SOURCE", 300, 1, 100000),
        rate_block_minutes=_bounded_int("RATE_BLOCK_MINUTES", 15, 1, 1440),
        trusted_proxies=_trusted_proxies(_env("TRUSTED_PROXIES")),
        session_staff_idle_minutes=_bounded_int("SESSION_STAFF_IDLE_MINUTES", 30, 1, 240),
        session_staff_absolute_hours=_bounded_int("SESSION_STAFF_ABSOLUTE_HOURS", 12, 1, 24),
        session_patient_idle_minutes=_bounded_int("SESSION_PATIENT_IDLE_MINUTES", 15, 1, 120),
        session_patient_absolute_hours=_bounded_int("SESSION_PATIENT_ABSOLUTE_HOURS", 2, 1, 24),
    )


_OIDC_ALGS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"})  # asymmetric only


def _https_url(name: str, value: str) -> str:
    """An https URL, or http only for a loopback host (a local test issuer)."""
    from urllib.parse import urlparse

    u = urlparse(value)
    loopback = u.hostname in ("localhost", "127.0.0.1", "::1")
    if not u.hostname or u.scheme not in ("https", "http") or (u.scheme == "http" and not loopback) or u.username or u.password:
        raise RuntimeError(f"{name} must be an https URL (http only for localhost)")
    return value.rstrip("/") if name == "OIDC_ISSUER" else value


def _auth_settings() -> tuple[str, dict]:
    """AUTH_MODE and the OIDC settings. `oidc` with anything missing or unsafe refuses to start (fail closed); it never
    falls back to demo login."""
    mode = _env("AUTH_MODE", "demo")
    if mode not in ("demo", "oidc"):
        raise RuntimeError("AUTH_MODE must be demo or oidc")
    if mode == "demo":
        return mode, {}
    issuer, client_id = _env("OIDC_ISSUER"), _env("OIDC_CLIENT_ID")
    if not issuer or not client_id:
        raise RuntimeError("AUTH_MODE=oidc requires OIDC_ISSUER and OIDC_CLIENT_ID")
    algs = tuple(a.strip() for a in _env("OIDC_ALLOWED_ALGS", "RS256,ES256").split(",") if a.strip())
    if not algs or any(a not in _OIDC_ALGS for a in algs):
        raise RuntimeError("OIDC_ALLOWED_ALGS must list asymmetric algorithms only (RS*, PS*, ES*); never HS* or none")
    jwks = _env("OIDC_JWKS_URL")
    claim = re.compile(r"^[A-Za-z0-9_.:/-]{1,64}$")
    role_claim, fac_claim = _env("OIDC_ROLE_CLAIM", "sehat_role"), _env("OIDC_FACILITY_CLAIM", "sehat_facilities")
    if not claim.fullmatch(role_claim) or not claim.fullmatch(fac_claim):
        raise RuntimeError("OIDC_ROLE_CLAIM and OIDC_FACILITY_CLAIM must be claim names")
    return mode, {
        "oidc_issuer": _https_url("OIDC_ISSUER", issuer),
        "oidc_client_id": client_id,
        "oidc_jwks_url": _https_url("OIDC_JWKS_URL", jwks) if jwks else "",
        "oidc_allowed_algs": algs,
        "oidc_clock_skew_s": _bounded_int("OIDC_CLOCK_SKEW_S", 60, 0, 300),
        "oidc_role_claim": role_claim,
        "oidc_facility_claim": fac_claim,
    }


def _trusted_proxies(value: str) -> frozenset[str]:
    """Peers whose X-Forwarded-For is believed (exact IP addresses). Empty = never believe the header."""
    import ipaddress

    out = set()
    for part in (p.strip() for p in value.split(",") if p.strip()):
        try:
            out.add(str(ipaddress.ip_address(part)))
        except ValueError as exc:
            raise RuntimeError("TRUSTED_PROXIES must be a comma-separated list of IP addresses") from exc
    return frozenset(out)


def _bounded_int(name: str, default: int, lo: int, hi: int) -> int:
    """A whole number within [lo, hi]; anything else refuses to start (never silently clamped)."""
    raw = _env(name, str(default))
    if not raw.isdigit() or not lo <= int(raw) <= hi:
        raise RuntimeError(f"{name} must be a whole number from {lo} to {hi}")
    return int(raw)


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
        raise RuntimeError("AI_PROVIDER must be one of: none, fake, azure, local")
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
    from app.ai.local_models import MODELS as LOCAL_MODELS

    local_model = _env("LOCAL_LLM_MODEL") or "qwen3-4b-instruct-2507-q4km"
    if local_model not in LOCAL_MODELS:
        raise RuntimeError(f"LOCAL_LLM_MODEL must be one of: {', '.join(LOCAL_MODELS)}")
    local_url = _local_llm_url(_env("LOCAL_LLM_URL") or "http://127.0.0.1:8091")
    # Local: 3 passes share one laptop GPU (measured max 25.8 s, docs/16 §2a.4), so the default is 60 s there.
    timeout = float(_env("AI_TIMEOUT_S") or ("60" if provider == "local" else "30"))
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
        "local_llm_model": local_model,
        "local_llm_model_dir": _path(_env("LOCAL_LLM_MODEL_DIR") or "./models/llm"),
        "local_llm_url": local_url,
        "guardrails_enabled": _flag("GUARDRAILS_ENABLED"),
        "ai_keyword_red_flags": _flag("AI_KEYWORD_RED_FLAGS", default=True),
    }


def _local_llm_url(value: str) -> str:
    """LOCAL_LLM_URL must be http://<loopback host>:<port> with nothing else, so redacted case text can only go to
    this machine. Returns the normalised URL; the value is not echoed."""
    error = RuntimeError("LOCAL_LLM_URL must be http://127.0.0.1:<port> (or http://[::1]:<port>); no path, credentials or query")
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise error
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise error from None
    host = (parts.hostname or "").lower()
    if (parts.scheme.lower() != "http" or host not in LOCAL_LLM_HOSTS or port is None or parts.username is not None
            or parts.password is not None or parts.path not in ("", "/") or parts.query or parts.fragment):
        raise error
    return f"http://{'[::1]' if host == '::1' else host}:{port}"


def _medgemma_settings(ocr_enabled: bool) -> dict:
    """MEDGEMMA_* (docs/18 §4). Validated only when MEDGEMMA_ENABLED=1. Refusals name what is missing, never values."""
    enabled = _flag("MEDGEMMA_ENABLED")
    backend = (_env("MEDGEMMA_BACKEND") or "google_ai").lower()
    model = _env("MEDGEMMA_MODEL") or "gemini-3.8-flash"
    key = _env("GOOGLE_AI_API_KEY")
    # local: model load (+ a one-time hash of the 8-bit build) and generation on this machine take longer than a cloud call
    timeout_raw = _env("MEDGEMMA_TIMEOUT_S") or ("120" if backend == "local" else "30")
    try:
        timeout = float(timeout_raw)
    except ValueError:
        raise RuntimeError("MEDGEMMA_TIMEOUT_S must be a number of seconds") from None
    if enabled:
        if not ocr_enabled:
            raise RuntimeError("MEDGEMMA_ENABLED=1 requires OCR_ENABLED=1 (images are uploaded through the document route)")
        if backend not in MEDGEMMA_BACKENDS:
            raise RuntimeError("MEDGEMMA_BACKEND must be one of: fake, google_ai, azure, local")
        if backend == "local":
            if not (MEDGEMMA_LOCAL_DIR / "SEHAT_ENGINE_MANIFEST.json").is_file():
                raise RuntimeError(MEDGEMMA_LOCAL_NOT_INSTALLED)
            if not _path(_env("OCR_WORKER_PYTHON", "./.venv-ocr/bin/python")).is_file():
                raise RuntimeError("MEDGEMMA_BACKEND=local requires the OCR worker venv (.venv-ocr, docs/14 §9): it runs the model")
        if not 1 <= timeout <= 120:
            raise RuntimeError("MEDGEMMA_TIMEOUT_S must be between 1 and 120 seconds")
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,100}", model):
            raise RuntimeError("MEDGEMMA_MODEL must be a model id (letters, digits, . _ : / -)")
        if backend in ("google_ai", "azure"):
            if not _flag("AI_CLOUD_ENABLED"):
                raise RuntimeError(f"MEDGEMMA_BACKEND={backend} requires AI_CLOUD_ENABLED=1 (images leave this machine)")
            if not _flag("AI_CLOUD_SYNTHETIC_DATA_ONLY"):
                raise RuntimeError(f"MEDGEMMA_BACKEND={backend} requires AI_CLOUD_SYNTHETIC_DATA_ONLY=1: only synthetic/demo images may be "
                                   "sent to a cloud model in this build")
        if backend == "google_ai" and not key:
            raise RuntimeError("MEDGEMMA_BACKEND=google_ai requires GOOGLE_AI_API_KEY")
        if backend == "azure":
            missing = [n for n in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT_NAME", "AZURE_OPENAI_API_VERSION") if not _env(n)]
            if missing:
                raise RuntimeError(f"MEDGEMMA_BACKEND=azure requires {', '.join(missing)}")
            _azure_endpoint(_env("AZURE_OPENAI_ENDPOINT"))
    return {"medgemma_enabled": enabled, "medgemma_backend": backend, "medgemma_model": model, "google_ai_api_key": key,
            "medgemma_timeout_s": timeout if 1 <= timeout <= 120 else 30.0}


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
