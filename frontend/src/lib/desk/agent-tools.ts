import { deskQuery } from "./backend";

export const invoiceTools = [
  { type: "function", function: { name: "search_invoices", description: "Search persisted invoices by supplier, invoice number or filename; filter by recommendation or processing stage. Returns a bounded page and total count, not the whole dataset.", parameters: { type: "object", properties: { q: { type: "string" }, action: { type: "string", enum: ["PAGAR", "ESCALAR", "NO_PAGAR"] }, lifecycle: { type: "string", enum: ["processed", "extracted", "processing", "error"] }, page: { type: "integer", minimum: 1 } }, additionalProperties: false } } },
  { type: "function", function: { name: "get_invoice", description: "Get one recorded invoice's recommendation, exact rule statuses, reasons and review limitations. Use an exact file_id returned by search.", parameters: { type: "object", properties: { file_id: { type: "string" } }, required: ["file_id"], additionalProperties: false } } },
  { type: "function", function: { name: "get_rule_evidence", description: "Get recorded evidence and trace for a particular rule on an invoice.", parameters: { type: "object", properties: { file_id: { type: "string" }, rule_id: { type: "string" } }, required: ["file_id", "rule_id"], additionalProperties: false } } },
  { type: "function", function: { name: "invoice_totals", description: "Get aggregate counts, processing stages and recommendation totals grouped by currency across all persisted invoices.", parameters: { type: "object", properties: {}, additionalProperties: false } } },
];

export async function runInvoiceTool(name: string, args: Record<string, unknown>) {
  if (name === "invoice_totals") return deskQuery("summary");
  if (name === "search_invoices") {
    const params = new URLSearchParams({ limit: "25" });
    for (const key of ["q", "action", "lifecycle", "page"]) {
      const value = args[key];
      if (typeof value === "string" || typeof value === "number") params.set(key, String(value).slice(0, 200));
    }
    return deskQuery("invoices", params);
  }
  if (name !== "get_invoice" && name !== "get_rule_evidence") throw new Error("Unknown read tool");
  const file = args.file_id;
  if (typeof file !== "string" || !file || file.length > 255 || /[/\\]/.test(file)) throw new Error("Invalid invoice identifier");
  const detail = await deskQuery("invoice", new URLSearchParams({ file }));
  if (name === "get_rule_evidence") {
    const check = detail.checks.find((item: { rule_id: string }) => item.rule_id === args.rule_id);
    return { file_id: file, version: detail.version, rule: check ?? null, error: check ? null : "rule_not_recorded" };
  }
  return { file_id: file, version: detail.version, row: detail.row, salida: detail.salida,
    attention_required: detail.attention_required, lifecycle: detail.lifecycle,
    rule_counts: detail.rule_counts, checks: detail.checks.map((check: Record<string, unknown>) => ({
      rule_id: check.rule_id, status: check.status, explanation: check.explanation, evidence_refs: check.evidence_refs,
    })), review: detail.review, error: detail.error };
}

export function boundedEvidence(value: unknown) {
  const text = JSON.stringify(value);
  return text.length <= 32_000 ? text : JSON.stringify({ incomplete: true, reason: "tool_result_exceeds_budget", retained_excerpt: text.slice(0, 30_000), instruction: "Narrow the query. Do not claim this is complete evidence." });
}
