import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { buildPanelBundle } from "../src/lib/desk/panels";

function invoices(action: string, rows: unknown[]) {
  return {
    grand: rows.length,
    matched: rows.length,
    rows,
    page: 1,
    limit: 25,
  };
}

test("buildPanelBundle queue panel uses live invoice shape", async () => {
  const rows = [
    {
      file_id: "a.pdf",
      vendor: "Acme",
      number: "1",
      date: "2026-01-01",
      total: 100,
      currency: "EUR",
      lifecycle: "processed",
      review_status: "COMPLETED",
      verdict: "ESCALAR",
      reason: "missing order",
      attention_required: true,
    },
    {
      file_id: "b.pdf",
      vendor: "Acme",
      number: "2",
      date: "2026-01-02",
      total: 50,
      currency: "EUR",
      lifecycle: "processed",
      review_status: "COMPLETED",
      verdict: "ESCALAR",
      reason: "missing order",
      attention_required: true,
    },
  ];
  mock.method(globalThis, "fetch", async (input: RequestInfo) => {
    const url = String(input);
    assert.match(url, /\/api\/ui\/invoices/);
    assert.match(url, /action=ESCALAR/);
    return Response.json(invoices("ESCALAR", rows));
  });
  try {
    const bundle = await buildPanelBundle("what needs my judgement");
    assert.equal(bundle.intent, "queue");
    assert.equal(bundle.panels.length, 1);
    assert.equal(bundle.panels[0].id, "queue");
    assert.equal(bundle.panels[0].rows.length, 1);
    assert.equal(bundle.panels[0].rows[0].title, "Acme");
    assert.equal(bundle.panels[0].rows[0].lead, "2");
    assert.ok(bundle.panels[0].actions?.some((a) => a.ui === "invoices"));
    assert.ok(!bundle.panels[0].rows[0].actions?.some((a) => a.act === "approve" || a.act === "reject"));
  } finally {
    mock.restoreAll();
  }
});

test("buildPanelBundle report panel from summary", async () => {
  mock.method(globalThis, "fetch", async () =>
    Response.json({
      total: 10,
      suppliers: 3,
      lifecycle: { processed: 8, error: 2 },
      totals: [
        { currency: "EUR", verdict: "PAGAR", n: 4, total: 400, missing_amounts: 0 },
        { currency: "EUR", verdict: "ESCALAR", n: 3, total: 120, missing_amounts: 0 },
        { currency: "EUR", verdict: "NO_PAGAR", n: 1, total: 9, missing_amounts: 0 },
      ],
    }),
  );
  try {
    const bundle = await buildPanelBundle("day report please");
    assert.equal(bundle.intent, "report");
    assert.equal(bundle.panels[0].id, "report");
    assert.ok(bundle.panels[0].rows.some((r) => r.title === "Needs your judgement"));
  } finally {
    mock.restoreAll();
  }
});
