import type { Flujo, FlujoTrabajo } from "@/lib/engine/types";
import type { Detail } from "./types";

export type EvidenceRecord = Record<string, unknown>;
export function record(value: unknown): EvidenceRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as EvidenceRecord)
    : {};
}
export function records(value: unknown): EvidenceRecord[] {
  return Array.isArray(value) ? value.map(record) : [];
}
export function text(value: unknown, fallback = "Not recorded"): string {
  return typeof value === "string" && value
    ? value
    : typeof value === "number"
      ? String(value)
      : fallback;
}
export function valueText(value: unknown): string {
  if (value == null) return "Not recorded";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
export function humanize(value: string): string {
  const names: Record<string, string> = {
    no_text_layer_or_cascade_off:
      "No text layer was available, or the text extraction cascade was disabled.",
    succeeded: "Completed",
    completed: "Completed",
    failed: "Failed",
    unknown: "Outcome unknown",
    pending: "Pending",
    running: "In progress",
    processing: "In progress",
    needs_review: "Needs review",
    PASS: "Passed",
    FAIL: "Failed",
    BLOCKED: "Blocked",
    NEEDS_REVIEW: "Needs review",
    NOT_APPLICABLE: "Not applicable",
    SUPPORTED: "Supported",
    CHALLENGED: "Challenged",
    UNCERTAIN: "Uncertain",
    NOT_REVIEWED: "Not reviewed",
    available: "Available",
    unavailable: "Unavailable",
    partial: "Partial",
    present: "Present",
    ambiguous: "Ambiguous",
  };
  return (
    names[value] ??
    value
      .replace(/[_-]/g, " ")
      .toLowerCase()
      .replace(/^./, (s) => s.toUpperCase())
  );
}
export function jobs(flow: Flujo): FlujoTrabajo[] {
  return Array.isArray(flow.extraccion?.trabajos)
    ? flow.extraccion.trabajos
    : [];
}
export function matchingDetail(
  flow: Flujo,
  detail?: Detail,
): Detail | undefined {
  const expectedVersion =
    flow.revision?.record_id ??
    flow.evaluacion?.record_id ??
    flow.identidad?.input_id;
  return detail &&
    detail.input_id === flow.identidad?.input_id &&
    detail.version === expectedVersion
    ? detail
    : undefined;
}
export function stageTime(
  flow: Flujo,
  name: string,
  detail?: Detail,
): string | null {
  const stored = flow.etapas?.find((stage) => stage.etapa === name)?.at;
  if (stored) return stored;
  if (name === "recibida") return flow.identidad?.recibida_at ?? null;
  if (name === "revisada") return flow.revision?.reviewed_at ?? null;
  // captured_at is evidence capture time, not the time a decision was made.
  if (name === "evaluada")
    return (
      matchingDetail(flow, detail)?.lifecycle.find(
        (stage) => stage.stage === "evaluated",
      )?.at ?? null
    );
  return null;
}
export function attempts(flow: Flujo) {
  return jobs(flow)
    .flatMap((job) =>
      (Array.isArray(job.intentos) ? job.intentos : []).map((attempt) => ({
        job,
        attempt,
      })),
    )
    .sort((a, b) => {
      const left = Date.parse(
        a.attempt.started_at ?? a.attempt.finished_at ?? "",
      );
      const right = Date.parse(
        b.attempt.started_at ?? b.attempt.finished_at ?? "",
      );
      return (
        (Number.isFinite(left) ? left : Infinity) -
          (Number.isFinite(right) ? right : Infinity) ||
        a.attempt.attempt_number - b.attempt.attempt_number
      );
    });
}
export const stageTitles: Record<string, string> = {
  original: "Original document",
  render: "Page rendering",
  reading: "Document reading",
  interpretation: "Invoice interpretation",
};
export function outputExplanation(flow: Flujo): string {
  const reasons: Record<string, string> = {
    not_processed:
      "No processing result is recorded. The invoice is routed for review.",
    no_evaluation:
      "There is no usable completed evaluation. The invoice is routed for review.",
    evaluator: "The recommendation follows the recorded rule evaluation.",
    evaluator_review_disabled:
      "The recommendation follows the rule evaluation. Contextual review was explicitly disabled for this run.",
    evaluator_confirmed_by_review:
      "The stored review contains no challenged rule or blocking finding that changes the evaluator’s recommendation.",
    review_challenged:
      "The review challenged a rule or recorded a blocking finding. The recommendation was changed to review.",
    review_unavailable:
      "The evaluator recommended payment, but a usable contextual review is missing. The recommendation is review.",
  };
  return (
    reasons[flow.salida.basis] ??
    `Recorded decision basis: ${flow.salida.basis}.`
  );
}
export function decisionTitle(value: string) {
  return (
    (
      {
        PAGAR: "Payment recommended",
        ESCALAR: "Review required",
        NO_PAGAR: "Do not pay",
      } as Record<string, string>
    )[value] ?? "No decision recorded"
  );
}
export function decisionTone(value: string) {
  return value === "PAGAR"
    ? "success"
    : value === "NO_PAGAR"
      ? "danger"
      : "attention";
}
