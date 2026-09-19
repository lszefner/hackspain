import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const GATE_COOKIE = "site-access";

export function proxy(request: NextRequest) {
  const hasAccess = request.cookies.get(GATE_COOKIE)?.value === "granted";
  if (hasAccess) {
    return NextResponse.next();
  }

  const url = request.nextUrl.clone();
  url.pathname = "/access";
  url.search = "";
  url.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!access|api/access|_next/static|_next/image|favicon.ico).*)"],
};
