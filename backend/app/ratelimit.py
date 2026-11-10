"""Server-side rate limiting for patient-code entry (docs/security/post-remediation-hardening.md A4; owner decision H4).

Every attempt to use a patient code or a binding code (lookup, link, bind) is counted against two keys: the
authenticated principal and the client's network source. Each key has a per-minute (burst) and a per-hour limit;
going over either starts a temporary block for that key. The request id plays no part, so rotating it changes
nothing. Counters live in SQLite (migration step 17), shared by every backend process that uses the same database
file and kept across restarts. This is NOT a multi-host limiter: several hosts need a shared store (e.g. Redis)
behind the same `Store` interface.

If the store cannot be used, attempts are refused (503), never let through unlimited. A refusal (429) says only to
wait; it never says whether a code or a patient exists.
"""

from __future__ import annotations

import hashlib
import hmac
import math
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol

import aiosqlite
from fastapi import Request

from app import audit
from app.auth import Principal
from app.config import get_settings
from app.database import transaction
from app.errors import ApiError

Scope = Literal["patient_code_entry", "login"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="microseconds")


def client_source(request: Request) -> str:
    """The client's address. X-Forwarded-For is believed only from a configured trusted proxy, and then the rightmost
    address that is not itself a trusted proxy is used (the one the trusted proxy actually saw)."""
    peer = request.client.host if request.client else "unknown"
    trusted = get_settings().trusted_proxies
    if peer not in trusted:
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        if hop not in trusted:
            return hop
    return peer


def _key(scope: str, kind: str, value: str) -> str:
    # HMAC so the table never holds a raw user id or IP address, and keys cannot be precomputed without the secret.
    return hmac.new(get_settings().jwt_secret_key.encode(), f"{scope}\n{kind}\n{value}".encode(), hashlib.sha256).hexdigest()


class Store(Protocol):
    async def hit(self, key: str, windows: list[tuple[int, int]], block_s: int) -> tuple[bool, int, bool]: ...


class SqliteStore:
    """Fixed windows in SQLite. `hit` counts one attempt and returns (allowed, retry_after_s, block_started)."""

    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    async def hit(self, key: str, windows: list[tuple[int, int]], block_s: int) -> tuple[bool, int, bool]:
        now = _now()
        async with transaction(self.conn):  # BEGIN IMMEDIATE: concurrent attempts are counted one at a time
            async with self.conn.execute("SELECT blocked_until FROM rate_limit_blocks WHERE bucket_key = ?", (key,)) as cur:
                b = await cur.fetchone()
            if b is not None and b["blocked_until"] > _iso(now):
                retry = math.ceil((datetime.fromisoformat(b["blocked_until"]) - now).total_seconds())
                return False, max(retry, 1), False
            over = False
            for seconds, limit in windows:
                start = datetime.fromtimestamp(now.timestamp() // seconds * seconds, timezone.utc)
                wkey = f"{key}:{seconds}"
                async with self.conn.execute(
                    "INSERT INTO rate_limit_counters (bucket_key, window_start, count) VALUES (?, ?, 1) "
                    "ON CONFLICT (bucket_key, window_start) DO UPDATE SET count = count + 1 RETURNING count", (wkey, _iso(start))) as cur:
                    count = (await cur.fetchone())[0]
                await self.conn.execute("DELETE FROM rate_limit_counters WHERE bucket_key = ? AND window_start < ?",
                                        (wkey, _iso(start - timedelta(seconds=seconds))))
                over = over or count > limit
            if not over:
                return True, 0, False
            until = now + timedelta(seconds=block_s)
            await self.conn.execute("INSERT INTO rate_limit_blocks (bucket_key, blocked_until) VALUES (?, ?) "
                                    "ON CONFLICT (bucket_key) DO UPDATE SET blocked_until = excluded.blocked_until", (key, _iso(until)))
            return False, block_s, True


def _refused(retry_after: int) -> ApiError:
    return ApiError(429, "RATE_LIMITED", "Too many attempts. Please wait before trying again.", headers={"Retry-After": str(retry_after)})


async def check_login(conn: aiosqlite.Connection, request: Request, store: Store | None = None) -> None:
    """Sign-in attempts per network source (there is no principal yet). Failed sign-ins are audited, so without this
    anyone could grow the audit log without bound. Generous limits; same fail-closed store."""
    s = get_settings()
    store = store or getattr(request.app.state, "rate_limit_store", None) or SqliteStore(conn)
    try:
        allowed, retry, started = await store.hit(_key("login", "source", client_source(request)),
                                                  [(60, s.rate_login_per_minute_source), (3600, s.rate_login_per_hour_source)], s.rate_block_minutes * 60)
    except Exception as exc:
        raise ApiError(503, "RATE_LIMIT_UNAVAILABLE", "This check is temporarily unavailable. Please try again shortly.") from exc
    if started:
        async with transaction(conn):
            await audit.record(conn, principal=None, action="rate_limited", outcome="denied", request_id=getattr(request.state, "request_id", None),
                               details=audit.RateLimitedDetails(scope="login", key_kind="source", blocked_minutes=s.rate_block_minutes))
    if not allowed:
        raise _refused(retry)


async def check_code_entry(conn: aiosqlite.Connection, request: Request, principal: Principal, store: Store | None = None) -> None:
    """Count one patient-code or binding-code attempt for this principal and this source; raise 429 when over a limit,
    503 when the limit cannot be checked. Call it before anything about the code is looked up."""
    s = get_settings()
    store = store or getattr(request.app.state, "rate_limit_store", None) or SqliteStore(conn)
    block_s = s.rate_block_minutes * 60
    checks = [
        ("principal", principal.user_id, [(60, s.rate_code_per_minute_principal), (3600, s.rate_code_per_hour_principal)]),
        ("source", client_source(request), [(60, s.rate_code_per_minute_source), (3600, s.rate_code_per_hour_source)]),
    ]
    refusal = None
    for kind, value, windows in checks:
        try:
            allowed, retry, started = await store.hit(_key("patient_code_entry", kind, value), windows, block_s)
        except ApiError:
            raise
        except Exception as exc:  # the store itself failed: fail closed
            raise ApiError(503, "RATE_LIMIT_UNAVAILABLE", "This check is temporarily unavailable. Please try again shortly.") from exc
        if started:
            async with transaction(conn):
                await audit.record(conn, principal=principal, action="rate_limited", outcome="denied",
                                   request_id=getattr(request.state, "request_id", None),
                                   details=audit.RateLimitedDetails(scope="patient_code_entry", key_kind=kind, blocked_minutes=s.rate_block_minutes))  # type: ignore[arg-type]
        if not allowed:
            refusal = refusal or _refused(retry)
    if refusal is not None:
        raise refusal
