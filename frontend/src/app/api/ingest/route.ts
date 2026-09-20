import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";
import { backendBase } from "@/lib/desk/backend";
import {
  looksLikePdf,
  revisionInputDir,
  safePdfFileName,
} from "@/lib/desk/ingest";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const MAX_BYTES = 25 * 1024 * 1024;

export async function POST(request: Request) {
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json({ ok: false, error: "invalid_form" }, { status: 400 });
  }

  // A drop of N PDFs becomes ONE engine run. Launching one run per file made
  // the engine recapture the workbook, the supplier/order masters, the ERP
  // snapshot and the processed history N times, and the backend's single-run
  // guard rejected every file after the first as engine_busy.
  const files = form.getAll("file").filter((f): f is File => f instanceof File);
  if (files.length === 0) {
    return NextResponse.json({ ok: false, error: "missing_file" }, { status: 400 });
  }

  const dir = revisionInputDir();
  const fileIds: string[] = [];
  for (const file of files) {
    const fileId = safePdfFileName(file.name);
    if (!fileId) {
      return NextResponse.json(
        { ok: false, error: "invalid_pdf_name", file_name: file.name },
        { status: 400 },
      );
    }
    if (file.size <= 0 || file.size > MAX_BYTES) {
      return NextResponse.json(
        { ok: false, error: "file_too_large", file_id: fileId },
        { status: 413 },
      );
    }
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (!looksLikePdf(bytes)) {
      return NextResponse.json(
        { ok: false, error: "not_a_pdf", file_id: fileId },
        { status: 400 },
      );
    }
    try {
      await mkdir(dir, { recursive: true });
      await writeFile(path.join(dir, fileId), bytes);
    } catch {
      return NextResponse.json(
        { ok: false, error: "stage_failed", file_id: fileId },
        { status: 500 },
      );
    }
    if (!fileIds.includes(fileId)) fileIds.push(fileId);
  }

  const requestKey =
    typeof form.get("request_key") === "string" &&
    String(form.get("request_key")).trim().length > 0
      ? String(form.get("request_key")).trim().slice(0, 200)
      : `agent-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

  // Kept for callers that still read a single id from the response.
  const fileId = fileIds[0];
  const body_ = new URLSearchParams({ request_key: requestKey });
  if (fileIds.length === 1) {
    body_.set("objetivo", "una");
    body_.set("file_id", fileId);
  } else {
    body_.set("objetivo", "varias");
    for (const id of fileIds) body_.append("file_id", id);
  }

  const base = backendBase();
  try {
    const res = await fetch(`${base}/api/lanzar`, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body_,
      signal: AbortSignal.timeout(30_000),
    });
    const body = (await res.json().catch(() => ({}))) as {
      ok?: boolean;
      procesando?: boolean;
      error?: string;
    };

    if (!res.ok) {
      return NextResponse.json(
        {
          ok: false,
          error: body.error || "lanzar_failed",
          file_id: fileId,
          file_ids: fileIds,
          staged: true,
        },
        { status: res.status },
      );
    }

    if (!body.ok) {
      return NextResponse.json(
        {
          ok: false,
          error: body.procesando ? "engine_busy" : "lanzar_rejected",
          file_id: fileId,
          file_ids: fileIds,
          staged: true,
          procesando: Boolean(body.procesando),
        },
        { status: 409 },
      );
    }

    return NextResponse.json({
      ok: true,
      file_id: fileId,
      file_ids: fileIds,
      request_key: requestKey,
      procesando: body.procesando ?? true,
    });
  } catch {
    return NextResponse.json(
      {
        ok: false,
        error: "backend_unavailable",
        file_id: fileId,
        file_ids: fileIds,
        staged: true,
      },
      { status: 502 },
    );
  }
}
