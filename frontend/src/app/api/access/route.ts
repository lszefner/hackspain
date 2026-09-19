import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const GATE_COOKIE = "site-access";
const ACCESS_CODE = process.env.SITE_ACCESS_CODE ?? "albertito";
const MAX_AGE_SECONDS = 60 * 60 * 24 * 30;

export async function POST(request: NextRequest) {
  const form = await request.formData();
  const code = String(form.get("code") ?? "").trim();
  const next = String(form.get("next") ?? "/");
  const safeNext = next.startsWith("/") ? next : "/";

  const url = request.nextUrl.clone();

  if (code.toLowerCase() !== ACCESS_CODE.toLowerCase()) {
    url.pathname = "/access";
    url.search = "";
    url.searchParams.set("next", safeNext);
    url.searchParams.set("error", "1");
    return NextResponse.redirect(url);
  }

  url.pathname = safeNext;
  url.search = "";
  const response = NextResponse.redirect(url);
  response.cookies.set(GATE_COOKIE, "granted", {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: MAX_AGE_SECONDS,
  });
  return response;
}
