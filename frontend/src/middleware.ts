import { NextResponse, type NextRequest } from "next/server";

import { API_BASE_URL, SESSION_COOKIE, allowedRoles, homeFor, type Role } from "@/lib/auth";

// Server-side route guard. The backend validates the JWT; the frontend never decodes it itself.
export async function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const roles = allowedRoles(pathname);
  if (!roles) return NextResponse.next();

  const toLogin = () => {
    const url = new URL("/login", request.url);
    url.searchParams.set("next", pathname + search);
    const res = NextResponse.redirect(url);
    res.cookies.delete(SESSION_COOKIE);
    return res;
  };

  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return toLogin();

  let role: Role;
  try {
    const me = await fetch(`${API_BASE_URL}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!me.ok) return toLogin();
    role = (await me.json()).role;
  } catch {
    return toLogin();
  }

  if (!roles.includes(role)) {
    return NextResponse.redirect(new URL(homeFor(role), request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/intake/:path*", "/dashboard/:path*"],
};
