import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { buildDossierPanel } from "@/lib/desk/panels";
import { actionClass } from "@/lib/desk/types";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const file = request.nextUrl.searchParams.get("file")?.trim();
  if (!file || file !== file.replace(/^.*[/\\]/, "")) {
    return NextResponse.json({ error: "invalid_file" }, { status: 400 });
  }

  try {
    const built = await buildDossierPanel(file);
    if (!built) {
      return NextResponse.json({ error: "not_found" }, { status: 404 });
    }
    const verdict = String(built.facts.verdict || "ESCALAR");
    const amount =
      typeof built.facts.amount === "string" ? built.facts.amount : "amount not recorded";
    const vendor =
      typeof built.facts.vendor === "string" ? built.facts.vendor : "Supplier not recorded";
    const said = `I finished ${file}. ${vendor} · ${amount}. Recommendation: ${actionClass(verdict)}. Approval and payment are not connected.`;
    return NextResponse.json(
      { said, panels: [built.panel], facts: built.facts },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json(
      { error: "Backend unavailable. Check the API connection and retry." },
      { status: 503 },
    );
  }
}
