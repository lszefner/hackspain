import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export async function POST(request: NextRequest) {
  return NextResponse.json({
    name: request.nextUrl.searchParams.get("name") ?? "file",
    ok: false,
    reason: "Upload processing is not connected. No file was saved or evaluated.",
    invoices: [],
  });
}
