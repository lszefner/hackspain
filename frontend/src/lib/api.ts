export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8010";

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

export const getResumen = () => api<Resumen>("/api/resumen");

export const getFactura = (fileId: string) =>
  api<FacturaDetalle>(`/api/factura/${encodeURIComponent(fileId)}`);

export const lanzarLote = () =>
  api<{ ok: boolean; procesando: boolean }>("/api/lanzar", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: "objetivo=lote",
  });

export const lanzarUna = (fileId: string) =>
  api<{ ok: boolean; procesando: boolean }>("/api/lanzar", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: `objetivo=una&file_id=${encodeURIComponent(fileId)}`,
  });
