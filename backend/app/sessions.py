"""Server-managed sessions (docs/security/post-remediation-hardening.md A2).

A session token is 256 random bits, given to the client once and never stored: the database keeps only its SHA-256.
Every authenticated request looks the session up, so a revocation takes effect on the next request, survives a
restart, and applies to every backend process that shares the database. There are no refresh tokens: an idle timeout
that slides with use, capped by an absolute lifetime that never moves.

Tokens and session hashes are never logged or written to the audit log; the public `session_id` (a random UUID) is
what users and admins see and revoke.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from app.config import get_settings

TOKEN_PREFIX = "sehat_s1."
_TOKEN_RX = re.compile(r"^sehat_s1\.[A-Za-z0-9_-]{43}$")  # token_urlsafe(32) is 43 characters
SLIDE_AFTER = timedelta(seconds=60)  # last_seen / idle expiry is refreshed at most once a minute per session


@dataclass(frozen=True)
class Session:
    session_id: str
    user_id: str
    username: str
    role: str
    auth_mode: str
    issuer: str | None
    subject: str | None
    absolute_expires_at: str
    # OIDC only: the facilities the identity provider asserted at login. None = unrestricted ("*"); for demo sessions
    # this is unused and scope comes from server config (ACCOUNT_FACILITIES).
    facilities: frozenset[str] | None = None


_STAR = '"*"'


def _facilities_json(auth_mode: str, facilities: frozenset[str] | None) -> str | None:
    if auth_mode != "oidc":
        return None
    return _STAR if facilities is None else json.dumps(sorted(facilities))


def _facilities(raw: str | None) -> frozenset[str] | None:
    if raw is None or raw == _STAR:
        return None
    return frozenset(json.loads(raw))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="microseconds")  # fixed width, UTC: ISO strings compare like the times they hold


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def lifetimes(role: str) -> tuple[timedelta, timedelta]:
    s = get_settings()
    if role == "patient":
        return timedelta(minutes=s.session_patient_idle_minutes), timedelta(hours=s.session_patient_absolute_hours)
    return timedelta(minutes=s.session_staff_idle_minutes), timedelta(hours=s.session_staff_absolute_hours)


def _new_row(user_id: str, username: str, role: str, auth_mode: str, issuer: str | None, subject: str | None,
             facilities: frozenset[str] | None = None) -> tuple[str, tuple]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    idle, absolute = lifetimes(role)
    now = _now()
    abs_at = now + absolute
    row = (str(uuid.uuid4()), token_hash(token), user_id, username, role, auth_mode, issuer, subject,
           _facilities_json(auth_mode, facilities), _iso(now), _iso(now), _iso(min(now + idle, abs_at)), _iso(abs_at))
    return token, row


_INSERT = ("INSERT INTO sessions (session_id, token_sha256, user_id, username, role, auth_mode, issuer, subject, facilities_json, "
           "created_at, last_seen_at, idle_expires_at, absolute_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


async def create(conn: aiosqlite.Connection, *, user_id: str, username: str, role: str, auth_mode: str,
                 issuer: str | None = None, subject: str | None = None, facilities: frozenset[str] | None = None) -> tuple[str, Session]:
    """Insert a session inside the caller's transaction (so it commits with its audit event). Returns the token,
    which exists only in this return value from now on."""
    token, row = _new_row(user_id, username, role, auth_mode, issuer, subject, facilities)
    await conn.execute(_INSERT, row)
    return token, Session(row[0], user_id, username, role, auth_mode, issuer, subject, row[12], _facilities(row[8]))


def create_sync(db_path: Path, *, user_id: str, username: str, role: str, auth_mode: str = "demo") -> str:
    """Synchronous variant for tests and scripts that mint a session for an account without the login route."""
    token, row = _new_row(user_id, username, role, auth_mode, None, None)
    with sqlite3.connect(db_path) as db:
        db.execute(_INSERT, row)
    return token


async def resolve(conn: aiosqlite.Connection, token: str) -> Session | None:
    """The live session for this token, or None (unknown, malformed, revoked, idle-expired or past its absolute
    lifetime). Slides the idle timeout, never past the absolute lifetime, and never on a revoked row."""
    if not _TOKEN_RX.fullmatch(token):
        return None
    async with conn.execute("SELECT * FROM sessions WHERE token_sha256 = ?", (token_hash(token),)) as cur:
        r = await cur.fetchone()
    now = _iso(_now())
    if r is None or r["revoked_at"] is not None or now >= r["idle_expires_at"] or now >= r["absolute_expires_at"]:
        return None
    if _iso(_now() - SLIDE_AFTER) > r["last_seen_at"]:
        idle, _ = lifetimes(r["role"])
        new_idle = min(_iso(_now() + idle), r["absolute_expires_at"])
        await conn.execute("UPDATE sessions SET last_seen_at = ?, idle_expires_at = ? WHERE session_id = ? AND revoked_at IS NULL",
                           (now, new_idle, r["session_id"]))
    return Session(r["session_id"], r["user_id"], r["username"], r["role"], r["auth_mode"], r["issuer"], r["subject"], r["absolute_expires_at"],
                   _facilities(r["facilities_json"]))


async def revoke(conn: aiosqlite.Connection, session_id: str, reason: str, by: str, *, user_id: str | None = None) -> bool:
    """Revoke one live session (optionally only if it belongs to `user_id`). True if this call revoked it."""
    sql = "UPDATE sessions SET revoked_at = ?, revoked_reason = ?, revoked_by = ? WHERE session_id = ? AND revoked_at IS NULL"
    args: tuple = (_iso(_now()), reason, by, session_id)
    if user_id is not None:
        sql += " AND user_id = ?"
        args += (user_id,)
    cur = await conn.execute(sql, args)
    return cur.rowcount == 1


async def revoke_user(conn: aiosqlite.Connection, user_id: str, reason: str, by: str) -> int:
    """Revoke every live session of one user. Other users' sessions are untouched. Returns how many."""
    cur = await conn.execute("UPDATE sessions SET revoked_at = ?, revoked_reason = ?, revoked_by = ? WHERE user_id = ? AND revoked_at IS NULL",
                             (_iso(_now()), reason, by, user_id))
    return cur.rowcount


async def list_active(conn: aiosqlite.Connection, user_id: str) -> list[dict]:
    now = _iso(_now())
    async with conn.execute("SELECT session_id, auth_mode, created_at, last_seen_at, idle_expires_at, absolute_expires_at FROM sessions "
                            "WHERE user_id = ? AND revoked_at IS NULL AND idle_expires_at > ? AND absolute_expires_at > ? ORDER BY created_at",
                            (user_id, now, now)) as cur:
        return [dict(r) for r in await cur.fetchall()]
