export type Verdict = "PAGAR" | "ESCALAR" | "NO_PAGAR";
export type Invoice = {
  file_id: string;
  vendor: string;
  number: string | null;
  date: string | null;
  total: number | string | null;
  currency: string | null;
  lifecycle: string;
  review_status: string | null;
  verdict: Verdict;
  reason: string | null;
  attention_required: boolean;
};
export type Supplier = {
  id: string;
  name: string;
  currency: string;
  count: number;
  pay_n: number;
  review_n: number;
  nopay_n: number;
  pay_total: number | null;
  review_total: number | null;
  nopay_total: number | null;
  missing_amounts: number;
};
export type Lanes = {
  counts: Partial<Record<"PAY" | "ESCALATE" | "DO NOT PAY", number>>;
  grand: number;
  matched: number;
  supplier_count: number;
  lanes: Supplier[];
  page: number;
  limit: number;
};
export type Invoices = {
  grand: number;
  matched: number;
  rows: Invoice[];
  page: number;
  limit: number;
};
export type Summary = {
  total: number;
  suppliers: number;
  lifecycle: Record<string, number>;
  totals: {
    currency: string;
    verdict: Verdict;
    n: number;
    total: number | null;
    missing_amounts: number;
  }[];
};
export type Detail = {
  input_id?: string;
  version?: string;
  file_id: string;
  row: Invoice;
  salida: { verdict: Verdict; basis: string };
  attention_required: boolean;
  pdf_available: boolean;
  lifecycle: { stage: string; state: string | null; at: string | null }[];
  checks: {
    rule_id: string;
    status: string;
    explanation: string;
    [key: string]: unknown;
  }[];
  review: { status?: string; [key: string]: unknown } | null;
  invoice: unknown;
  evidence: unknown;
  evaluation: unknown;
  ruleset: unknown;
  error: unknown;
};
export const actions = [
  ["", "All"],
  ["PAGAR", "Recommend pay"],
  ["ESCALAR", "Review"],
  ["NO_PAGAR", "Do not pay"],
] as const;
export function actionName(value: string) {
  return actions.find(([key]) => key === value)?.[1] ?? "Not evaluated";
}
export function actionClass(value: string) {
  return (
    (
      { PAGAR: "PAY", ESCALAR: "ESCALATE", NO_PAGAR: "DONOTPAY" } as Record<
        string,
        string
      >
    )[value] ?? "ESCALATE"
  );
}
export function money(value: number | string | null, currency: string | null) {
  if (value == null) return "Amount not recorded";
  const amount = Number(value);
  if (!currency || currency === "UNKNOWN")
    return new Intl.NumberFormat("en-GB", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount);
  try {
    return new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency,
    }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
}
