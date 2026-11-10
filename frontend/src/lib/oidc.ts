// OIDC backend-for-frontend helpers (docs/security/post-remediation-hardening.md A1). Server-side only: the browser
// never sees the provider's tokens, the PKCE verifier or the client secret. No React, no DOM; unit-tested with
// `node --test`.

export type OidcConfig = { issuer: string; clientId: string; clientSecret: string | null; redirectUri: string };

function httpsOrLoopback(name: string, value: string): string {
  const u = new URL(value);
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(u.hostname);
  if (u.protocol !== "https:" && !(u.protocol === "http:" && loopback)) throw new Error(`${name} must be an https URL (http only for localhost)`);
  return value.replace(/\/+$/, "");
}

/** Complete OIDC settings, or null when OIDC is not configured. A partial configuration is an error, never a fallback. */
export function oidcConfigFromEnv(env: Record<string, string | undefined>): OidcConfig | null {
  const issuer = env.OIDC_ISSUER?.trim(), clientId = env.OIDC_CLIENT_ID?.trim(), redirect = env.OIDC_REDIRECT_URI?.trim();
  if (!issuer && !clientId && !redirect) return null;
  if (!issuer) throw new Error("OIDC_ISSUER is required");
  if (!clientId) throw new Error("OIDC_CLIENT_ID is required");
  if (!redirect) throw new Error("OIDC_REDIRECT_URI is required");
  return {
    issuer: httpsOrLoopback("OIDC_ISSUER", issuer), clientId, clientSecret: env.OIDC_CLIENT_SECRET?.trim() || null,
    redirectUri: httpsOrLoopback("OIDC_REDIRECT_URI", redirect),
  };
}

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function randomToken(bytes = 32): string {
  return base64url(crypto.getRandomValues(new Uint8Array(bytes)));
}

export async function pkceChallenge(verifier: string): Promise<string> {
  return base64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
}

export function authorizeUrl(endpoint: string, p: { clientId: string; redirectUri: string; state: string; nonce: string; challenge: string }): string {
  const url = new URL(endpoint);
  url.search = new URLSearchParams({
    response_type: "code", client_id: p.clientId, redirect_uri: p.redirectUri, scope: "openid",
    state: p.state, nonce: p.nonce, code_challenge: p.challenge, code_challenge_method: "S256",
  }).toString();
  return url.toString();
}

/** Exact comparison of two secrets without an early exit on the first differing character. */
export function sameSecret(a: string, b: string): boolean {
  if (!a || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** A same-site path to return to after sign-in, or null. */
export function safeNext(next: string | null | undefined): string | null {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") ? next : null;
}

export type Discovery = { issuer: string; authorization_endpoint: string; token_endpoint: string };

export async function discover(cfg: OidcConfig): Promise<Discovery> {
  const res = await fetch(`${cfg.issuer}/.well-known/openid-configuration`, { cache: "no-store", redirect: "error" });
  if (!res.ok) throw new Error("OIDC discovery failed");
  const d = (await res.json()) as Discovery;
  if (d.issuer?.replace(/\/+$/, "") !== cfg.issuer) throw new Error("OIDC discovery issuer mismatch");
  httpsOrLoopback("authorization_endpoint", d.authorization_endpoint);
  httpsOrLoopback("token_endpoint", d.token_endpoint);
  return d;
}

export const FLOW_COOKIE = "sehat_oidc_flow";
