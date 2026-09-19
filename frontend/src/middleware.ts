import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const GATE_COOKIE = "site-access";
const PUBLIC_PATHS = ["/access", "/api/access"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const isPublic =
    PUBLIC_PATHS.some((path) => pathname.startsWith(path)) ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/favicon.ico");

  if (isPublic) {
    return NextResponse.next();
  }

  const hasAccess = request.cookies.get(GATE_COOKIE)?.value === "granted";
  if (hasAccess) {
    return NextResponse.next();
  }

  const url = request.nextUrl.clone();
  url.pathname = "/access";
  url.search = "";
  url.searchParams.set("next", pathname);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
