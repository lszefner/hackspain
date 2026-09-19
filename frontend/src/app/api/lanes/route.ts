import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { lanes } from "@/lib/desk/fold";

export function GET(request: NextRequest) {
  const q = request.nextUrl.searchParams;
  return NextResponse.json(lanes(q.get("q") ?? "", q.get("action") ?? ""));
}
