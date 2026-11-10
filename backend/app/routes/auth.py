import uuid
from typing import Literal

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app import audit, oidc, ratelimit, sessions
from app.auth import Principal, Role, authenticate_demo_user, get_current_principal, require_roles
from app.config import get_settings
from app.database import get_db, transaction
from app.errors import ApiError, not_found

router = APIRouter(prefix="/auth", tags=["auth"])


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


class LoginRequest(BaseModel):
    username: str = Field(max_length=64, pattern=r"^[a-z0-9_]{1,64}$")  # demo account ids only
    role: Role


class LoginResponse(BaseModel):
    access_token: str  # an opaque server-session token (not a JWT); send it as a Bearer token
    token_type: str = "bearer"
    expires_in: int  # seconds until the session's absolute lifetime ends (the idle timeout may end it sooner)
    user_id: str
    username: str
    role: Role
    session_id: str
    auth_mode: Literal["demo", "oidc"]


class MeResponse(BaseModel):
    user_id: str
    username: str
    role: Role
    # From server config only (docs/17 §8). null = unrestricted (isolation off, or "*"); [] = no facility.
    facility_scope: list[str] | None
    facility_isolation: Literal["enforced", "off"]
    session_id: str | None
    auth_mode: Literal["demo", "oidc"]


class OidcSessionRequest(BaseModel):
    model_config = {"extra": "forbid", "hide_input_in_errors": True}

    # A compact JWS (header.payload.signature, base64url): never free text, so it cannot carry anything else.
    id_token: str = Field(min_length=20, max_length=16384, pattern=r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")
    nonce: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


async def _refuse(db, request: Request, mode: str, reason: str, status: int = 401) -> ApiError:
    async with transaction(db):
        await audit.record(db, principal=None, action="auth_login_failed", outcome="denied", request_id=_rid(request),
                           details=audit.AuthLoginFailedDetails(auth_mode=mode, reason=reason))  # type: ignore[arg-type]
    if status == 404:
        return ApiError(404, "FEATURE_DISABLED", "This login method is not enabled on this server")
    return ApiError(401, "UNAUTHORIZED", "Sign-in failed")  # one message for every reason: nothing to probe


@router.get("/mode")
async def auth_mode() -> dict:
    """Public: which sign-in the login page should offer. Never both: demo accounts exist only in demo mode."""
    mode = get_settings().auth_mode
    return {"auth_mode": mode, "demo_accounts": mode == "demo"}


@router.post("/oidc/session", response_model=LoginResponse)
async def oidc_session(body: OidcSessionRequest, request: Request, db: aiosqlite.Connection = Depends(get_db)) -> LoginResponse:
    """Real login (A1): the frontend posts the provider's ID token and the nonce it issued; a valid token becomes a
    server session. Only with AUTH_MODE=oidc; there is never a fallback to demo login."""
    settings = get_settings()
    await ratelimit.check_login(db, request)  # before anything is validated or audited
    if settings.auth_mode != "oidc":
        raise await _refuse(db, request, "oidc", "auth_mode_disabled", 404)
    try:
        # One validator per app, so the provider's keys are cached across sign-ins (PyJWKClient) instead of fetched each time.
        validator = getattr(request.app.state, "oidc_validator", None)
        if validator is None or validator.s is not settings:
            validator = oidc.Validator(settings, getattr(request.app.state, "oidc_jwks", None))
            request.app.state.oidc_validator = validator
        ident = await run_in_threadpool(validator.validate, body.id_token, body.nonce)  # key fetch is blocking I/O
    except oidc.OidcError as exc:
        raise await _refuse(db, request, "oidc", exc.reason) from None
    username = f"oidc-{ident.user_id[:8]}"  # display handle only; never the provider's email or name
    principal = Principal(user_id=ident.user_id, username=username, role=ident.role, facilities=ident.facilities, auth_mode="oidc")
    async with transaction(db):
        token, sess = await sessions.create(db, user_id=ident.user_id, username=username, role=ident.role.value, auth_mode="oidc",
                                            issuer=ident.issuer, subject=ident.subject, facilities=ident.facilities)
        await audit.record(db, principal=principal, action="auth_login_succeeded", outcome="success", request_id=_rid(request),
                           details=audit.AuthLoginDetails(auth_mode="oidc", session_id=sess.session_id))
    return LoginResponse(access_token=token, expires_in=int(sessions.lifetimes(ident.role.value)[1].total_seconds()), user_id=ident.user_id,
                         username=username, role=ident.role, session_id=sess.session_id, auth_mode="oidc")


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request, db: aiosqlite.Connection = Depends(get_db)) -> LoginResponse:
    """Demo login (synthetic data only): username + role → a server session. No passwords; demo accounts only.
    Switched off entirely when AUTH_MODE=oidc."""
    await ratelimit.check_login(db, request)  # before anything is validated or audited
    if get_settings().auth_mode != "demo":
        raise await _refuse(db, request, "demo", "auth_mode_disabled", 404)
    try:
        principal = authenticate_demo_user(body.username, body.role)
    except ApiError:
        async with transaction(db):
            await audit.record(db, principal=None, action="auth_login_failed", outcome="denied", request_id=_rid(request),
                               details=audit.AuthLoginFailedDetails(auth_mode="demo", reason="unknown_account_or_role"))
        raise
    async with transaction(db):
        token, sess = await sessions.create(db, user_id=principal.user_id, username=principal.username, role=principal.role.value, auth_mode="demo")
        await audit.record(db, principal=principal, action="auth_login_succeeded", outcome="success", request_id=_rid(request),
                           details=audit.AuthLoginDetails(auth_mode="demo", session_id=sess.session_id))
    return LoginResponse(
        access_token=token,
        expires_in=int(sessions.lifetimes(principal.role.value)[1].total_seconds()),
        user_id=principal.user_id,
        username=principal.username,
        role=principal.role,
        session_id=sess.session_id,
        auth_mode="demo",
    )


@router.get("/me", response_model=MeResponse)
async def me(principal: Principal = Depends(get_current_principal)) -> MeResponse:
    """Session introspection used by the frontend's server-side route guards."""
    return MeResponse(
        user_id=principal.user_id,
        username=principal.username,
        role=principal.role,
        facility_scope=sorted(principal.facilities) if principal.facilities is not None else None,
        facility_isolation="enforced" if get_settings().facility_isolation else "off",
        session_id=principal.session_id,
        auth_mode=principal.auth_mode,  # type: ignore[arg-type]
    )


@router.post("/logout")
async def logout(request: Request, principal: Principal = Depends(get_current_principal), db: aiosqlite.Connection = Depends(get_db)) -> dict:
    """Revoke the session this request used. The token stops working at once, on every backend process."""
    async with transaction(db):
        done = await sessions.revoke(db, principal.session_id, "logout", principal.user_id)
        await audit.record(db, principal=principal, action="session_revoked", outcome="success", request_id=_rid(request),
                           details=audit.SessionRevokedDetails(scope="logout", session_id=principal.session_id, revoked_count=int(done)))
    return {"revoked": done}


@router.get("/sessions")
async def my_sessions(principal: Principal = Depends(get_current_principal), db: aiosqlite.Connection = Depends(get_db)) -> dict:
    """Your own live sessions (public ids and times only)."""
    rows = await sessions.list_active(db, principal.user_id)
    return {"sessions": [{**r, "current": r["session_id"] == principal.session_id} for r in rows]}


@router.post("/sessions/{session_id}/revoke")
async def revoke_one(session_id: uuid.UUID, request: Request, principal: Principal = Depends(get_current_principal),
                     db: aiosqlite.Connection = Depends(get_db)) -> dict:
    """Revoke one of your own sessions. Someone else's session id looks exactly like an unknown one (404)."""
    async with transaction(db):
        done = await sessions.revoke(db, str(session_id), "user_revoked", principal.user_id, user_id=principal.user_id)
        if not done:
            raise not_found()
        await audit.record(db, principal=principal, action="session_revoked", outcome="success", request_id=_rid(request),
                           details=audit.SessionRevokedDetails(scope="own_session", session_id=str(session_id), revoked_count=1))
    return {"revoked": True}


@router.post("/sessions/revoke-all")
async def revoke_all_mine(request: Request, principal: Principal = Depends(get_current_principal), db: aiosqlite.Connection = Depends(get_db)) -> dict:
    """Sign out everywhere: revoke every session of this account, including this one."""
    async with transaction(db):
        n = await sessions.revoke_user(db, principal.user_id, "user_revoked_all", principal.user_id)
        await audit.record(db, principal=principal, action="session_revoked", outcome="success", request_id=_rid(request),
                           details=audit.SessionRevokedDetails(scope="own_all", revoked_count=n))
    return {"revoked_count": n}


@router.post("/admin/users/{user_id}/sessions/revoke")
async def admin_revoke_user(user_id: uuid.UUID, request: Request, principal: Principal = Depends(require_roles(Role.ADMIN)),
                            db: aiosqlite.Connection = Depends(get_db)) -> dict:
    """Admin only: revoke every session of one user (for example a lost device or a departed staff member)."""
    async with transaction(db):
        n = await sessions.revoke_user(db, str(user_id), "admin_revoked", principal.user_id)
        await audit.record(db, principal=principal, action="session_revoked", outcome="success", request_id=_rid(request),
                           details=audit.SessionRevokedDetails(scope="admin_user_all", target_user_id=str(user_id), revoked_count=n))
    return {"revoked_count": n}
