import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { API_BASE_URL, SESSION_COOKIE, allowedRoles, homeFor, type Role } from "@/lib/auth";
import { FLOW_COOKIE, discover, oidcConfigFromEnv, sameSecret } from "@/lib/oidc";

function fail(request: Request, why: string) {
  // One generic message on the page; the reason code is for the URL only (no token, code or claim is ever shown).
  const res = NextResponse.redirect(new URL(`/login?error=${encodeURIComponent(why)}`, request.url));
  res.cookies.delete({ name: FLOW_COOKIE, path: "/api/auth/oidc" });
  return res;
}

// GET: the provider redirects back here. Check state, exchange the code with the PKCE verifier (server-side, with
// the client secret if this is a confidential client), then hand the ID token and nonce to the backend, which
// validates them and creates the server session. The session token goes into the same httpOnly cookie as demo login.
export async function GET(request: Request) {
  const cfg = oidcConfigFromEnv(process.env);
  if (!cfg) return fail(request, "not_configured");
  const params = new URL(request.url).searchParams;
  const raw = (await cookies()).get(FLOW_COOKIE)?.value;
  let flow: { state: string; nonce: string; verifier: string; next: string | null } | null = null;
  try {
    flow = raw ? JSON.parse(raw) : null;
  } catch {
    flow = null;
  }
  const code = params.get("code");
  if (!flow || !code || !sameSecret(params.get("state") ?? "", flow.state)) return fail(request, "state");

  const d = await discover(cfg);
  const form = new URLSearchParams({ grant_type: "authorization_code", code, redirect_uri: cfg.redirectUri, client_id: cfg.clientId, code_verifier: flow.verifier });
  if (cfg.clientSecret) form.set("client_secret", cfg.clientSecret);
  const tokenRes = await fetch(d.token_endpoint, { method: "POST", body: form, cache: "no-store", redirect: "error" }).catch(() => null);
  const tokens = tokenRes?.ok ? ((await tokenRes.json()) as { id_token?: string }) : null;
  if (!tokens?.id_token) return fail(request, "token_exchange");

  const backend = await fetch(`${API_BASE_URL}/auth/oidc/session`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id_token: tokens.id_token, nonce: flow.nonce }), cache: "no-store",
  });
  if (!backend.ok) return fail(request, "not_allowed");
  const session = (await backend.json()) as { access_token: string; expires_in: number; role: Role };

  // Honour ?next= only if this role may open it (same rule as demo login); otherwise the role's home.
  const target = flow.next && allowedRoles(flow.next)?.includes(session.role) ? flow.next : homeFor(session.role);
  const res = NextResponse.redirect(new URL(target, request.url));
  res.cookies.set(SESSION_COOKIE, session.access_token, {
    httpOnly: true, sameSite: "lax", secure: process.env.NODE_ENV === "production", path: "/", maxAge: session.expires_in,
  });
  res.cookies.delete({ name: FLOW_COOKIE, path: "/api/auth/oidc" });
  return res;
}
