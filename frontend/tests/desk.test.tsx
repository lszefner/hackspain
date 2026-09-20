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
const { stoppedRunError } = require("../src/lib/desk/ingest-ui");

test("unknown runs stop polling when the engine is idle, preserving the cause", () => {
  assert.equal(stoppedRunError("unknown", false, "rules generation blocked", "run_interrupted"), "rules generation blocked");
  assert.equal(stoppedRunError("unknown", false, null, "run_interrupted"), "run_interrupted");
  assert.equal(stoppedRunError("unknown", true, null, "run_interrupted"), null);
  assert.equal(stoppedRunError("completed", false, "old error"), null);
});
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

test("collapsed evidence and rule inputs are not rendered until expanded", async () => {
  const {
    Disclosure,
    RuleResults,
  } = require("../src/components/desk/journey-evidence");
  let renders = 0;
  function HeavyEvidence() {
    renders++;
    return <p>Large evidence payload</p>;
  }
  mount(
    <>
      <Disclosure title="Saved technical record">
        <HeavyEvidence />
      </Disclosure>
      <RuleResults
        value={[
          {
            rule_id: "R_TEST",
            status: "VIOLATED",
            explanation: "Fixture rule failed",
            inputs: [{ field: "invoice.total", value: 99, state: "present" }],
          },
        ]}
      />
    </>,
  );
  assert.equal(renders, 0);
  assert.equal(screen.queryByRole("table"), null);
  const disclosure = screen
    .getByText("Saved technical record")
    .closest("details")!;
  disclosure.open = true;
  fireEvent(disclosure, new dom.window.Event("toggle"));
  await screen.findByText("Large evidence payload");
  assert.equal(renders, 1);
  const rule = screen.getByText("R_TEST").closest("details")!;
  assert.ok(rule.querySelector(".journey-rule-dot.danger"));
  rule.open = true;
  fireEvent(rule, new dom.window.Event("toggle"));
  await screen.findByRole("table");
});

test("uploaded invoice proposes email and a typed yes calls the fake write tool", async () => {
  Object.assign(globalThis, {
    getComputedStyle: window.getComputedStyle.bind(window),
    ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
    requestAnimationFrame: (cb: FrameRequestCallback) => setTimeout(() => cb(performance.now()), 16),
    cancelAnimationFrame: (id: ReturnType<typeof setTimeout>) => clearTimeout(id),
  });
  const { AgentPage } = require("../src/components/desk/agent");
  const draft = {
    file_id: "customer.pdf", vendor: "Customer", intent: "review_result",
    intent_label: "Invoice review result", why: "Duplicate invoice",
    to: "customer@example.com", subject: "Review of invoice INV-1",
    body: "Please review the duplicate invoice finding.", demo: true,
  };
  const writes: unknown[] = [];
  let polls = 0;
  const observedStages: string[] = [];
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url === "/api/summary") return Response.json({ total: 0, totals: [] });
    if (url === "/api/ingest") return Response.json({ ok: true, file_id: draft.file_id, request_key: "demo-run" });
    if (url.includes("/ejecucion/")) {
      polls++;
      if (polls > 1) observedStages.push(document.querySelector(".run-path-title")?.textContent || "");
      return Response.json({ state: polls < 5 ? "running" : "completed" });
    }
    if (url.endsWith("/flujo")) return Response.json({ etapas: polls === 3 ? [
      { etapa: "recibida", estado: "hecha" },
      { etapa: "extraida", estado: "hecha" },
      { etapa: "evaluada", estado: "pendiente" },
      { etapa: "emitida", estado: "pendiente" },
    ] : [] });
    if (url.endsWith("/estado")) return Response.json({ procesando: false });
    if (url.startsWith("/api/invoice-panel")) return Response.json({ said: "NO_PAGAR: duplicate invoice", panels: [] });
    if (url.startsWith("/api/outreach")) return Response.json({ needed: true, draft });
    if (url === "/api/write-email") {
      writes.push(JSON.parse(String(init?.body)));
      return Response.json({ tool: "write_email", status: "written", draft, sent: false, demo: true });
    }
    throw new Error(`Unexpected request: ${url}`);
  };
  const view = render(<AgentPage onOpenInvoice={() => {}} />);
  const fileInput = view.container.querySelector('input[type="file"][accept*="pdf"]');
  assert.ok(fileInput);
  fireEvent.change(fileInput, { target: { files: [new File(["%PDF-demo"], draft.file_id, { type: "application/pdf" })] } });
  await waitFor(() => assert.equal(screen.getByRole("button", { name: "Send", exact: true }).disabled, false), { timeout: 4000 });
  fireEvent.click(screen.getByRole("button", { name: "Send", exact: true }));
  await waitFor(() => assert.match(view.container.textContent || "", /Would you like me to write an email/), { timeout: 10000 });
  assert.deepEqual(observedStages, ["Receiving the file", "Receiving the file", "Evaluating rules", "Evaluating rules"]);
  assert.equal(writes.length, 0);
  assert.equal(screen.queryByRole("button", { name: /approve|reject/i }), null);
  fireEvent.change(screen.getByRole("textbox", { name: "Message the desk" }), { target: { value: "yes" } });
  fireEvent.click(screen.getByRole("button", { name: "Send", exact: true }));
  await waitFor(() => assert.match(view.container.textContent || "", /Email sent to customer@example.com/));
  assert.deepEqual(writes, [{ file: "customer.pdf" }]);
  assert.doesNotMatch(view.container.textContent || "", /demo|simulat|SMTP|no email is sent|nothing was sent|write_email/i);
});
