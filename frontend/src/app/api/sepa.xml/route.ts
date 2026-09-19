import { NextResponse } from "next/server";
export function GET() {
  return NextResponse.json({ error: "No authorized payment run is recorded. A recommendation is not payment authorization." }, { status: 409 });
}
