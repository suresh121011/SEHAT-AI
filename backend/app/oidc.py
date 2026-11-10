"""OpenID Connect ID-token validation (docs/security/post-remediation-hardening.md A1; owner decision H1: provider-neutral).

The frontend (backend-for-frontend) runs the authorization-code flow with PKCE and posts the ID token here exactly
once, with the nonce it issued. This module checks it against the provider's published keys and turns it into an
`Identity`; app.routes.auth then creates a server session (app.sessions). Nothing here stores or logs a token.

Checked: signature against the provider's JWKS (asymmetric algorithms from an allowlist only; never `none` or HS*),
`iss` exactly the configured issuer, `aud` containing the configured client id (and `azp` equal to it when there are
several audiences), `exp`/`iat`/`nbf` with a bounded clock skew, `sub`, and `nonce` equal to the one the frontend
issued. Role and facilities come from configured claims the provider asserts; anything unexpected is refused.
"""

from __future__ import annotations

import hmac
import re
import uuid
from dataclasses import dataclass

import httpx
import jwt
from jwt import PyJWK, PyJWKClient, PyJWKSet

from app.auth import Role
from app.config import Settings

_NS = uuid.UUID("5e4a7a10-0000-4000-8000-5e4a7a1a0002")
_FACILITY = re.compile(r"^[A-Z0-9-]{3,32}$")  # same pattern as CaseCreate.facility_code


class OidcError(Exception):
    def __init__(self, reason: str):  # "invalid_token" | "identity_not_allowed" (audit reason codes; never claim values)
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    user_id: str
    role: Role
    facilities: frozenset[str] | None  # None = no facility restriction: supervisor/admin "*", or a patient (ownership-limited)


def user_id_for(issuer: str, subject: str) -> str:
    """Stable internal id for one provider identity: the pair (issuer, subject), never an email or a name."""
    return str(uuid.uuid5(_NS, f"{issuer}\n{subject}"))


class Validator:
    def __init__(self, settings: Settings, jwks: dict | None = None):
        """`jwks` is injected only by tests (a local test issuer); otherwise keys come from the provider."""
        self.s = settings
        self._jwks = PyJWKSet.from_dict(jwks) if jwks is not None else None
        self._client: PyJWKClient | None = None

    def _jwks_url(self) -> str:
        if self.s.oidc_jwks_url:
            return self.s.oidc_jwks_url
        r = httpx.get(f"{self.s.oidc_issuer}/.well-known/openid-configuration", timeout=5.0, follow_redirects=False)
        r.raise_for_status()
        doc = r.json()
        if doc.get("issuer", "").rstrip("/") != self.s.oidc_issuer or not str(doc.get("jwks_uri", "")).startswith("https://"):
            raise OidcError("invalid_token")  # a discovery document for another issuer, or keys over plain http
        return doc["jwks_uri"]

    def _key(self, token: str, kid: str | None) -> PyJWK:
        if self._jwks is not None:
            for k in self._jwks.keys:
                if k.key_id == kid:
                    return k
            raise OidcError("invalid_token")
        if self._client is None:
            self._client = PyJWKClient(self._jwks_url(), cache_keys=True, timeout=5)
        return self._client.get_signing_key_from_jwt(token)

    def validate(self, id_token: str, nonce: str) -> Identity:
        s = self.s
        try:
            header = jwt.get_unverified_header(id_token)
            if header.get("alg") not in s.oidc_allowed_algs:
                raise OidcError("invalid_token")
            key = self._key(id_token, header.get("kid"))
            claims = jwt.decode(
                id_token, key, algorithms=list(s.oidc_allowed_algs), audience=s.oidc_client_id, issuer=s.oidc_issuer,
                leeway=s.oidc_clock_skew_s, options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
            )
        except OidcError:
            raise
        except (jwt.PyJWTError, httpx.HTTPError, ValueError, KeyError) as exc:
            raise OidcError("invalid_token") from exc
        aud = claims["aud"]
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != s.oidc_client_id:
            raise OidcError("invalid_token")
        if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce):
            raise OidcError("invalid_token")
        subject = claims["sub"]
        if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
            raise OidcError("invalid_token")
        return Identity(s.oidc_issuer, subject, user_id_for(s.oidc_issuer, subject), *self._role_and_scope(claims))

    def _role_and_scope(self, claims: dict) -> tuple[Role, frozenset[str] | None]:
        raw_role = claims.get(self.s.oidc_role_claim)
        try:
            role = Role(raw_role)
        except ValueError as exc:
            raise OidcError("identity_not_allowed") from exc
        raw = claims.get(self.s.oidc_facility_claim)
        if role == Role.PATIENT:
            # A patient's access is limited by ownership (cases they created, and in M-C cases linked to their confirmed
            # identity binding), never by facility; a facility claim is ignored rather than trusted to change anything.
            return role, None
        if raw == "*" or raw == ["*"]:
            if role not in (Role.SUPERVISOR, Role.ADMIN):
                raise OidcError("identity_not_allowed")
            return role, None
        if not isinstance(raw, list) or not raw or any(not isinstance(c, str) or not _FACILITY.fullmatch(c) for c in raw):
            raise OidcError("identity_not_allowed")  # staff must carry an explicit, well-formed facility list
        return role, frozenset(raw)
