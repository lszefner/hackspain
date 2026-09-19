/**
 * Thin typed client for the `backend.server` JSON API.
 * Contract: docs/backend-api.md. Not wired into `data.ts` yet.
 */
import type {
  Ejecucion,
  Estado,
  Factura,
  Flujo,
  LanzarRespuesta,
  Resumen,
  Salud,
} from "./types";

export class EngineApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
  ) {
    super(`engine API ${status}: ${code}`);
    this.name = "EngineApiError";
  }
}

async function request<T>(base: string, path: string): Promise<T> {
  const res = await fetch(`${base}${path}`, { cache: "no-store" });
  if (!res.ok) {
    let code = res.statusText;
    try {
      const body = (await res.json()) as { error?: unknown };
      if (typeof body?.error === "string") code = body.error;
    } catch {
      // non-JSON error body: keep the status text
    }
    throw new EngineApiError(res.status, code);
  }
  return (await res.json()) as T;
}

export function createEngineClient(
  base: string = process.env.NEXT_PUBLIC_API_BASE ?? "",
) {
  return {
    salud: () => request<Salud>(base, "/api/salud"),
    resumen: () => request<Resumen>(base, "/api/resumen"),
    factura: (fileId: string) =>
      request<Factura>(base, `/api/factura/${encodeURIComponent(fileId)}`),
    flujo: (fileId: string) =>
      request<Flujo>(base, `/api/factura/${encodeURIComponent(fileId)}/flujo`),
    ejecucion: (requestKey: string) =>
      request<Ejecucion>(base, `/api/ejecucion/${encodeURIComponent(requestKey)}`),
    estado: () => request<Estado>(base, "/api/estado"),
    lanzar: async (opts: {
      requestKey: string;
      fileId?: string;
    }): Promise<LanzarRespuesta> => {
      const form = new URLSearchParams({ request_key: opts.requestKey });
      if (opts.fileId) {
        form.set("objetivo", "una");
        form.set("file_id", opts.fileId);
      }
      const res = await fetch(`${base}/api/lanzar`, {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: form.toString(),
      });
      const body = (await res.json().catch(() => ({}))) as LanzarRespuesta;
      if (!res.ok) {
        throw new EngineApiError(res.status, body.error ?? res.statusText);
      }
      return body;
    },
  };
}

export type EngineClient = ReturnType<typeof createEngineClient>;
