/* eslint-disable @typescript-eslint/no-require-imports */
import { JSDOM } from "jsdom";
import React from "react";
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost/",
});
Object.assign(globalThis, {
  window: dom.window,
  self: dom.window,
  document: dom.window.document,
  HTMLElement: dom.window.HTMLElement,
  HTMLCanvasElement: dom.window.HTMLCanvasElement,
  MutationObserver: dom.window.MutationObserver,
  React,
});
Object.defineProperty(globalThis, "navigator", {
  value: dom.window.navigator,
  configurable: true,
});
window.matchMedia = () =>
  ({
    matches: true,
    addEventListener() {},
    removeEventListener() {},
  }) as unknown as MediaQueryList;
HTMLCanvasElement.prototype.getContext = (() =>
  null) as typeof HTMLCanvasElement.prototype.getContext;
const {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} = require("@testing-library/react");
const { SWRConfig } = require("swr");
const {
  SearchParamsContext,
} = require("next/dist/shared/lib/hooks-client-context.shared-runtime");
const { InvoicePage } = require("../src/components/desk/invoices");
const { SummaryPage } = require("../src/components/desk/summary");
const { money } = require("../src/lib/desk/types");
afterEach(cleanup);
function mount(children: React.ReactNode) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <SearchParamsContext.Provider value={new URLSearchParams()}>
        {children}
      </SearchParamsContext.Provider>
    </SWRConfig>,
  );
}
test("supplier rows load on expansion, apply filters, and select an invoice", async () => {
  const calls: string[] = [];
  let selected = "";
  globalThis.fetch = async (input) => {
    const url = String(input);
    calls.push(url);
    return Response.json(
      url.startsWith("/api/lanes")
        ? {
            grand: 30,
            matched: 1,
            supplier_count: 1,
            lanes: [
              {
                id: "supplier-test",
                name: "Contract fixture supplier",
                currency: "EUR",
                count: 1,
                pay_n: 0,
                review_n: 1,
                nopay_n: 0,
                pay_total: 0,
                review_total: 123,
                nopay_total: 0,
                missing_amounts: 0,
              },
            ],
          }
        : {
            matched: 1,
            rows: [
              {
                file_id: "contract-fixture.pdf",
                number: "TEST-1",
                date: "2026-09-19",
                total: 123,
                currency: "EUR",
                verdict: "ESCALAR",
                lifecycle: "processed",
                reason: "Missing approval",
                attention_required: true,
              },
            ],
          },
    );
  };
  mount(
    <InvoicePage
      onSelect={(file: string) => {
        selected = file;
      }}
    />,
  );
  await screen.findByText("Contract fixture supplier");
  assert.equal(calls.length, 1);
  fireEvent.click(
    screen.getByRole("button", { name: /Contract fixture supplier/ }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: /contract-fixture.pdf/ }),
  );
  assert.equal(selected, "contract-fixture.pdf");
  assert.match(calls[1], /vendor=supplier-test/);
  fireEvent.change(screen.getByRole("combobox"), {
    target: { value: "error" },
  });
  await waitFor(() =>
    assert.ok(calls.some((url) => url.includes("lifecycle=error"))),
  );
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "invoice & supplier" },
  });
  await waitFor(() =>
    assert.ok(
      calls.some(
        (url) =>
          new URL(url, "http://local").searchParams.get("q") ===
          "invoice & supplier",
      ),
    ),
  );
});
test("summary keeps unknown currencies separate and never labels recommendations paid", async () => {
  globalThis.fetch = async () =>
    Response.json({
      total: 3,
      suppliers: 2,
      lifecycle: { processed: 2, error: 1 },
      totals: [
        {
          currency: "EUR",
          verdict: "PAGAR",
          n: 2,
          total: 100,
          missing_amounts: 0,
        },
        {
          currency: "UNKNOWN",
          verdict: "ESCALAR",
          n: 1,
          total: null,
          missing_amounts: 1,
        },
      ],
    });
  mount(<SummaryPage />);
  await screen.findByText("Not aggregated");
  assert.ok(screen.getByText("Recommend pay · EUR"));
  assert.ok(screen.getByText(/Resolution and payment are not recorded/));
  assert.equal(
    screen.getByRole("link", { name: "error" }).getAttribute("href"),
    "/?view=invoices&stage=error",
  );
  assert.equal(money(null, "EUR"), "Amount not recorded");
  assert.match(money(12, null), /currency not recorded/);
});
test("API failure offers a working retry without fabricated rows", async () => {
  let count = 0;
  globalThis.fetch = async () =>
    ++count === 1
      ? Response.json({ error: "offline" }, { status: 503 })
      : Response.json({ total: 0, suppliers: 0, lifecycle: {}, totals: [] });
  mount(<SummaryPage />);
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByText("No invoices recorded yet.");
  assert.equal(count, 2);
});

const { Dossier } = require("../src/components/desk/dossier");
const {
  stageTime,
  attempts,
  matchingDetail,
} = require("../src/lib/desk/journey");
const flowFixture = require("../../docs/examples/backend-api/factura.flujo.json");
const failedFixture = require("../../docs/examples/backend-api/factura.flujo.extraction-failed.json");

test("invoice opens the audit trail immediately and preserves missing timestamps", async () => {
  const calls: string[] = [];
  const flow = structuredClone(flowFixture);
  flow.revision = { record_id: null, status: "DISABLED" };
  const detail = {
    input_id: flow.identidad.input_id,
    version: flow.evaluacion.record_id,
    pdf_available: true,
    lifecycle: [{ stage: "evaluated", at: "2026-09-19T20:02:00Z" }],
  };
  globalThis.fetch = async (input) => {
    calls.push(String(input));
    return Response.json(String(input).includes("/flujo") ? flow : detail);
  };
  let closed = false;
  mount(
    <Dossier
      file="invoice.pdf"
      onClose={() => {
        closed = true;
      }}
    />,
  );
  await screen.findByRole("heading", { name: "Invoice journey" });
  assert.equal(calls.length, 2);
  assert.ok(calls.some((url) => url.endsWith("/flujo")));
  assert.ok(
    screen.getByRole("heading", { name: "Contextual review was disabled" }),
  );
  assert.ok(
    screen.getByText("No reviewer decision was made.", { exact: false }),
  );
  assert.equal(
    screen.queryByRole("button", { name: /Load processing attempts/ }),
    null,
  );
  assert.equal(document.querySelector("iframe"), null);
  await screen.findByRole("link", { name: /Open original PDF/ });
  assert.equal(
    screen
      .getByRole("link", { name: /Open original PDF/ })
      .getAttribute("href"),
    "/api/pdf?file=invoice.pdf",
  );
  assert.ok(
    screen.getByText(
      "No payment execution or confirmation is recorded in this system.",
    ),
  );
  assert.equal(stageTime(flow, "evaluada"), null); // Never substitute the evidence capture date.
  assert.equal(stageTime(flow, "evaluada", detail), "2026-09-19T20:02:00Z");
  assert.equal(
    matchingDetail(flow, { ...detail, input_id: "another-input" }),
    undefined,
  );
  fireEvent.click(screen.getByRole("button", { name: "All invoices" }));
  assert.equal(closed, true);
});
test("failed extraction and missing audit sections remain visible without an invented decision", async () => {
  const flow = structuredClone(failedFixture);
  flow.extraccion.trabajos = { error: { code: "archive_unavailable" } };
  globalThis.fetch = async (input) =>
    String(input).includes("/flujo")
      ? Response.json(flow)
      : Response.json({ error: "offline" }, { status: 503 });
  mount(<Dossier file="failed.pdf" onClose={() => {}} />);
  await screen.findByRole("heading", {
    name: "Extraction could not be completed",
  });
  assert.ok(
    screen.getByRole("heading", { name: "Rule evaluation not recorded" }),
  );
  assert.ok(
    screen.getByText(/processing log could not be read: archive_unavailable/),
  );
  assert.ok(
    screen.getByText(
      /No rule decision is recorded after the extraction failure/,
    ),
  );
  assert.equal(screen.queryByRole("link", { name: /Open original PDF/ }), null);
});
test("attempts are ordered by their saved timestamps and preserve retries and failures", () => {
  const flow = structuredClone(flowFixture);
  const job = flow.extraccion.trabajos[0];
  job.intentos = [
    {
      ...job.intentos[0],
      attempt_number: 2,
      started_at: "2026-09-19T22:05:00+02:00",
      status: "succeeded",
    },
    {
      ...job.intentos[0],
      attempt_number: 1,
      started_at: "2026-09-19T22:00:00+02:00",
      status: "failed",
      error: { code: "timeout" },
    },
  ];
  const events = attempts(flow);
  assert.equal(events[0].attempt.status, "failed");
  assert.equal(events[events.length - 1].attempt.attempt_number, 2);
  assert.equal(events.length, 3);
});
