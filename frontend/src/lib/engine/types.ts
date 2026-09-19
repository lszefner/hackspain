/**
 * 1:1 mirror of the `backend.server` JSON API (see docs/backend-api.md).
 *
 * Field names are identical to the wire format — Spanish where the API is
 * Spanish. Deep engine artifacts (evaluator results, review bodies, parsed
 * invoices, stored checks) are typed as `Record<string, unknown>` /
 * `unknown`: they are immutable engine records, not UI contracts. Their
 * schemas live in:
 *   - rules_ingestion/evaluation_result.schema.json   (evaluacion.resultado,
 *     Factura.evaluation_result)
 *   - rules_ingestion/contextual_contracts.py REVIEW_SCHEMA (revision body,
 *     Factura.contextual_review)
 *   - benchmark/schemas + ingestion/schemas            (invoice, reading,
 *     checks — the extraction contracts)
 */

export type Decision = "PAGAR" | "ESCALAR" | "NO_PAGAR";
export type EstadoRevision = "pendiente" | "procesando" | "hecha" | "error";
export type ReviewStatus = "COMPLETED" | "INCOMPLETE" | "FAILED" | "DISABLED";
export type FlujoEtapaNombre =
  | "recibida"
  | "extraida"
  | "evaluada"
  | "revisada"
  | "emitida"
  | "resuelta"
  | "pagada";
export type FlujoEtapaEstado =
  "hecha" | "pendiente" | "error" | "no_registrada" | "desactivada";

// ── GET /api/salud ──────────────────────────────────────────────────

export interface Salud {
  ok: boolean;
  postgres: boolean;
  /** null when no cheap readiness probe ran; false when it failed */
  storage_bucket: boolean | null;
  facturas_dir: string;
  facturas_en_disco: number;
  procesando: boolean;
  /** exception class name when something failed, else null */
  error: string | null;
}

// ── GET /api/resumen ────────────────────────────────────────────────

export interface ResumenFactura {
  file_id: string;
  estado: EstadoRevision;
  decision: Decision | null;
}

export interface Resumen {
  total: number;
  conteo: Record<EstadoRevision, number>;
  decisiones: Record<Decision, number>;
  facturas: ResumenFactura[];
  lote_tamano: number;
  procesando: boolean;
  error: string | null;
}

// ── GET /api/factura/{file_id} ──────────────────────────────────────

export interface Factura {
  file_id: string;
  estado: EstadoRevision;
  decision: Decision | null;
  /** stored rule verdicts ({rule_id, verdict, reason}); [] when pending */
  checks: Record<string, unknown>[];
  /** projected invoice fields; {} when pending */
  campos: Record<string, unknown>;
  error: unknown;
  // The fields below are absent (not null) on the `estado: "pendiente"` stub.
  evaluation_record_id?: string | null;
  review_record_id?: string | null;
  /** evaluator result artifact; schema: evaluation_result.schema.json */
  evaluation_result?: Record<string, unknown> | null;
  /** review body artifact; schema: contextual_contracts.py REVIEW_SCHEMA */
  contextual_review?: Record<string, unknown> | null;
  review_status?: ReviewStatus | null;
  attention_required?: boolean;
}

// ── GET /api/factura/{file_id}/flujo ────────────────────────────────

export interface FlujoIdentidad {
  input_id: string;
  batch_id: string;
  sha256: string | null;
  bytes: number | null;
  recibida_at: string | null;
}

export interface FlujoSnapshot {
  kind: string;
  captured_at: string;
  as_of: string | null;
  asserted_by: string;
  authoritative_for: string[];
  scope: string;
  availability: "available" | "partial" | "unavailable";
}

export interface FlujoEjecucion {
  request_key: string;
  state: string | null;
  error: string | null;
  captured_at: string | null;
  evaluation_date_policy: string | null;
  rule_generation: "pinned" | "generated" | "pending" | "reused" | null;
  snapshots: Record<string, FlujoSnapshot> | null;
  /** present when the frozen run-input artifact could not be loaded */
  artifact_error?: { code: string };
}

export interface FlujoIntento {
  attempt_number: number;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  latency_seconds: number | null;
  usage: Record<string, unknown> | null;
  provider_request_id: string | null;
  error: Record<string, unknown> | null;
  raw_artifact_ids: string[];
}

export interface FlujoTrabajo {
  id: string;
  stage: "original" | "render" | "reading" | "interpretation" | string;
  provider: string | null;
  model: string | null;
  state: string;
  attempt_count: number;
  max_attempts: number;
  /** job settings minus `config` (server-side only) */
  extra: Record<string, unknown> | null;
  config_version: string | null;
  prompt_version: string | null;
  adapter_version: string | null;
  schema_hash: string | null;
  artifact_id: string | null;
  last_error: Record<string, unknown> | null;
  created_at: string | null;
  intentos: FlujoIntento[];
}

export interface FlujoExtraccion {
  status: "completed" | "needs_review" | "failed" | "unknown" | string | null;
  error: { code: string; unknown_outcome?: boolean } | null;
  cost_usd: number | string | null;
  ruta: "deterministic" | "vision" | null;
  determinista: {
    attempted: boolean;
    accepted: boolean;
    gaps: string[];
  } | null;
  /** why vision ran: determinista.gaps when ruta === 'vision', else [] */
  por_que_vision: string[];
  paginas: number | null;
  trabajos: FlujoTrabajo[] | { error: { code: string } };
  /** parsed invoice artifact; schema: benchmark/schemas invoice */
  factura: Record<string, unknown> | null;
  /** stored structural checks artifact; schema: ingestion validation */
  checks: Record<string, unknown> | null;
  gaps: string[];
}

export interface FlujoEvaluacion {
  record_id: string;
  evaluation_id?: string;
  evaluation_date?: string;
  captured_at?: string;
  interpreter?: string;
  decision?: Decision;
  /** full evaluator result; schema: evaluation_result.schema.json */
  resultado?: Record<string, unknown> | { code: string };
  /** per-source summaries keyed by source name */
  fuentes?: Record<string, Record<string, unknown>> | { code: string };
  rule_source_lineage?: Record<string, unknown>;
  /** present when the record could not be loaded */
  error?: { code: string };
}

export interface FlujoRevision {
  record_id: string | null;
  status?: ReviewStatus | null;
  attention_required?: boolean;
  error?: unknown;
  reviewed_at?: string | null;
  /** review body; schema: contextual_contracts.py REVIEW_SCHEMA */
  rule_reviews?: Record<string, unknown>[] | null;
  findings?: Record<string, unknown>[] | null;
  provider?: string | null;
  model?: string | null;
}

export interface FlujoSalida {
  /** deliverable verdict — identical to outcomes.jsonl output */
  verdict: Decision;
  basis: string;
}

export interface FlujoEtapa {
  etapa: FlujoEtapaNombre;
  estado: FlujoEtapaEstado;
  at: string | null;
  ref: string | null;
}

export interface Flujo {
  file_id: string;
  identidad: FlujoIdentidad | null;
  ejecucion: FlujoEjecucion | null;
  extraccion: FlujoExtraccion | null;
  evaluacion: FlujoEvaluacion | null;
  revision: FlujoRevision | null;
  salida: FlujoSalida;
  /** human resolution is never tracked by the engine — always null */
  resolucion: null;
  /** payment is never tracked by the engine — always null */
  pago: null;
  etapas: FlujoEtapa[];
}

// ── GET /api/ejecucion/{request_key} ────────────────────────────────

export interface EjecucionArchivo {
  file_id: string;
  evaluation_record_id: string | null;
  review_record_id: string | null;
  evaluation_date: string | null;
  evaluation_date_fallback?: boolean;
  decision?: Decision;
  review_status?: ReviewStatus;
  attention_required?: boolean;
  error: { code: string } | null;
}

export interface Ejecucion {
  request_key: string;
  state: "completed" | "partial" | "failed" | "running" | "unknown" | string;
  batch_id: string | null;
  revision_counts?: { stored: number; failed: number };
  files?: EjecucionArchivo[];
  /** error_code when the run has no result artifact yet */
  error?: string | null;
}

// ── GET /api/estado ─────────────────────────────────────────────────

export interface Estado {
  procesando: boolean;
  error: string | null;
}

// ── POST /api/lanzar ────────────────────────────────────────────────

export interface LanzarRespuesta {
  ok: boolean;
  procesando?: boolean;
  /** present on 422 invalid_run_request */
  error?: string;
}
