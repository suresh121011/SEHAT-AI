import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { API_BASE_URL, SESSION_COOKIE, homeFor } from "@/lib/auth";

// POST: log in via the backend and keep the JWT in an httpOnly cookie (never exposed to JS).
export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const backend = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: body?.username, role: body?.role }),
    cache: "no-store",
  });
  const data = await backend.json();
  if (!backend.ok) return NextResponse.json(data, { status: backend.status });

  const res = NextResponse.json({ username: data.username, role: data.role, home: homeFor(data.role) });
  res.cookies.set(SESSION_COOKIE, data.access_token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: data.expires_in,
  });
  return res;
}

// DELETE: log out. Revokes the server-side session first, so the token stops working everywhere (not only in this
// browser), then clears the cookie. The cookie is cleared even if the backend is unreachable.
export async function DELETE() {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  let revoked = false;
  if (token) {
    const backend = await fetch(`${API_BASE_URL}/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    }).catch(() => null);
    revoked = backend?.ok ?? false;
  }
  const res = NextResponse.json({ ok: true, revoked });
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
