import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import dossiers from "@/desk-data/dossiers.json";

const KNOWN = new Set(Object.keys(dossiers as Record<string, unknown>));

/**
 * The documents are static files under public/desk/facturas. This only checks
 * the name is one of the 500 we exported -- so a crafted ?file= cannot reach
 * anything else -- and hands the reader to the CDN.
 */
export function GET(request: NextRequest) {
  const name = (request.nextUrl.searchParams.get("file") ?? "").split("/").pop() ?? "";
  if (!KNOWN.has(name)) return new NextResponse("not found", { status: 404 });
  return NextResponse.redirect(
    new URL(`/desk/facturas/${encodeURIComponent(name)}`, request.nextUrl.origin),
  );
}
