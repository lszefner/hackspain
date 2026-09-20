import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { unzipSync } from "fflate";
import { NextResponse } from "next/server";
import {
  looksLikePdf,
  revisionInputDir,
  safePdfFileName,
} from "@/lib/desk/ingest";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const MAX_BYTES = 100 * 1024 * 1024;
const MAX_PDFS = 80;

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

  return NextResponse.json({
    ok: true,
    files: staged,
    staged: staged.length,
    skipped,
    zip_name: path.basename(file.name),
    note: "PDFs were staged on disk. This endpoint does not launch engine runs for the batch.",
  });
}
