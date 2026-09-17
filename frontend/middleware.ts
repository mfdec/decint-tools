import { NextResponse, type NextRequest } from "next/server";

/**
 * Gate the console at the edge: without a session cookie the /console page is
 * never served, so there's no flash of the tool UI before a client-side check
 * runs. This only tests for the cookie's presence — the backend still verifies
 * the signature on every /api/v1 call, so a forged cookie gets you an empty
 * shell and 401s, not data.
 */
const COOKIE = "decint_session";

export function middleware(req: NextRequest) {
  if (req.cookies.get(COOKIE)) return NextResponse.next();

  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = `?next=${encodeURIComponent(req.nextUrl.pathname)}`;
  return NextResponse.redirect(url);
}

export const config = {
  // /billing needs the same treatment: it shows what an account has paid for,
  // and the processor return pages land inside it.
  matcher: ["/console/:path*", "/billing/:path*"],
};
