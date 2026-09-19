import path from "node:path";

/** Stage the PDF where `backend.run_revision` reads originals before Supabase ingest. */
export function revisionInputDir(): string {
  if (process.env.REVISION_INPUT_DIR?.trim()) {
    return path.resolve(process.env.REVISION_INPUT_DIR.trim());
  }
  // Next runs with cwd = frontend/
  return path.resolve(process.cwd(), "..", "caja", "facturas");
}

export function safePdfFileName(name: string): string | null {
  const base = path.basename(name).replace(/[^\w.\-+() ]+/g, "_");
  if (!base.toLowerCase().endsWith(".pdf")) return null;
  if (base.length < 5 || base.includes("..")) return null;
  return base;
}

export function looksLikePdf(bytes: Uint8Array): boolean {
  if (bytes.length < 5) return false;
  return (
    bytes[0] === 0x25 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x44 &&
    bytes[3] === 0x46 &&
    bytes[4] === 0x2d
  );
}

