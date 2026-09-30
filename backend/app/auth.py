"""Hackathon JWT auth: pre-seeded demo accounts, role claim, header cross-check.

See docs/04_Security_Privacy_Access.md §2 (Hackathon Authentication) and
docs/06_API_Data_Contracts.md §1 (X-SEHAT-* headers).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

import jwt
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.errors import ApiError


class Role(str, Enum):
    PATIENT = "patient"
    ANM = "anm"
    MEDICAL_OFFICER = "medical_officer"
    SUPERVISOR = "supervisor"
    ADMIN = "admin"


# Demo accounts from docs/09 §1.4. Each account has exactly one role.
DEMO_ACCOUNTS: dict[str, Role] = {
    "patient_demo": Role.PATIENT,
    "anm_demo": Role.ANM,
    "mo_demo": Role.MEDICAL_OFFICER,
    "supervisor_demo": Role.SUPERVISOR,
}

_USER_ID_NAMESPACE = uuid.UUID("5e4a7a10-0000-4000-8000-5e4a7a1a0001")


def demo_user_id(username: str) -> str:
    return str(uuid.uuid5(_USER_ID_NAMESPACE, username))


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    role: Role


def create_access_token(username: str, role: Role) -> tuple[str, int]:
    settings = get_settings()
    expires_in = settings.jwt_expire_minutes * 60
    now = datetime.now(timezone.utc)
    claims = {
        "sub": demo_user_id(username),
        "username": username,
        "role": role.value,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm), expires_in


def authenticate_demo_user(username: str, role: Role) -> Principal:
    expected = DEMO_ACCOUNTS.get(username)
    if expected is None or expected != role:
        raise ApiError(401, "UNAUTHORIZED", "Unknown demo account or role mismatch")
    return Principal(user_id=demo_user_id(username), username=username, role=role)


def _decode(token: str) -> Principal:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "role"]},
        )
        return Principal(user_id=claims["sub"], username=claims.get("username", ""), role=Role(claims["role"]))
    except (jwt.PyJWTError, ValueError) as exc:
        raise ApiError(401, "UNAUTHORIZED", "Invalid or expired JWT") from exc


_bearer = HTTPBearer(auto_error=False)


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_sehat_role: str | None = Header(default=None),
    x_sehat_user_id: str | None = Header(default=None),
) -> Principal:
    """The JWT is authoritative. X-SEHAT-* headers are optional but must match it when sent."""
    if credentials is None:
        raise ApiError(401, "UNAUTHORIZED", "Missing bearer token")
    principal = _decode(credentials.credentials)
    if x_sehat_role is not None and x_sehat_role != principal.role.value:
        raise ApiError(403, "FORBIDDEN", "X-SEHAT-Role does not match token")
    if x_sehat_user_id is not None and x_sehat_user_id != principal.user_id:
        raise ApiError(403, "FORBIDDEN", "X-SEHAT-User-ID does not match token")
    return principal


def require_roles(*roles: Role):
    """Dependency factory: allow only the given roles."""

    async def _check(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.role not in roles:
            raise ApiError(403, "FORBIDDEN", "Role does not have permission")
        return principal

    return _check
