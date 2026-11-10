import { NextResponse } from "next/server";

import { FLOW_COOKIE, authorizeUrl, discover, oidcConfigFromEnv, pkceChallenge, randomToken, safeNext } from "@/lib/oidc";

// GET: begin sign-in with the identity provider (authorization code + PKCE). State, nonce and the PKCE verifier stay
// in a short-lived httpOnly cookie scoped to the OIDC routes; the browser only ever sees the provider's login page.
export async function GET(request: Request) {
  const cfg = oidcConfigFromEnv(process.env);
  if (!cfg) return NextResponse.json({ error: { code: "FEATURE_DISABLED", message: "Sign-in with an identity provider is not configured" } }, { status: 404 });
  const d = await discover(cfg);
  const state = randomToken(), nonce = randomToken(), verifier = randomToken(48);
  const next = safeNext(new URL(request.url).searchParams.get("next"));
  const res = NextResponse.redirect(authorizeUrl(d.authorization_endpoint, {
    clientId: cfg.clientId, redirectUri: cfg.redirectUri, state, nonce, challenge: await pkceChallenge(verifier),
  }));
  res.cookies.set(FLOW_COOKIE, JSON.stringify({ state, nonce, verifier, next }), {
    httpOnly: true, sameSite: "lax", secure: process.env.NODE_ENV === "production", path: "/api/auth/oidc", maxAge: 600,
  });
  return res;
}
