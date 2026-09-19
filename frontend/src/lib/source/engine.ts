/**
 * DataSource backed by `backend.server` (Supabase engine API).
 *
 * Pages keep consuming `DataSource`; this adapter maps the wire contract in
 * docs/backend-api.md onto the UI types. Deliverable verdict is always
 * `flujo.salida.verdict` — never the evaluator's preliminary `decision`.
 * Resolution / payment are always null (engine does not authorize payment).
 *
 * Missing engine routes (`/api/normas`, `/api/kpis`, `/api/eventos`, …) return
 * empty / derived stubs — never the inventados of the mock dataset.
 */
import type {
  Baldosa,
  CambioNorma,
  CamposFactura,
  Coste,
  Decision,
  Documento,
  Escalado,
  Evento,
  EventoLog,
  Expediente,
  Extraccion,
  FacturaFila,
  Kpis,
  ParteTrabajo,
  ReglaVeredicto,
  Resultado,
  Salud,
  Veredicto,
  Via,
} from "../types";
import type { DataSource, Filtros } from "../data";
import { createEngineClient, EngineApiError, type EngineClient } from "../engine/client";
import type {
  Flujo,
  FlujoEtapa,
  ResumenFactura,
  Salud as EngineSalud,
} from "../engine/types";

const NORMA_ENGINE = "engine";

export async function probeEngine(base: string): Promise<boolean> {
  try {
    const res = await fetch(`${base}/api/salud`, { cache: "no-store" });
    if (!res.ok) return false;
    const body = (await res.json()) as { postgres?: unknown };
    return typeof body.postgres === "boolean";
  } catch {
    return false;
  }
}

function client(): EngineClient {
  return createEngineClient();
}

function eurosToCent(v: unknown): number | null {
  if (v == null || v === "") return null;
  if (typeof v === "number" && Number.isFinite(v)) return Math.round(v * 100);
  if (typeof v === "string") {
    const n = Number(v.replace(",", ".").trim());
    return Number.isFinite(n) ? Math.round(n * 100) : null;
  }
  return null;
}

function asString(v: unknown): string | null {
  if (v == null) return null;
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return null;
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

function mapVia(ruta: string | null | undefined): Via | null {
  if (ruta === "deterministic" || ruta === "determinista") return "determinista";
  if (ruta === "vision") return "vision";
  return null;
}

function mapEtapaUi(etapas: FlujoEtapa[] | undefined, estado: string): FacturaFila["etapa"] {
  if (!etapas?.length) {
    if (estado === "pendiente") return "ingerida";
    if (estado === "procesando") return "extraida";
    if (estado === "error") return "extraida";
    return "decidida";
  }
  const by = Object.fromEntries(etapas.map((e) => [e.etapa, e.estado]));
  if (by.emitida === "hecha") return "decidida";
  if (by.evaluada === "hecha" || by.revisada === "hecha") return "decidida";
  if (by.extraida === "hecha" || by.extraida === "error") return "extraida";
  return "ingerida";
}

function mapRuleVeredicto(status: unknown, compliance: unknown, verdict: unknown): Veredicto {
  const tokens = [status, compliance, verdict]
    .filter((x) => typeof x === "string")
    .map((x) => String(x).toUpperCase());
  if (tokens.some((t) => ["PASS", "PASSED", "COMPLIANT", "CUMPLE", "OK", "ALLOW"].includes(t))) {
    return "CUMPLE";
  }
  if (tokens.some((t) => ["FAIL", "FAILED", "FALLA", "VIOLATION", "REJECT"].includes(t))) {
    return "FALLA";
  }
  // BLOCKED / NEEDS_REVIEW / NOT_EVALUATED / UNSUPPORTED / NOT_APPLICABLE → not a pass
  return "SIN_DATOS";
}

function camposFromProjected(campos: Record<string, unknown> | undefined): CamposFactura {
  const c = campos ?? {};
  return {
    numero: asString(c.invoice_number ?? c.numero),
    fecha: asString(c.date ?? c.fecha ?? c.issue_date),
    nif_emisor: asString(c.nif ?? c.nif_emisor ?? c.supplier_tax_id),
    proveedor: asString(c.proveedor ?? c.vendor_id ?? c.supplier_name),
    pedido: asString(c.pedido ?? c.purchase_order_reference ?? c.po),
    iban: asString(c.iban),
    base_cent: eurosToCent(c.base ?? c.taxable_base),
    iva_pct: (() => {
      const n = Number(asString(c.iva_pct ?? c.iva_rate));
      return Number.isFinite(n) ? n : null;
    })(),
    iva_cent: eurosToCent(c.iva ?? c.tax),
    total_cent: eurosToCent(c.total),
  };
}

function camposFromFacturaArtifact(factura: Record<string, unknown> | null | undefined): CamposFactura {
  const f = factura ?? {};
  const supplier = asRecord(f.supplier) ?? {};
  const payment = asRecord(f.payment) ?? {};
  const totals = asRecord(f.totals) ?? {};
  const taxes = Array.isArray(f.taxes) ? f.taxes : [];
  const tax0 = asRecord(taxes[0]) ?? {};
  const rate = Number(asString(tax0.rate_percent));
  return {
    numero: asString(f.invoice_number),
    fecha: asString(f.issue_date),
    nif_emisor: asString(supplier.tax_id),
    proveedor: asString(supplier.name),
    pedido: asString(f.purchase_order_reference),
    iban: asString(payment.iban),
    base_cent: eurosToCent(totals.taxable_base),
    iva_pct: Number.isFinite(rate) ? rate : null,
    iva_cent: eurosToCent(tax0.amount),
    total_cent: eurosToCent(totals.total),
  };
}

function mergeCampos(a: CamposFactura, b: CamposFactura): CamposFactura {
  return {
    numero: a.numero ?? b.numero,
    fecha: a.fecha ?? b.fecha,
    nif_emisor: a.nif_emisor ?? b.nif_emisor,
    proveedor: a.proveedor ?? b.proveedor,
    pedido: a.pedido ?? b.pedido,
    iban: a.iban ?? b.iban,
    base_cent: a.base_cent ?? b.base_cent,
    iva_pct: a.iva_pct ?? b.iva_pct,
    iva_cent: a.iva_cent ?? b.iva_cent,
    total_cent: a.total_cent ?? b.total_cent,
  };
}

function costEur(costUsd: number | string | null | undefined): number {
  if (costUsd == null) return 0;
  const n = typeof costUsd === "number" ? costUsd : Number(costUsd);
  return Number.isFinite(n) ? n : 0;
}

function latencyMs(flujo: Flujo): number {
  const trabajos = flujo.extraccion?.trabajos ?? [];
  let total = 0;
  for (const t of trabajos) {
    for (const i of t.intentos ?? []) {
      if (typeof i.latency_seconds === "number") total += i.latency_seconds * 1000;
    }
  }
  return Math.round(total);
}

function motivoFromFlujo(flujo: Flujo): string | null {
  const basis = flujo.salida?.basis;
  const reasons = asRecord(flujo.evaluacion?.resultado)?.decision_reasons;
  if (Array.isArray(reasons) && reasons.length > 0) {
    const first = asRecord(reasons[0]);
    const expl = asString(first?.explanation);
    if (expl) return expl;
  }
  if (flujo.revision?.attention_required) {
    return `revisión requiere atención (${flujo.revision.status ?? "—"}; basis=${basis ?? "—"})`;
  }
  if (basis) return basis;
  return null;
}

function reglasFromFlujo(flujo: Flujo): ReglaVeredicto[] {
  const resultado = asRecord(flujo.evaluacion?.resultado);
  const ruleResults = Array.isArray(resultado?.rule_results) ? resultado!.rule_results : [];
  if (ruleResults.length > 0) {
    return ruleResults.map((raw, idx) => {
      const r = asRecord(raw) ?? {};
      const evidencia: Record<string, string | number | null> = {};
      const inputs = Array.isArray(r.inputs) ? r.inputs : [];
      for (const inp of inputs) {
        const row = asRecord(inp);
        if (!row) continue;
        const field = asString(row.field) ?? `input_${Object.keys(evidencia).length}`;
        const val = row.value;
        evidencia[field] =
          val == null || typeof val === "string" || typeof val === "number" ? (val as string | number | null) : JSON.stringify(val);
      }
      if (typeof r.status === "string") evidencia.status = r.status;
      if (typeof r.compliance === "string") evidencia.compliance = r.compliance;
      return {
        regla: asString(r.rule_id) ?? `rule_${idx}`,
        descripcion: asString(r.explanation) ?? asString(r.applied_consequence) ?? "",
        veredicto: mapRuleVeredicto(r.status, r.compliance, r.verdict),
        evidencia,
      };
    });
  }
  // Fall back to factura.checks-shaped list on the factura endpoint projection
  return [];
}

function decisionFromFlujo(flujo: Flujo, docId: string): Decision {
  const resultado = asRecord(flujo.evaluacion?.resultado);
  const ruleset = asRecord(resultado?.ruleset);
  const norma =
    asString(ruleset?.ruleset_version) ??
    asString(ruleset?.policy_id) ??
    NORMA_ENGINE;
  const verdict = (flujo.salida?.verdict ?? "ESCALAR") as Resultado;
  const snaps = flujo.ejecucion?.snapshots ?? null;
  const snapKinds = snaps
    ? Object.entries(snaps).map(([k, v]) => `${k}:${v.kind}`).join(", ")
    : "—";
  return {
    doc_id: docId,
    norma_version: norma,
    snapshot_erp: snapKinds,
    snapshot_maestro: snapKinds,
    result: verdict,
    motivo: motivoFromFlujo(flujo),
    reglas: reglasFromFlujo(flujo),
    coste_eur: costEur(flujo.extraccion?.cost_usd),
    latencia_ms: latencyMs(flujo),
    creado_at: flujo.evaluacion?.captured_at ?? flujo.identidad?.recibida_at ?? new Date().toISOString(),
  };
}

function extraccionFromFlujo(flujo: Flujo, docId: string): Extraccion | null {
  const ex = flujo.extraccion;
  if (!ex) return null;
  if (ex.status === "unknown" && !ex.factura && !ex.ruta) return null;
  const campos = camposFromFacturaArtifact(asRecord(ex.factura));
  return {
    doc_id: docId,
    intento: ex.trabajos?.reduce((n, t) => n + (t.attempt_count ?? 0), 0) ?? 0,
    plantilla: null,
    via: mapVia(ex.ruta),
    campos,
    campos_faltantes: ex.gaps ?? ex.por_que_vision ?? [],
    cuadra_interna: null,
    coste_eur: costEur(ex.cost_usd),
    latencia_ms: latencyMs(flujo),
    creado_at: flujo.identidad?.recibida_at ?? new Date().toISOString(),
  };
}

function eventosFromEtapas(flujo: Flujo, docId: string): Evento[] {
  return (flujo.etapas ?? []).map((e, i) => ({
    id: i + 1,
    doc_id: docId,
    etapa: e.etapa,
    nivel:
      e.estado === "error" ? ("error" as const) :
      e.estado === "pendiente" || e.estado === "no_registrada" ? ("warn" as const) :
      ("info" as const),
    mensaje: `${e.estado}${e.ref ? ` · ${e.ref}` : ""}`,
    at: e.at ?? flujo.identidad?.recibida_at ?? new Date().toISOString(),
  }));
}

function documentoFromFlujo(flujo: Flujo, estado: string): Documento {
  const id = flujo.identidad;
  return {
    doc_id: id?.sha256 ?? id?.input_id ?? flujo.file_id,
    file_id: flujo.file_id,
    ruta: flujo.file_id,
    bytes: id?.bytes ?? 0,
    tiene_texto: flujo.extraccion?.ruta === "deterministic",
    lote: id?.batch_id?.slice(0, 8) ?? "engine",
    estado,
    intentos: flujo.extraccion?.trabajos?.reduce((n, t) => n + (t.attempt_count ?? 0), 0) ?? 0,
    ultimo_error:
      (flujo.extraccion?.error && asString(flujo.extraccion.error.code)) ||
      (typeof flujo.ejecucion?.error === "string" ? flujo.ejecucion.error : null),
    creado_at: id?.recibida_at ?? new Date().toISOString(),
  };
}

function filaPendiente(row: ResumenFactura): FacturaFila {
  return {
    file_id: row.file_id,
    doc_id: row.file_id,
    lote: "engine",
    etapa: row.estado === "procesando" ? "extraida" : "ingerida",
    tiene_texto: true,
    via: null,
    proveedor: null,
    nif: null,
    pedido: null,
    total_cent: null,
    result: null,
    motivo: row.estado === "procesando" ? "procesando" : "pendiente de la próxima pasada",
    latencia_ms: 0,
    coste_eur: 0,
    intentos: 0,
    decidida_at: null,
  };
}

function filaFromFlujo(flujo: Flujo, estado: string): FacturaFila {
  const doc = documentoFromFlujo(flujo, estado);
  const ex = extraccionFromFlujo(flujo, doc.doc_id);
  const decision = decisionFromFlujo(flujo, doc.doc_id);
  const emitida = flujo.etapas?.find((e) => e.etapa === "emitida");
  return {
    file_id: flujo.file_id,
    doc_id: doc.doc_id,
    lote: doc.lote,
    etapa: mapEtapaUi(flujo.etapas, estado),
    tiene_texto: doc.tiene_texto,
    via: ex?.via ?? null,
    proveedor: ex?.campos.proveedor ?? null,
    nif: ex?.campos.nif_emisor ?? null,
    pedido: ex?.campos.pedido ?? null,
    total_cent: ex?.campos.total_cent ?? null,
    result: decision.result,
    motivo: decision.motivo,
    latencia_ms: decision.latencia_ms,
    coste_eur: decision.coste_eur,
    intentos: doc.intentos,
    decidida_at: emitida?.at ?? flujo.evaluacion?.captured_at ?? null,
  };
}

async function mapPool<T, R>(
  items: T[],
  concurrency: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  if (items.length === 0) return [];
  const out = new Array<R>(items.length);
  let cursor = 0;
  async function worker() {
    while (true) {
      const i = cursor++;
      if (i >= items.length) return;
      out[i] = await fn(items[i]!);
    }
  }
  const n = Math.min(concurrency, items.length);
  await Promise.all(Array.from({ length: n }, () => worker()));
  return out;
}

async function loadFlujo(api: EngineClient, fileId: string): Promise<Flujo | null> {
  try {
    return await api.flujo(fileId);
  } catch (e) {
    if (e instanceof EngineApiError && (e.status === 404 || e.status === 422)) return null;
    throw e;
  }
}

async function filasDesdeResumen(f: Filtros): Promise<FacturaFila[]> {
  const api = client();
  const resumen = await api.resumen();
  let rows = resumen.facturas;
  if (f.q) {
    const q = f.q.toLowerCase();
    rows = rows.filter((r) => r.file_id.toLowerCase().includes(q));
  }

  const pending = rows.filter((r) => r.estado === "pendiente" || r.estado === "procesando");
  const needFlow = rows.filter((r) => r.estado === "hecha" || r.estado === "error");

  const pendingFilas = pending.map(filaPendiente);
  const flows = await mapPool(needFlow, 8, async (r) => {
    const flujo = await loadFlujo(api, r.file_id);
    return { r, flujo };
  });

  const doneFilas: FacturaFila[] = [];
  for (const { r, flujo } of flows) {
    if (!flujo) {
      doneFilas.push({
        ...filaPendiente(r),
        etapa: r.estado === "error" ? "extraida" : "decidida",
        result: (r.decision as Resultado | null) ?? null,
        motivo: r.estado === "error" ? "error" : "sin flujo",
      });
      continue;
    }
    doneFilas.push(filaFromFlujo(flujo, r.estado));
  }

  let all = pendingFilas.concat(doneFilas);
  if (f.result) {
    all = all.filter((row) => row.result === f.result);
  }
  return all;
}

export const engineSource: DataSource = {
  normas: () => [NORMA_ENGINE],
  normaActiva: () => NORMA_ENGINE,

  async kpis() {
    const resumen = await client().resumen();
    const por: Record<Resultado, { n: number; total: number; sin: number }> = {
      PAGAR: { n: resumen.decisiones.PAGAR, total: 0, sin: resumen.decisiones.PAGAR },
      ESCALAR: { n: resumen.decisiones.ESCALAR, total: 0, sin: resumen.decisiones.ESCALAR },
      NO_PAGAR: { n: resumen.decisiones.NO_PAGAR, total: 0, sin: resumen.decisiones.NO_PAGAR },
    };
    // Counts from resumen use preliminary `decision`; totals unknown without N+1.
    const kpis: Kpis = {
      norma: NORMA_ENGINE,
      nDocs: resumen.total,
      porResultado: (["PAGAR", "ESCALAR", "NO_PAGAR"] as Resultado[]).map((r) => ({
        result: r,
        n: por[r].n,
        total_cent: null,
        sin_importe: por[r].sin,
      })),
      costeTotal_eur: 0,
      duracionPasada_s: 0,
    };
    return kpis;
  },

  async baldosas(f) {
    const filas = await filasDesdeResumen(f);
    return filas
      .filter((x): x is FacturaFila & { result: Resultado } => x.result != null)
      .map((x): Baldosa => ({ file_id: x.file_id, result: x.result, motivo: x.motivo }));
  },

  facturas: (f) => filasDesdeResumen(f),

  async eventos(f) {
    // No /api/eventos yet — synthesize a short log from resumen states only.
    const resumen = await client().resumen();
    let filas: EventoLog[] = resumen.facturas.map((r, i) => ({
      id: i + 1,
      doc_id: r.file_id,
      file_id: r.file_id,
      etapa: r.estado,
      nivel: r.estado === "error" ? "error" : r.estado === "pendiente" ? "warn" : "info",
      mensaje: `estado=${r.estado} decision=${r.decision ?? "—"}`,
      at: new Date().toISOString(),
    }));
    if (f.etapa) filas = filas.filter((e) => e.etapa === f.etapa);
    if (f.nivel) filas = filas.filter((e) => e.nivel === f.nivel);
    if (f.q) {
      const q = f.q.toLowerCase();
      filas = filas.filter(
        (e) =>
          (e.file_id?.toLowerCase().includes(q) ?? false) ||
          e.mensaje.toLowerCase().includes(q),
      );
    }
    const total = filas.length;
    if (f.limit) filas = filas.slice(0, f.limit);
    return { filas, total };
  },

  async motivos() {
    const filas = await filasDesdeResumen({});
    const map = new Map<string, { motivo: string; result: Resultado; n: number }>();
    for (const row of filas) {
      if (!row.result || !row.motivo) continue;
      const key = `${row.result}::${row.motivo}`;
      const cur = map.get(key) ?? { motivo: row.motivo, result: row.result, n: 0 };
      cur.n++;
      map.set(key, cur);
    }
    return [...map.values()].sort((a, b) => b.n - a.n);
  },

  async expediente(fileId) {
    const api = client();
    let flujo: Flujo;
    let factura;
    try {
      [flujo, factura] = await Promise.all([api.flujo(fileId), api.factura(fileId)]);
    } catch (e) {
      if (e instanceof EngineApiError && (e.status === 404 || e.status === 422)) return null;
      throw e;
    }

    const doc = documentoFromFlujo(flujo, factura.estado);
    let ex = extraccionFromFlujo(flujo, doc.doc_id);
    const projected = camposFromProjected(factura.campos as Record<string, unknown>);
    if (ex) {
      ex = { ...ex, campos: mergeCampos(ex.campos, projected) };
    } else if (Object.values(projected).some((v) => v != null)) {
      ex = {
        doc_id: doc.doc_id,
        intento: 0,
        plantilla: null,
        via: null,
        campos: projected,
        campos_faltantes: [],
        cuadra_interna: null,
        coste_eur: 0,
        latencia_ms: 0,
        creado_at: doc.creado_at,
      };
    }

    // Prefer rich rule_results; if empty, map factura.checks
    let decision = decisionFromFlujo(flujo, doc.doc_id);
    if (decision.reglas.length === 0 && Array.isArray(factura.checks)) {
      decision = {
        ...decision,
        reglas: factura.checks.map((raw, idx) => {
          const c = asRecord(raw) ?? {};
          return {
            regla: asString(c.rule_id) ?? `check_${idx}`,
            descripcion: asString(c.reason) ?? "",
            veredicto: mapRuleVeredicto(c.verdict, c.status, c.compliance),
            evidencia: { verdict: asString(c.verdict) },
          };
        }),
      };
    }

    const resumen = await api.resumen();
    const ids = resumen.facturas.map((r) => r.file_id);
    const idx = ids.indexOf(fileId);
    const prev = idx > 0 ? ids[idx - 1]! : null;
    const next = idx >= 0 && idx < ids.length - 1 ? ids[idx + 1]! : null;

    const expediente: Expediente = {
      documento: doc,
      extraccion: ex,
      decision,
      otrasDecisiones: [],
      eventos: eventosFromEtapas(flujo, doc.doc_id),
      notas: [],
      proveedor: ex?.campos.nif_emisor
        ? {
            nif: ex.campos.nif_emisor,
            proveedor_id: ex.campos.nif_emisor,
            razon_social: ex.campos.proveedor ?? ex.campos.nif_emisor,
            iban: ex.campos.iban ?? "",
            condiciones_dias: null,
          }
        : null,
      asiento: ex?.campos.pedido
        ? {
            asiento_id: ex.campos.pedido,
            pedido: ex.campos.pedido,
            nif: ex.campos.nif_emisor,
            proveedor_id: null,
            importe_esperado_cent: ex.campos.total_cent ?? 0,
            estado: "PENDIENTE",
            fecha_registro: ex.campos.fecha,
          }
        : null,
      resolucion: null, // engine never records human resolution
      prev,
      next,
    };
    return expediente;
  },

  async bandeja() {
    const filas = await filasDesdeResumen({});
    const escalados: Escalado[] = filas
      .filter((f) => f.result === "ESCALAR")
      .map((f) => {
        const motivo = f.motivo ?? "ESCALAR";
        let categoria: Escalado["categoria"] = "revisar";
        const m = motivo.toLowerCase();
        if (m.includes("importe") || m.includes("amount") || m.includes("total")) categoria = "importe";
        else if (m.includes("missing") || m.includes("falt") || m.includes("campo")) categoria = "campo_faltante";
        else if (m.includes("ilegible") || m.includes("vision") || m.includes("text")) categoria = "ilegible";
        return {
          file_id: f.file_id,
          doc_id: f.doc_id,
          motivo,
          categoria,
          proveedor: f.nif
            ? {
                nif: f.nif,
                proveedor_id: f.nif,
                razon_social: f.proveedor ?? f.nif,
                iban: "",
                condiciones_dias: null,
              }
            : null,
          pedido: f.pedido,
          total_cent: f.total_cent,
          importe_erp_cent: null,
          email_posible: Boolean(f.proveedor || f.nif),
          resolucion: null,
        };
      });
    return escalados;
  },

  async coste(): Promise<Coste> {
    // /api/coste not implemented — empty measured shape, not mock inventados.
    return {
      rutas: [],
      coste_por_doc_eur: 0,
      docs_por_segundo: 0,
      proyeccion: [],
      punto_cruce_ocr: "Coste por ruta aún no expuesto por la engine API.",
    };
  },

  async diffNormas(): Promise<CambioNorma[]> {
    return [];
  },

  async partes(): Promise<ParteTrabajo[]> {
    // No run-list endpoint yet; individuales vía /api/ejecucion/{key}.
    return [];
  },

  async salud(): Promise<Salud> {
    const api = client();
    const [s, resumen]: [EngineSalud, Awaited<ReturnType<EngineClient["resumen"]>>] =
      await Promise.all([api.salud(), api.resumen()]);
    return {
      erp: {
        ok: s.postgres,
        snapshot: s.ok ? "supabase" : "unavailable",
        asientos: s.facturas_en_disco,
      },
      llm: {
        ok: s.storage_bucket !== false,
        modo: s.ok ? "normal" : "degradado",
      },
      pendientes: resumen.conteo.pendiente + resumen.conteo.procesando,
      reintentos: 0,
      errores24h: resumen.conteo.error,
    };
  },
};
