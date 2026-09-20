import type { Detail } from "./types";

export type OutreachIntent =
  | "clarify_tax_id"
  | "clarify_amount"
  | "clarify_currency"
  | "request_po"
  | "confirm_iban"
  | "clarify_supplier";

export type OutreachDraft = {
  file_id: string;
  vendor: string;
  intent: OutreachIntent;
  intent_label: string;
  why: string;
  to: string;
  subject: string;
  body: string;
  demo: true;
};

const INTENT_LABEL: Record<OutreachIntent, string> = {
  clarify_tax_id: "Wrong or missing tax ID",
  clarify_amount: "Amount does not match",
  clarify_currency: "Currency missing or unclear",
  request_po: "Purchase order missing",
  confirm_iban: "Bank account to confirm",
  clarify_supplier: "Supplier identity unclear",
};

const FAIL_STATUSES = new Set([
  "FAIL",
  "VIOLATED",
  "NEEDS_REVIEW",
  "BLOCKED",
  "ERROR",
  "UNSUPPORTED",
]);

function textOf(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function failedChecks(detail: Detail) {
  return (detail.checks || []).filter((check) => {
    const status = textOf(check.status || check.verdict).toUpperCase();
    return FAIL_STATUSES.has(status);
  });
}

function evidenceBlob(detail: Detail): string {
  const bits = [
    detail.row?.reason,
    detail.salida?.basis,
    textOf(detail.error),
    ...failedChecks(detail).flatMap((c) => [
      textOf(c.rule_id),
      textOf(c.status),
      textOf(c.explanation || c.reason),
    ]),
    JSON.stringify(detail.invoice ?? {}),
    JSON.stringify(detail.evidence ?? {}),
  ];
  return bits.join("\n").toLowerCase();
}

function pickIntent(blob: string): { intent: OutreachIntent; why: string } | null {
  // Order matters: more specific supplier asks first.
  if (
    /(invoice\.iban|supplier\.iban|iban)/.test(blob) &&
    /(mismatch|does not match|not the|diferente|no es el|unusable|missing|unverif)/.test(
      blob,
    )
  ) {
    return {
      intent: "confirm_iban",
      why: "The bank account on the invoice does not line up with the master record.",
    };
  }
  if (
    /(tax_id|nif|vat|cif|supplier_tax)/.test(blob) &&
    /(missing|unusable|mismatch|wrong|invalid|ambiguous|unverif|not recorded)/.test(
      blob,
    )
  ) {
    return {
      intent: "clarify_tax_id",
      why: "The supplier tax ID (NIF/CIF) is missing, ambiguous, or does not match.",
    };
  }
  if (
    /(purchase_order|pedido|po\b|order\.identity)/.test(blob) &&
    /(missing|absent|unusable|not present|no purchase order)/.test(blob)
  ) {
    return {
      intent: "request_po",
      why: "A purchase order reference is missing or could not be matched.",
    };
  }
  if (
    /(amount|importe|total|pedido_importe|arithmetic)/.test(blob) &&
    /(mismatch|does not match|delta|diferencia|not match|unusable|fail)/.test(blob)
  ) {
    return {
      intent: "clarify_amount",
      why: "The invoice amount does not match the expected order or arithmetic checks.",
    };
  }
  if (
    /(currency|divisa|moneda)/.test(blob) &&
    /(missing|unknown|unusable|not recorded|absent)/.test(blob)
  ) {
    return {
      intent: "clarify_currency",
      why: "The currency is missing or could not be verified.",
    };
  }
  if (
    /(supplier\.id|supplier\.identity|vendor|r_vendor|supplier_identity)/.test(
      blob,
    ) &&
    /(ambiguous|missing|unusable|unverif|not found|unidentified)/.test(blob)
  ) {
    return {
      intent: "clarify_supplier",
      why: "The supplier identity on the invoice is ambiguous or incomplete.",
    };
  }
  return null;
}

function field(detail: Detail, ...keys: string[]): string {
  const invoice = (detail.invoice || {}) as Record<string, unknown>;
  const supplier = (invoice.supplier || {}) as Record<string, unknown>;
  for (const key of keys) {
    if (key.startsWith("supplier.") && supplier[key.slice(9)] != null)
      return textOf(supplier[key.slice(9)]);
    if (invoice[key] != null) return textOf(invoice[key]);
    if ((detail.row as Record<string, unknown> | undefined)?.[key] != null)
      return textOf((detail.row as Record<string, unknown>)[key]);
  }
  return "";
}

function defaultRecipient(detail: Detail): string {
  const override = process.env.DESK_EMAIL_TO?.trim();
  if (override) return override;
  const fromInvoice =
    field(detail, "vendor_email", "supplier.email", "email") || "";
  return fromInvoice;
}

function buildBody(
  intent: OutreachIntent,
  detail: Detail,
  why: string,
): { subject: string; body: string } {
  const vendor = detail.row?.vendor || "supplier";
  const number = detail.row?.number || detail.file_id;
  const total = detail.row?.total != null ? String(detail.row.total) : "—";
  const currency = detail.row?.currency || "currency not recorded";
  const nif = field(detail, "nif", "tax_id", "supplier.tax_id") || "not recorded";
  const po =
    field(detail, "purchase_order_reference", "pedido", "order") ||
    "not recorded";

  switch (intent) {
    case "clarify_tax_id":
      return {
        subject: `Tax ID check — invoice ${number}`,
        body:
          `Hello ${vendor},\n\n` +
          `We paused invoice ${number} because the tax ID on the document looks wrong or incomplete ` +
          `(recorded value: ${nif}).\n\n` +
          `Could you send the correct tax ID, or a corrected invoice?\n\n` +
          `Accounts payable`,
      };
    case "clarify_amount":
      return {
        subject: `Amount clarification — invoice ${number}`,
        body:
          `Hello ${vendor},\n\n` +
          `We paused invoice ${number} (${total} ${currency}) because the amount does not match our records.\n\n` +
          `${why}\n\n` +
          `Please confirm the correct total or send a corrected invoice.\n\n` +
          `Accounts payable`,
      };
    case "clarify_currency":
      return {
        subject: `Currency missing — invoice ${number}`,
        body:
          `Hello ${vendor},\n\n` +
          `Invoice ${number} does not carry a usable currency. We cannot post it as-is.\n\n` +
          `Please confirm the currency or resend the invoice with it stated clearly.\n\n` +
          `Accounts payable`,
      };
    case "request_po":
      return {
        subject: `Purchase order needed — invoice ${number}`,
        body:
          `Hello ${vendor},\n\n` +
          `Invoice ${number} (${total} ${currency}) has no usable purchase order reference ` +
          `(recorded: ${po}).\n\n` +
          `Please send the PO number this invoice should be booked against.\n\n` +
          `Accounts payable`,
      };
    case "confirm_iban":
      return {
        subject: `Bank account confirmation — invoice ${number}`,
        body:
          `Hello ${vendor},\n\n` +
          `We paused invoice ${number} because the bank account on the invoice does not match the account we hold for you.\n\n` +
          `Please confirm the correct IBAN in writing before we proceed.\n\n` +
          `Accounts payable`,
      };
    case "clarify_supplier":
    default:
      return {
        subject: `Supplier details — invoice ${number}`,
        body:
          `Hello ${vendor},\n\n` +
          `We paused invoice ${number} because we could not fully identify the supplier on the document.\n\n` +
          `${why}\n\n` +
          `Please confirm your legal name and tax ID, or send a clearer copy.\n\n` +
          `Accounts payable`,
      };
  }
}

/** Decide whether this invoice warrants a supplier email, and draft it. */
export function classifyOutreach(detail: Detail): OutreachDraft | null {
  // Clean pay recommendations with no failed checks: no outreach.
  const failed = failedChecks(detail);
  const verdict = detail.salida?.verdict || detail.row?.verdict;
  if (verdict === "PAGAR" && failed.length === 0 && !detail.attention_required) {
    return null;
  }

  // Pure duplicates / do-not-pay without a supplier-fixable field issue: skip.
  const blob = evidenceBlob(detail);
  if (
    /(duplicate|duplicad|same invoice number)/.test(blob) &&
    !(/(tax_id|nif|iban|amount|currency|purchase_order|pedido)/.test(blob) &&
      /(missing|mismatch|unusable|wrong)/.test(blob))
  ) {
    return null;
  }

  const picked = pickIntent(blob);
  if (!picked) return null;

  const { subject, body } = buildBody(picked.intent, detail, picked.why);
  return {
    file_id: detail.file_id,
    vendor: detail.row?.vendor || "Supplier not recorded",
    intent: picked.intent,
    intent_label: INTENT_LABEL[picked.intent],
    why: picked.why,
    to: defaultRecipient(detail),
    subject,
    body,
    demo: true,
  };
}
