import { NextResponse } from "next/server";
import { deskQuery } from "@/lib/desk/backend";
import { classifyOutreach, type OutreachDraft } from "@/lib/desk/outreach";
import type { Detail, Invoices } from "@/lib/desk/types";

export const dynamic = "force-dynamic";

async function loadDetail(fileId: string): Promise<Detail | null> {
  try {
    return (await deskQuery(
      "invoice",
      new URLSearchParams({ file: fileId }),
    )) as Detail;
  } catch {
    return null;
  }
}

/** Single-file draft for the editable popup. */
export async function GET(request: Request) {
  const file = new URL(request.url).searchParams.get("file")?.trim();
  if (!file || file !== file.replace(/^.*[/\\]/, "")) {
    return NextResponse.json({ error: "invalid_file" }, { status: 400 });
  }
  const detail = await loadDetail(file);
  if (!detail) {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  const draft = classifyOutreach(detail);
  if (!draft) {
    return NextResponse.json({
      needed: false,
      file_id: file,
      reason:
        "Nothing on this invoice looks like a supplier message would fix it. I will not invent an email.",
    });
  }
  return NextResponse.json({ needed: true, draft });
}

/**
 * Propose outreach for one file, an explicit list, or a scan of escalated invoices.
 * Body: { file?: string, files?: string[], limit?: number }
 */
export async function POST(request: Request) {
  let body: { file?: string; files?: string[]; limit?: number } = {};
  try {
    body = (await request.json()) as typeof body;
  } catch {
    body = {};
  }

  const drafts: OutreachDraft[] = [];
  const skipped: { file_id: string; reason: string }[] = [];

  let candidates: string[] = [];
  if (typeof body.file === "string" && body.file) {
    candidates = [body.file.replace(/^.*[/\\]/, "")];
  } else if (Array.isArray(body.files) && body.files.length) {
    candidates = body.files
      .map((f) => String(f).replace(/^.*[/\\]/, ""))
      .filter(Boolean)
      .slice(0, 40);
  } else {
    try {
      const limit = Math.min(Math.max(Number(body.limit) || 25, 1), 40);
      const list = (await deskQuery(
        "invoices",
        new URLSearchParams({
          action: "ESCALAR",
          limit: String(limit),
          page: "1",
        }),
      )) as Invoices;
      candidates = list.rows.map((r) => r.file_id);
    } catch {
      return NextResponse.json(
        { error: "Backend unavailable. Check the API connection and retry." },
        { status: 503 },
      );
    }
  }

  for (const fileId of candidates) {
    const detail = await loadDetail(fileId);
    if (!detail) {
      skipped.push({ file_id: fileId, reason: "not_found" });
      continue;
    }
    const draft = classifyOutreach(detail);
    if (!draft) {
      skipped.push({
        file_id: fileId,
        reason: "no_supplier_actionable_issue",
      });
      continue;
    }
    drafts.push(draft);
  }

  return NextResponse.json({
    drafts,
    skipped,
    demo: true,
    note: "Email drafts ready for review.",
  });
}
