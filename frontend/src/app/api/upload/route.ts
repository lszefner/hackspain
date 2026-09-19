import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 30;

const MAX = 25 * 1024 * 1024;

/**
 * serve.py: inspect(). It never parsed the document -- it checks the bytes are
 * really what the extension claims and reports what came in. Same here, so the
 * answer is as true on Vercel as it is locally.
 */
export async function POST(request: NextRequest) {
  const name = (request.nextUrl.searchParams.get("name") ?? "file").split("/").pop() || "file";
  const blob = new Uint8Array(await request.arrayBuffer());
  if (blob.byteLength > MAX) return new NextResponse("too large", { status: 413 });

  const head = (n: number) => String.fromCharCode(...blob.slice(0, n));
  const low = name.toLowerCase();

  if (low.endsWith(".pdf")) {
    if (head(4) !== "%PDF") {
      return NextResponse.json({
        name, size: blob.byteLength, ok: false,
        reason: "not a PDF inside, whatever the name says", invoices: [],
      });
    }
    return NextResponse.json({
      name, size: blob.byteLength, ok: true, kind: "pdf", invoices: [name],
    });
  }
  if (low.endsWith(".zip")) {
    if (head(2) !== "PK") {
      return NextResponse.json({
        name, size: blob.byteLength, ok: false,
        reason: "the zip is damaged and will not open", invoices: [],
      });
    }
    // Reading the central directory needs the Python desk; say so plainly
    // rather than guess at what is inside.
    return NextResponse.json({
      name, size: blob.byteLength, ok: false,
      reason: "zips are opened by the desk that runs locally, not by this deployment",
      invoices: [],
    });
  }
  return NextResponse.json({
    name, size: blob.byteLength, ok: false,
    reason: "not a .pdf or a .zip", invoices: [],
  });
}
