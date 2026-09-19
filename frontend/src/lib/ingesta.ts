/**
 * Ingesta de facturas desde la UI. En el mock, los documentos subidos quedan
 * en un almacén en memoria como "ingerida / pendiente de la próxima pasada":
 * la web no inventa extracciones ni decisiones que el motor no ha hecho.
 * En producción esto es escribir el PDF en la caja y lanzar `alberto ingesta`
 * (idempotente: doc_id = sha256 del fichero, file_id normalizado a NFC).
 */
import type { Evento } from "./types";

export interface DocIngerido {
  doc_id: string;
  file_id: string; // NFC, como exige el verificador
  bytes: number;
  lote: string;
  creado_at: string;
}

interface Almacen {
  docs: Map<string, DocIngerido>; // por file_id
  eventos: Evento[];
  seq: number;
}

const g = globalThis as unknown as { __ingesta?: Almacen };
const almacen = (): Almacen =>
  (g.__ingesta ??= { docs: new Map(), eventos: [], seq: 900001 });

const hash = (s: string) => {
  let h = 2166136261;
  for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return (h >>> 0).toString(16).padStart(8, "0").repeat(8).slice(0, 64);
};

export interface ResultadoIngesta {
  añadidas: string[];
  duplicadas: string[];
  rechazadas: string[]; // no-PDF
}

export function ingerir(ficheros: { name: string; size: number }[]): ResultadoIngesta {
  const a = almacen();
  const out: ResultadoIngesta = { añadidas: [], duplicadas: [], rechazadas: [] };
  const ahora = new Date().toISOString().slice(0, 19);

  for (const f of ficheros) {
    const file_id = f.name.normalize("NFC");
    if (!file_id.toLowerCase().endsWith(".pdf")) {
      out.rechazadas.push(file_id);
      continue;
    }
    if (a.docs.has(file_id)) {
      // idempotencia: el mismo fichero dos veces no crea dos documentos
      out.duplicadas.push(file_id);
      a.eventos.push({
        id: a.seq++, doc_id: hash(file_id), etapa: "ingesta", nivel: "info",
        mensaje: "ya estaba registrado: se ignora (idempotencia por doc_id)", at: ahora,
      });
      continue;
    }
    const doc: DocIngerido = {
      doc_id: hash(file_id), file_id, bytes: f.size, lote: "manual", creado_at: ahora,
    };
    a.docs.set(file_id, doc);
    a.eventos.push({
      id: a.seq++, doc_id: doc.doc_id, etapa: "ingesta", nivel: "info",
      mensaje: `documento registrado desde la web (${f.size.toLocaleString("es-ES")} bytes) · pendiente de la próxima pasada`,
      at: ahora,
    });
    out.añadidas.push(file_id);
  }
  return out;
}

export const docsIngeridos = (): DocIngerido[] =>
  [...almacen().docs.values()].sort((a, b) => b.creado_at.localeCompare(a.creado_at));

export const eventosIngesta = (): Evento[] => [...almacen().eventos];
