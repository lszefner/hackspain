/**
 * Espejo 1:1 del esquema de alberto.db (SQLite).
 * Los importes van SIEMPRE en céntimos (enteros) — el invariante Decimal
 * del backend proyectado a TypeScript. Nunca floats para dinero.
 */

export type Resultado = "PAGAR" | "NO_PAGAR" | "ESCALAR";
export type Veredicto = "CUMPLE" | "FALLA" | "SIN_DATOS";
export type Via = "determinista" | "vision";

// ── tablas ──────────────────────────────────────────────────────────

export interface Documento {
  doc_id: string; // sha256 del fichero
  file_id: string; // nombre exacto del PDF, NFC
  ruta: string;
  bytes: number;
  tiene_texto: boolean;
  lote: string; // lote1 | lote2
  estado: string;
  intentos: number;
  ultimo_error: string | null;
  creado_at: string;
}

export interface CamposFactura {
  numero: string | null;
  fecha: string | null;
  nif_emisor: string | null;
  proveedor: string | null;
  pedido: string | null;
  iban: string | null;
  base_cent: number | null;
  iva_pct: number | null;
  iva_cent: number | null;
  total_cent: number | null;
}

export interface Extraccion {
  doc_id: string;
  intento: number;
  plantilla: string | null;
  via: Via | null;
  campos: CamposFactura;
  campos_faltantes: string[];
  cuadra_interna: boolean | null; // base + IVA == total
  coste_eur: number; // coste real de la extracción (0 en la vía determinista)
  latencia_ms: number;
  creado_at: string;
}

export interface ReglaVeredicto {
  regla: string; // R1_nif_iban … R5_estado_erp
  descripcion: string;
  veredicto: Veredicto;
  evidencia: Record<string, string | number | null>;
}

export interface Decision {
  doc_id: string;
  norma_version: string;
  snapshot_erp: string;
  snapshot_maestro: string;
  result: Resultado;
  motivo: string | null;
  reglas: ReglaVeredicto[];
  coste_eur: number;
  latencia_ms: number;
  creado_at: string;
}

export interface Evento {
  id: number;
  doc_id: string | null;
  etapa: string; // ingesta | snapshot | maestro | extrae | decide | emite
  nivel: "info" | "warn" | "error";
  mensaje: string;
  at: string;
}

export interface Resolucion {
  doc_id: string;
  result: Resultado;
  motivo: string;
  resuelto_por: string | null;
  at: string;
}

export interface Nota {
  id: number;
  ambito: "proveedor" | "pedido" | "regla" | "general";
  clave: string | null; // P002 | PO-2026-0007 | R3_iva
  texto: string;
  origen: string;
  creado_at: string;
}

export interface Proveedor {
  nif: string;
  proveedor_id: string;
  razon_social: string;
  iban: string;
  condiciones_dias: number | null;
}

export interface Asiento {
  asiento_id: string;
  pedido: string;
  nif: string | null;
  proveedor_id: string | null;
  importe_esperado_cent: number;
  estado: "PENDIENTE" | "PAGADA";
  fecha_registro: string | null;
}

// ── vistas compuestas que consumen las páginas ──────────────────────

export interface Baldosa {
  file_id: string;
  result: Resultado;
  motivo: string | null;
}

export interface KpiResultado {
  result: Resultado;
  n: number;
  /** suma de totales conocidos; null si ninguno tiene importe */
  total_cent: number | null;
  /** docs sin importe conocido (imágenes sin extraer) */
  sin_importe: number;
}

export interface Kpis {
  norma: string;
  nDocs: number;
  porResultado: KpiResultado[];
  costeTotal_eur: number;
  duracionPasada_s: number;
}

export interface Expediente {
  documento: Documento;
  extraccion: Extraccion | null;
  decision: Decision;
  /** decisiones del mismo doc con otra norma/snapshot (la trazabilidad de versiones) */
  otrasDecisiones: Decision[];
  eventos: Evento[];
  /** solo las notas que aplican a ESTE proveedor/pedido/regla */
  notas: Nota[];
  proveedor: Proveedor | null;
  asiento: Asiento | null;
  resolucion: Resolucion | null;
  prev: string | null;
  next: string | null;
}

export interface Escalado {
  file_id: string;
  doc_id: string;
  motivo: string;
  categoria: "importe" | "campo_faltante" | "revisar" | "ilegible";
  proveedor: Proveedor | null;
  pedido: string | null;
  total_cent: number | null;
  importe_erp_cent: number | null;
  /** hay destinatario y datos suficientes para redactar el email */
  email_posible: boolean;
  resolucion: Resolucion | null;
}

export interface CosteRuta {
  via: Via;
  docs: number;
  coste_total_eur: number;
  coste_por_doc_eur: number;
  latencia_p50_ms: number;
  latencia_p95_ms: number;
}

export interface Coste {
  rutas: CosteRuta[];
  coste_por_doc_eur: number; // media ponderada
  docs_por_segundo: number;
  proyeccion: { docs_mes: number; coste_eur: number }[];
  punto_cruce_ocr: string; // explicación del punto donde el OCR local se amortiza
}

export interface CambioNorma {
  file_id: string;
  de: Resultado;
  a: Resultado;
  motivo: string;
}

export interface ParteTrabajo {
  pasada: string;
  inicio: string;
  duracion_s: number;
  docs: number;
  coste_eur: number;
  pagar: number;
  escalar: number;
  no_pagar: number;
  norma: string;
  lote: string;
}

export interface Salud {
  erp: { ok: boolean; snapshot: string; asientos: number };
  llm: { ok: boolean; modo: "normal" | "degradado" };
  pendientes: number;
  reintentos: number;
  errores24h: number;
}
