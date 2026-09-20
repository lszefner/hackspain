/**
 * LA COSTURA. Las páginas consumen únicamente esta interfaz.
 * Hoy la implementa el mock; enchufar alberto.db es escribir una segunda
 * DataSource (better-sqlite3 o fetch al FastAPI) sin tocar ninguna vista.
 */
import type {
  Baldosa, CambioNorma, Coste, Escalado, EventoLog, Expediente, FacturaFila,
  Kpis, ParteTrabajo, Resultado, Salud,
} from "./types";

export interface Filtros {
  norma?: string;
  result?: Resultado;
  q?: string;
}

export interface DataSource {
  /** versiones publicadas, en orden de publicación */
  normas(): string[];
  /** la versión que usan las vistas si no se pide otra: la última publicada */
  normaActiva(): string;
  kpis(norma: string): Promise<Kpis>;
  baldosas(f: Filtros): Promise<Baldosa[]>;
  /** el listado crudo: dónde está cada factura y toda la data relevante */
  facturas(f: Filtros): Promise<FacturaFila[]>;
  /** el registro de qué pasó y cuándo, filtrable */
  eventos(f: { etapa?: string; nivel?: string; q?: string; limit?: number }): Promise<{ filas: EventoLog[]; total: number }>;
  motivos(norma: string): Promise<{ motivo: string; result: Resultado; n: number }[]>;
  expediente(fileId: string, norma: string): Promise<Expediente | null>;
  bandeja(): Promise<Escalado[]>;
  coste(): Promise<Coste>;
  diffNormas(de: string, a: string): Promise<CambioNorma[]>;
  partes(): Promise<ParteTrabajo[]>;
  salud(): Promise<Salud>;
}

// Legacy views now redirect to the canonical live desk. Keep the interface
// for historical adapters, but never select demo data when a backend fails.
export const data: DataSource = new Proxy({} as DataSource, {
  get() { throw new Error("Legacy data source retired. Use the canonical backend desk queries."); },
});
