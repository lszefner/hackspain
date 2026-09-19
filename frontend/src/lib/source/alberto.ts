/**
 * DataSource real: lee `alberto.db` a traves de la API de `alberto web`.
 *
 * Es la segunda implementacion que anticipaba el comentario de data.ts. No
 * toca ninguna vista: las paginas siguen consumiendo la interfaz.
 *
 * Importante: aqui no se calcula nada. Los numeros son exactamente los que
 * salen en outcomes.jsonl, porque vienen de las mismas tablas. Si la
 * pantalla y el entregable divergieran, seria el peor sitio posible para
 * enterarse.
 */
import type {
  Baldosa, CambioNorma, Coste, Escalado, EventoLog, Expediente, FacturaFila,
  Kpis, ParteTrabajo, Resultado, Salud,
} from "../types";
import type { DataSource, Filtros } from "../data";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

async function get<T>(path: string, params?: Record<string, string | undefined>): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v) qs.set(k, v);
  const sep = qs.toString() ? `?${qs}` : "";
  const res = await fetch(`${BASE}${path}${sep}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** Las normas se piden una vez al arrancar: la interfaz las expone sincronas. */
let cacheNormas: { normas: string[]; activa: string } = { normas: [], activa: "" };

export async function precargarNormas(): Promise<void> {
  cacheNormas = await get<{ normas: string[]; activa: string }>("/api/normas");
}

const f = (x: Filtros) => ({ norma: x.norma, result: x.result, q: x.q });

export const albertoSource: DataSource = {
  normas: () => cacheNormas.normas,
  normaActiva: () => cacheNormas.activa,
  kpis: (norma) => get<Kpis>("/api/kpis", { norma }),
  baldosas: (x) => get<Baldosa[]>("/api/baldosas", f(x)),
  facturas: (x) => get<FacturaFila[]>("/api/facturas", f(x)),
  eventos: (x) =>
    get<{ filas: EventoLog[]; total: number }>("/api/eventos", {
      etapa: x.etapa, nivel: x.nivel, q: x.q,
      limit: x.limit ? String(x.limit) : undefined,
    }),
  motivos: (norma) =>
    get<{ motivo: string; result: Resultado; n: number }[]>("/api/motivos", { norma }),
  expediente: (fileId, norma) =>
    get<Expediente>(`/api/expediente/${encodeURIComponent(fileId)}`, { norma })
      .catch(() => null),
  bandeja: () => get<Escalado[]>("/api/bandeja"),
  coste: () => get<Coste>("/api/coste"),
  diffNormas: (de, a) => get<CambioNorma[]>("/api/diff", { de, a }),
  partes: () => get<ParteTrabajo[]>("/api/partes"),
  salud: () => get<Salud>("/api/salud"),
};

/** ¿Hay backend vivo? Decide si las vistas usan datos reales o el mock. */
export async function hayBackend(): Promise<boolean> {
  if (!BASE) return false;
  try {
    await precargarNormas();
    return cacheNormas.normas.length > 0;
  } catch {
    return false;
  }
}
