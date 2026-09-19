import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import dossiers from "@/desk-data/dossiers.json";

const BY_FILE = dossiers as Record<string, unknown>;

export function GET(request: NextRequest) {
  // basename, as the Python server does: the key is a filename, never a path
  const asked = (request.nextUrl.searchParams.get("file") ?? "").split("/").pop() ?? "";
  const found = BY_FILE[asked];
  if (!found) return new NextResponse("not found", { status: 404 });
  return NextResponse.json(found);
}
