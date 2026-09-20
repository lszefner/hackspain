import { test } from "node:test";
import assert from "node:assert/strict";
import { classifyOutreach } from "../src/lib/desk/outreach";
import { emailReply, writeEmail } from "../src/lib/desk/write-email";
import type { Detail } from "../src/lib/desk/types";

function baseDetail(over: Partial<Detail> = {}): Detail {
  return {
    file_id: "inv.pdf",
    row: {
      file_id: "inv.pdf",
      vendor: "Acme",
      number: "INV-1",
      date: "2026-09-01",
      total: 121,
      currency: "EUR",
      lifecycle: "processed",
      review_status: null,
      verdict: "ESCALAR",
      reason: null,
      attention_required: true,
    },
    salida: { verdict: "ESCALAR", basis: "evaluator" },
    attention_required: true,
    pdf_available: true,
    lifecycle: [],
    checks: [],
    review: null,
    invoice: {
      invoice_number: "INV-1",
      supplier: { name: "Acme", tax_id: "B12345678" },
    },
    evidence: null,
    evaluation: null,
    ruleset: null,
    error: null,
    ...over,
  };
}

test("tax id issues draft a clarify_tax_id email", () => {
  const draft = classifyOutreach(
    baseDetail({
      checks: [
        {
          rule_id: "R_VENDOR",
          status: "NEEDS_REVIEW",
          explanation:
            "Required evidence is missing: invoice.supplier_tax_id, supplier.tax_id",
        },
      ],
    }),
  );
  assert.ok(draft);
  assert.equal(draft?.intent, "clarify_tax_id");
  assert.match(draft?.subject || "", /Tax ID/i);
});

test("amount mismatch drafts clarify_amount", () => {
  const draft = classifyOutreach(
    baseDetail({
      checks: [
        {
          rule_id: "R_AMOUNT",
          status: "VIOLATED",
          explanation: "Invoice total does not match pedido_importe; delta 40",
        },
      ],
    }),
  );
  assert.equal(draft?.intent, "clarify_amount");
});

test("clean PAGAR with no failures needs no email", () => {
  const draft = classifyOutreach(
    baseDetail({
      salida: { verdict: "PAGAR", basis: "evaluator" },
      row: {
        ...baseDetail().row,
        verdict: "PAGAR",
        attention_required: false,
      },
      attention_required: false,
      checks: [{ rule_id: "R_VENDOR", status: "PASS", explanation: "ok" }],
    }),
  );
  assert.equal(draft, null);
});

test("duplicate-only do-not-pay offers a review email without claiming payment", () => {
  const draft = classifyOutreach(
    baseDetail({
      salida: { verdict: "NO_PAGAR", basis: "evaluator" },
      checks: [
        {
          rule_id: "R_DUPLICATES",
          status: "VIOLATED",
          explanation: "Duplicate invoice number from the same supplier",
        },
      ],
    }),
  );
  assert.equal(draft?.intent, "review_result");
  assert.match(draft?.body || "", /Duplicate invoice number/);
  assert.doesNotMatch(draft?.body || "", /already paid/i);
});


test("all escalations offer an email without inventing a reason or recipient", () => {
  const draft = classifyOutreach(baseDetail());
  assert.equal(draft?.intent, "review_result");
  assert.equal(draft?.to, "");
  assert.match(draft?.body || "", /No detailed reason was recorded/);
});


test("write_email uses recorded recipient and never sends", () => {
  const detail = baseDetail({ invoice: { supplier: { email: "customer@example.com" } } });
  const draft = classifyOutreach(detail)!;
  const result = writeEmail(draft);
  assert.equal(result.status, "written");
  if (result.status === "written") {
    assert.equal(result.draft.to, "customer@example.com");
    assert.equal(result.sent, false);
    assert.equal(result.demo, true);
  }
});

test("write_email requires an address and accepts the user's supplied recipient", () => {
  const draft = classifyOutreach(baseDetail())!;
  assert.equal(writeEmail(draft).status, "recipient_required");
  assert.equal(writeEmail(draft, "not an email").status, "recipient_required");
  const result = writeEmail(draft, "customer@example.com");
  assert.equal(result.status, "written");
  if (result.status === "written") assert.equal(result.draft.to, "customer@example.com");
});

test("email replies support explicit messages and leave unrelated questions alone", () => {
  for (const text of ["yes", "Yes please!", "write the email", "sí", "adelante"]) {
    assert.equal(emailReply(text)?.action, "write", text);
  }
  assert.deepEqual(emailReply("to customer@example.com"), { action: "write", recipient: "customer@example.com" });
  assert.equal(emailReply("no thanks")?.action, "cancel");
  assert.equal(emailReply("do not write the email"), null);
  assert.equal(emailReply("why was it rejected?"), null);
});
