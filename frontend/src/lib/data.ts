/**
 * LA COSTURA. Las páginas consumen únicamente esta interfaz.
 * Hoy la implementa el mock; enchufar alberto.db es escribir una segunda
 * DataSource (better-sqlite3 o fetch al FastAPI) sin tocar ninguna vista.
 */
import type {
  Baldosa, CambioNorma, Coste, Escalado, EventoLog, Expediente, FacturaFila,
  Kpis, ParteTrabajo, Resultado, Salud,
} from "./types";
import * as mock from "./mock/dataset";
import * as normas from "./normas";

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

// ── implementación mock ─────────────────────────────────────────────

const decisiones = (norma: string) => normas.decisionesDe(norma);

function filtrados(f: Filtros) {
  const decs = decisiones(f.norma ?? normas.normaActiva());
  return mock.documentos.filter((d) => {
    const dec = decs.get(d.doc_id)!;
    if (f.result && dec.result !== f.result) return false;
    if (f.q && !d.file_id.toLowerCase().includes(f.q.toLowerCase())) return false;
    return true;
  });
}

const mockSource: DataSource = {
  normas: () => normas.versiones(),
  normaActiva: () => normas.normaActiva(),

  async kpis(norma) {
    const decs = decisiones(norma);
    const por = new Map<Resultado, { n: number; total: number; sin: number }>();
    for (const d of mock.documentos) {
      const dec = decs.get(d.doc_id)!;
      const t = mock.extracciones.get(d.doc_id)?.campos.total_cent ?? null;
      const acc = por.get(dec.result) ?? { n: 0, total: 0, sin: 0 };
      acc.n++;
      if (t === null) acc.sin++;
      else acc.total += t;
      por.set(dec.result, acc);
    }
    return {
      norma,
      nDocs: mock.documentos.length,
      porResultado: (["PAGAR", "ESCALAR", "NO_PAGAR"] as Resultado[]).map((r) => {
        const a = por.get(r) ?? { n: 0, total: 0, sin: 0 };
        return { result: r, n: a.n, total_cent: a.n === a.sin ? null : a.total, sin_importe: a.sin };
      }),
      costeTotal_eur: 0.87,
      duracionPasada_s: 10.2,
    };
  },

  async baldosas(f) {
    const decs = decisiones(f.norma ?? normas.normaActiva());
    return filtrados(f).map((d) => {
      const dec = decs.get(d.doc_id)!;
      return { file_id: d.file_id, result: dec.result, motivo: dec.motivo };
    });
  },

  async facturas(f) {
    const decs = decisiones(f.norma ?? normas.normaActiva());
    return filtrados(f).map((d) => {
      const dec = decs.get(d.doc_id)!;
      const ext = mock.extracciones.get(d.doc_id)!;
      const nif = ext.campos.nif_emisor;
      return {
        file_id: d.file_id,
        doc_id: d.doc_id,
        lote: d.lote,
        etapa: "decidida" as const,
        tiene_texto: d.tiene_texto,
        via: ext.via,
        proveedor: ext.campos.proveedor,
        nif,
        pedido: ext.campos.pedido,
        total_cent: ext.campos.total_cent,
        result: dec.result,
        motivo: dec.motivo,
        latencia_ms: ext.latencia_ms + dec.latencia_ms,
        coste_eur: ext.coste_eur + dec.coste_eur,
        intentos: d.intentos,
        decidida_at: dec.creado_at,
      };
    });
  },

  async eventos(f) {
    const fileDe = new Map(mock.documentos.map((d) => [d.doc_id, d.file_id]));
    let filas: EventoLog[] = mock.eventos.map((e) => ({
      ...e,
      file_id: e.doc_id ? fileDe.get(e.doc_id) ?? null : null,
    }));
    if (f.etapa) filas = filas.filter((e) => e.etapa === f.etapa);
    if (f.nivel) filas = filas.filter((e) => e.nivel === f.nivel);
    if (f.q) {
      const q = f.q.toLowerCase();
      filas = filas.filter(
        (e) => e.file_id?.toLowerCase().includes(q) || e.mensaje.toLowerCase().includes(q),
      );
    }
    filas.sort((a, b) => b.at.localeCompare(a.at) || b.id - a.id);
    const total = filas.length;
    return { filas: filas.slice(0, f.limit ?? 200), total };
  },

  async motivos(norma) {
    const decs = decisiones(norma);
    const acc = new Map<string, { result: Resultado; n: number }>();
    for (const dec of decs.values()) {
      if (!dec.motivo) continue;
      const a = acc.get(dec.motivo) ?? { result: dec.result, n: 0 };
      a.n++;
      acc.set(dec.motivo, a);
    }
    return [...acc.entries()]
      .map(([motivo, a]) => ({ motivo, ...a }))
      .sort((x, y) => y.n - x.n);
  },

  async expediente(fileId, norma) {
    const doc = mock.documentos.find((d) => d.file_id === fileId);
    if (!doc) return null;
    const dec = decisiones(norma).get(doc.doc_id)!;
    const otras = normas.versiones()
      .filter((v) => v !== norma)
      .map((v) => normas.decisionesDe(v).get(doc.doc_id)!);
    const ext = mock.extracciones.get(doc.doc_id) ?? null;
    const pedido = ext?.campos.pedido ?? null;
    const nif = ext?.campos.nif_emisor ?? null;
    const prov = (nif && mock.proveedorPorNif.get(nif)) || null;
    const idx = mock.documentos.indexOf(doc);
    return {
      documento: doc,
      extraccion: ext,
      decision: dec,
      otrasDecisiones: otras,
      eventos: mock.eventos.filter((e) => e.doc_id === doc.doc_id),
      notas: mock.notas.filter(
        (n) =>
          (n.ambito === "pedido" && n.clave === pedido) ||
          (n.ambito === "proveedor" && n.clave === prov?.proveedor_id) ||
          (n.ambito === "regla" && dec.reglas.some((r) => r.regla === n.clave && r.veredicto !== "CUMPLE")),
      ),
      proveedor: prov,
      asiento: pedido ? mock.asientos.get(pedido) ?? null : null,
      resolucion: null,
      prev: mock.documentos[idx - 1]?.file_id ?? null,
      next: mock.documentos[idx + 1]?.file_id ?? null,
    };
  },

  async bandeja() {
    const out: Escalado[] = [];
    for (const d of mock.documentos) {
      const dec = mock.decisionesV3.get(d.doc_id)!;
      if (dec.result !== "ESCALAR") continue;
      const caso = mock.casoDe.get(d.doc_id)!;
      const ext = mock.extracciones.get(d.doc_id);
      const nif = ext?.campos.nif_emisor ?? null;
      const prov = (nif && mock.proveedorPorNif.get(nif)) || null;
      const pedido = ext?.campos.pedido ?? null;
      const categoria =
        caso === "importe_desvio" ? "importe"
        : caso === "revisar" ? "revisar"
        : caso === "sin_texto" ? "ilegible"
        : "campo_faltante";
      out.push({
        file_id: d.file_id, doc_id: d.doc_id, motivo: dec.motivo!,
        categoria,
        proveedor: prov, pedido,
        total_cent: ext?.campos.total_cent ?? null,
        importe_erp_cent: pedido ? mock.asientos.get(pedido)?.importe_esperado_cent ?? null : null,
        email_posible: categoria === "importe" || (categoria === "campo_faltante" && prov !== null),
        resolucion: null,
      });
    }
    const orden = { importe: 0, campo_faltante: 1, revisar: 2, ilegible: 3 };
    return out.sort((a, b) => orden[a.categoria] - orden[b.categoria]);
  },

  async coste() {
    return {
      rutas: [
        { via: "determinista", docs: 471, coste_total_eur: 0, coste_por_doc_eur: 0, latencia_p50_ms: 5.4, latencia_p95_ms: 8.9 },
        { via: "vision", docs: 29, coste_total_eur: 0.87, coste_por_doc_eur: 0.03, latencia_p50_ms: 2100, latencia_p95_ms: 4800 },
      ],
      coste_por_doc_eur: 0.00174,
      docs_por_segundo: 49,
      proyeccion: [
        { docs_mes: 10_000, coste_eur: 17.4 },
        { docs_mes: 100_000, coste_eur: 174 },
        { docs_mes: 1_000_000, coste_eur: 1740 },
      ],
      punto_cruce_ocr:
        "Con un 5,8 % de documentos por visión (0,03 €/doc), el OCR local (Tesseract) se amortizaría a partir de ~40.000 docs/mes. Con 29 documentos, no.",
    };
  },

  async diffNormas(de, a) {
    const dDe = decisiones(de);
    const dA = decisiones(a);
    const out: CambioNorma[] = [];
    for (const doc of mock.documentos) {
      const x = dDe.get(doc.doc_id)!;
      const y = dA.get(doc.doc_id)!;
      if (x.result !== y.result)
        out.push({ file_id: doc.file_id, de: x.result, a: y.result, motivo: y.motivo ?? "" });
    }
    return out;
  },

  async partes() {
    return [
      { pasada: "run-0003", inicio: "2026-09-19T10:01:12", duracion_s: 10.2, docs: 500, coste_eur: 0.87, pagar: 431, escalar: 49, no_pagar: 20, norma: "v3", lote: "lote1" },
      { pasada: "run-0002", inicio: "2026-09-19T08:44:03", duracion_s: 11.0, docs: 500, coste_eur: 0.87, pagar: 431, escalar: 49, no_pagar: 20, norma: "v3", lote: "lote1" },
      { pasada: "run-0001", inicio: "2026-09-18T23:58:41", duracion_s: 12.9, docs: 500, coste_eur: 0, pagar: 431, escalar: 78, no_pagar: 20, norma: "v3", lote: "lote1" },
    ];
  },

  async salud() {
    return {
      erp: { ok: true, snapshot: "erp-0003", asientos: 516 },
      llm: { ok: true, modo: "normal" },
      pendientes: 0,
      reintentos: 3,
      errores24h: 0,
    };
  },

};

export const data: DataSource = mockSource;
