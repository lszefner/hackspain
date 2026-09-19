import snapshot from "@/data/snapshot.json";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export type Estado = "pendiente" | "procesando" | "hecha" | "error";
export type Decision = "PAGAR" | "ESCALAR" | "NO_PAGAR" | null;

export interface FacturaResumen {
  file_id: string;
  estado: Estado;
  decision: Decision;
}

export interface Resumen {
  total: number;
  conteo: Record<Estado, number>;
  decisiones: Record<"PAGAR" | "ESCALAR" | "NO_PAGAR", number>;
  facturas: FacturaResumen[];
  lote_tamano: number;
  procesando: boolean;
  error: string | null;
}

export interface Check {
  rule_id: string | null;
  canonical: string;
  verdict: "PASS" | "FAIL" | "NEEDS_REVIEW";
  reason: string;
  on_fail: string | null;
}

export interface FacturaDetalle {
  file_id: string;
  estado: Estado;
  decision: Decision;
  checks: Check[];
  campos: Record<string, unknown>;
  error: string | null;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...init });
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

// Snapshot fallback: when there's no reachable backend (e.g. the Vercel
// deployment, which has no live pipeline behind it), fall back to a static
// export of the last local run instead of surfacing "offline". Real local
// dev against `python3 backend/server.py` always wins when it's reachable.
const SNAPSHOT_RESUMEN = snapshot.resumen as Resumen;
const SNAPSHOT_FACTURAS = snapshot.facturas as Record<string, FacturaDetalle>;

export const getResumen = (): Promise<Resumen> =>
  api<Resumen>("/api/resumen").catch(() => SNAPSHOT_RESUMEN);

export const getFactura = (fileId: string): Promise<FacturaDetalle> =>
  api<FacturaDetalle>(`/api/factura/${encodeURIComponent(fileId)}`).catch(
    () =>
      SNAPSHOT_FACTURAS[fileId] ?? {
        file_id: fileId,
        estado: "pendiente",
        decision: null,
        checks: [],
        campos: {},
        error: null,
      }
  );

// No live backend to launch a revision against in snapshot mode -- resolve
// as a no-op instead of throwing (offline UI is intentionally not shown for
// getResumen/getFactura, so it shouldn't appear here either).
export const lanzarLote = () =>
  api<{ ok: boolean; procesando: boolean }>("/api/lanzar", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: "objetivo=lote",
  }).catch(() => ({ ok: false, procesando: false }));

export const lanzarUna = (fileId: string) =>
  api<{ ok: boolean; procesando: boolean }>("/api/lanzar", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: `objetivo=una&file_id=${encodeURIComponent(fileId)}`,
  }).catch(() => ({ ok: false, procesando: false }));
