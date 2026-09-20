import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { unzipSync } from "fflate";
import { NextResponse } from "next/server";
import { backendBase } from "@/lib/desk/backend";
import {
  looksLikePdf,
  revisionInputDir,
  safePdfFileName,
} from "@/lib/desk/ingest";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const MAX_BYTES = 100 * 1024 * 1024;
const MAX_PDFS = 80;
// backend/server.py caps one run at LOTE_TAMANO files.
const MAX_PER_RUN = 20;

function isZipName(name: string) {
  return name.toLowerCase().endsWith(".zip");
}

function shouldSkipZipEntry(entryPath: string) {
  const normalized = entryPath.replace(/\\/g, "/");
  if (!normalized || normalized.endsWith("/")) return true;
  if (normalized.includes("..")) return true;
  if (normalized.startsWith("__MACOSX/") || normalized.includes("/__MACOSX/"))
    return true;
  if (path.basename(normalized).startsWith("._")) return true;
  return false;
}

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
  if (!isZipName(file.name) && file.type !== "application/zip") {
    return NextResponse.json({ ok: false, error: "not_a_zip" }, { status: 400 });
  }
  if (file.size <= 0 || file.size > MAX_BYTES) {
    return NextResponse.json(
      { ok: false, error: "file_too_large" },
      { status: 413 },
    );
  }

  const zipBytes = new Uint8Array(await file.arrayBuffer());
  let entries: Record<string, Uint8Array>;
  try {
    entries = unzipSync(zipBytes);
  } catch {
    return NextResponse.json({ ok: false, error: "invalid_zip" }, { status: 400 });
  }

  const dir = revisionInputDir();
  try {
    await mkdir(dir, { recursive: true });
  } catch {
    return NextResponse.json({ ok: false, error: "stage_failed" }, { status: 500 });
  }

  const staged: { file_id: string; bytes: number }[] = [];
  const usedNames = new Set<string>();
  let skipped = 0;

  for (const [entryPath, data] of Object.entries(entries)) {
    if (shouldSkipZipEntry(entryPath)) {
      skipped += 1;
      continue;
    }
    const fileId = safePdfFileName(path.basename(entryPath));
    if (!fileId) {
      skipped += 1;
      continue;
    }
    if (!looksLikePdf(data)) {
      skipped += 1;
      continue;
    }
    if (staged.length >= MAX_PDFS) {
      skipped += 1;
      continue;
    }

    let unique = fileId;
    if (usedNames.has(unique.toLowerCase())) {
      const stem = fileId.slice(0, -4);
      let n = 2;
      while (usedNames.has(`${stem}-${n}.pdf`.toLowerCase())) n += 1;
      unique = `${stem}-${n}.pdf`;
    }
    usedNames.add(unique.toLowerCase());

    try {
      await writeFile(path.join(dir, unique), data);
      staged.push({ file_id: unique, bytes: data.byteLength });
    } catch {
      skipped += 1;
    }
  }

  if (!staged.length) {
    return NextResponse.json(
      {
        ok: false,
        error: "no_pdfs_in_zip",
        files: [],
        staged: 0,
        skipped,
        zip_name: file.name,
      },
      { status: 400 },
    );
  }

  // Staging without launching left the PDFs on disk and the batch unprocessed.
  // Launch is opt-in so the old staging-only behaviour stays available, and one
  // run covers up to MAX_PER_RUN files instead of one run per file.
  const wantsLaunch = String(form.get("launch") ?? "") === "1";
  if (!wantsLaunch || staged.length === 0) {
    return NextResponse.json({
      ok: true,
      files: staged,
      staged: staged.length,
      skipped,
      launched: 0,
      pending_launch: staged.length,
      zip_name: path.basename(file.name),
      note: "PDFs were staged on disk. Pass launch=1 to start an engine run for them.",
    });
  }

  const launching = staged.slice(0, MAX_PER_RUN).map((entry) => entry.file_id);
  const requestKey =
    typeof form.get("request_key") === "string" &&
    String(form.get("request_key")).trim().length > 0
      ? String(form.get("request_key")).trim().slice(0, 200)
      : `zip-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const body = new URLSearchParams({ request_key: requestKey, objetivo: "varias" });
  for (const id of launching) body.append("file_id", id);

  let launched = 0;
  let launchError: string | null = null;
  try {
    const res = await fetch(`${backendBase()}/api/lanzar`, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
      signal: AbortSignal.timeout(30_000),
    });
    const reply = (await res.json().catch(() => ({}))) as {
      ok?: boolean;
      procesando?: boolean;
      error?: string;
    };
    if (res.ok && reply.ok) launched = launching.length;
    else launchError = reply.procesando ? "engine_busy" : reply.error || "lanzar_rejected";
  } catch {
    launchError = "backend_unavailable";
  }

  return NextResponse.json({
    ok: true,
    files: staged,
    staged: staged.length,
    skipped,
    launched,
    // Every staged file the run did not take. They stay on disk; a further
    // launch picks them up. Never report them as processed.
    pending_launch: staged.length - launched,
    request_key: launched > 0 ? requestKey : undefined,
    launch_error: launchError ?? undefined,
    zip_name: path.basename(file.name),
    note:
      launched > 0
        ? `Launched one engine run for ${launched} PDF(s); ${staged.length - launched} staged and awaiting a further run.`
        : "PDFs were staged on disk; the engine run was not started.",
  });
}
