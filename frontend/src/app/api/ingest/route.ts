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

  const file = form.get("file");
  if (!(file instanceof File)) {
    return NextResponse.json({ ok: false, error: "missing_file" }, { status: 400 });
  }

  const fileId = safePdfFileName(file.name);
  if (!fileId) {
    return NextResponse.json(
      { ok: false, error: "invalid_pdf_name" },
      { status: 400 },
    );
  }
  if (file.size <= 0 || file.size > MAX_BYTES) {
    return NextResponse.json(
      { ok: false, error: "file_too_large" },
      { status: 413 },
    );
  }

  const bytes = new Uint8Array(await file.arrayBuffer());
  if (!looksLikePdf(bytes)) {
    return NextResponse.json({ ok: false, error: "not_a_pdf" }, { status: 400 });
  }

  const dir = revisionInputDir();
  const dest = path.join(dir, fileId);
  try {
    await mkdir(dir, { recursive: true });
    await writeFile(dest, bytes);
  } catch {
    return NextResponse.json(
      { ok: false, error: "stage_failed" },
      { status: 500 },
    );
  }

  const requestKey =
    typeof form.get("request_key") === "string" &&
    String(form.get("request_key")).trim().length > 0
      ? String(form.get("request_key")).trim().slice(0, 200)
      : `agent-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

  const base = backendBase();
  try {
    const res = await fetch(`${base}/api/lanzar`, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        request_key: requestKey,
        objetivo: "una",
        file_id: fileId,
      }),
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
          staged: true,
          procesando: Boolean(body.procesando),
        },
        { status: 409 },
      );
    }

    return NextResponse.json({
      ok: true,
      file_id: fileId,
      request_key: requestKey,
      procesando: body.procesando ?? true,
    });
  } catch {
    return NextResponse.json(
      {
        ok: false,
        error: "backend_unavailable",
        file_id: fileId,
        staged: true,
      },
      { status: 502 },
    );
  }
}
