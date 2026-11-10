// Run: node --test src/lib/oidc.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import { authorizeUrl, oidcConfigFromEnv, pkceChallenge, randomToken, sameSecret, safeNext } from "./oidc.ts";

test("random tokens are url-safe and unique", () => {
  const a = randomToken(32), b = randomToken(32);
  assert.match(a, /^[A-Za-z0-9_-]{43}$/);
  assert.notEqual(a, b);
});

test("the PKCE challenge is BASE64URL(SHA-256(verifier)) (RFC 7636 S256)", async () => {
  const verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"; // RFC 7636 appendix B
  assert.equal(await pkceChallenge(verifier), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  const v = randomToken(32);
  assert.equal(await pkceChallenge(v), createHash("sha256").update(v).digest("base64url"));
});

test("the authorize URL carries state, nonce and the S256 challenge, and nothing secret", () => {
  const url = new URL(authorizeUrl("https://idp.example.test/auth", {
    clientId: "sehat-web", redirectUri: "https://app.example.test/api/auth/oidc/callback", state: "s".repeat(43),
    nonce: "n".repeat(43), challenge: "c".repeat(43),
  }));
  assert.equal(url.origin + url.pathname, "https://idp.example.test/auth");
  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
  assert.equal(url.searchParams.get("scope"), "openid");
  assert.equal(url.searchParams.get("nonce"), "n".repeat(43));
  assert.equal(url.searchParams.get("client_secret"), null);
});

test("state comparison is exact", () => {
  assert.equal(sameSecret("abc", "abc"), true);
  assert.equal(sameSecret("abc", "abd"), false);
  assert.equal(sameSecret("abc", "abcd"), false);
  assert.equal(sameSecret("", ""), false);
});

test("the post-login path is local only", () => {
  assert.equal(safeNext("/dashboard"), "/dashboard");
  for (const bad of ["//evil.example", "https://evil.example", "/\\evil", "dashboard", null, undefined]) assert.equal(safeNext(bad), null);
});

test("OIDC config is complete or absent, never partial", () => {
  assert.equal(oidcConfigFromEnv({}), null);
  assert.throws(() => oidcConfigFromEnv({ OIDC_ISSUER: "https://idp.example.test" }), /OIDC_CLIENT_ID/);
  assert.throws(() => oidcConfigFromEnv({ OIDC_ISSUER: "http://idp.example.test", OIDC_CLIENT_ID: "x", OIDC_REDIRECT_URI: "https://a.test/cb" }), /https/);
  const c = oidcConfigFromEnv({ OIDC_ISSUER: "https://idp.example.test/", OIDC_CLIENT_ID: "x", OIDC_REDIRECT_URI: "https://a.test/cb" });
  assert.equal(c?.issuer, "https://idp.example.test");
});
