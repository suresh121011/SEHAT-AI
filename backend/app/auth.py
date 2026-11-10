"""Authentication: server-managed sessions (app.sessions) for pre-seeded demo accounts, role, header cross-check.

See docs/04_Security_Privacy_Access.md §2 (Hackathon Authentication) and
docs/06_API_Data_Contracts.md §1 (X-SEHAT-* headers).
"""

import uuid
from dataclasses import dataclass
from enum import Enum

import aiosqlite
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import sessions
from app.config import get_settings
from app.database import get_db
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
    # Facilities this account may access (docs/17 §8). None = unrestricted: isolation is off, the account is mapped
    # to "*", or a server-internal actor. Set only from server config (`facility_scope`), never from the client.
    facilities: frozenset[str] | None = None
    session_id: str | None = None  # the server session this request authenticated with (None for internal actors)
    auth_mode: str = "demo"  # "demo" (synthetic-data demo accounts) or "oidc"

    def may_access_facility(self, facility_code: str | None) -> bool:
        return self.facilities is None or facility_code in self.facilities


def facility_scope(username: str) -> frozenset[str] | None:
    """Scope from trusted server config (ACCOUNT_FACILITIES) by username only: never from token claims, headers,
    query or body. Isolation off → None (unrestricted). Isolation on and the account is not mapped → EMPTY (deny all
    case access), never unrestricted."""
    mapping = get_settings().account_facilities
    if mapping is None:
        return None
    if username not in mapping:
        return frozenset()
    return mapping[username]


def create_access_token(username: str, role: Role) -> tuple[str, int]:
    """Mint a demo session for an account directly (tests and scripts; the login route uses `app.sessions.create`
    inside its audited transaction). Returns (opaque session token, absolute lifetime in seconds)."""
    token = sessions.create_sync(get_settings().database_path, user_id=demo_user_id(username), username=username, role=role.value)
    return token, int(sessions.lifetimes(role.value)[1].total_seconds())


def authenticate_demo_user(username: str, role: Role) -> Principal:
    expected = DEMO_ACCOUNTS.get(username)
    if expected is None or expected != role:
        raise ApiError(401, "UNAUTHORIZED", "Unknown demo account or role mismatch")
    return Principal(user_id=demo_user_id(username), username=username, role=role, facilities=facility_scope(username))


_bearer = HTTPBearer(auto_error=False)


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_sehat_role: str | None = Header(default=None),
    x_sehat_user_id: str | None = Header(default=None),
    db: aiosqlite.Connection = Depends(get_db),
) -> Principal:
    """The server-side session is authoritative (docs/security/post-remediation-hardening.md A2): it is looked up on
    every request, so a revoked or expired session stops working at once. X-SEHAT-* headers are optional but must
    match it when sent. Facility scope still comes from server config, never from the client."""
    if credentials is None:
        raise ApiError(401, "UNAUTHORIZED", "Missing bearer token")
    sess = await sessions.resolve(db, credentials.credentials)
    if sess is None:
        raise ApiError(401, "UNAUTHORIZED", "Invalid, expired or revoked session")
    if sess.auth_mode != get_settings().auth_mode:
        # A session from the other mode (e.g. a demo session after switching to OIDC) never authorizes anything.
        raise ApiError(401, "UNAUTHORIZED", "Invalid, expired or revoked session")
    scope = sess.facilities if sess.auth_mode == "oidc" else facility_scope(sess.username)
    principal = Principal(user_id=sess.user_id, username=sess.username, role=Role(sess.role), facilities=scope,
                          session_id=sess.session_id, auth_mode=sess.auth_mode)
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
