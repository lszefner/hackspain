import { fold } from "./fold";
import { deskQuery } from "./backend";
import {
  actionClass,
  money,
  type Detail,
  type Invoice,
  type Invoices,
  type Summary,
} from "./types";

export type PanelAction = {
  label: string;
  kind?: "primary" | "danger" | "quiet";
  act?: string;
  key?: string;
  ask?: string;
  ui?: "first" | "invoices";
  /** Open this invoice in the dossier without leaving via Invoices. */
  file?: string;
  href?: string;
  title?: string;
};

export type PanelInvoiceRow = {
  cells: [string, string, string, string, string];
  file_id: string;
  fields?: { label: string; value: string }[];
};

export type PanelRow = {
  key: string;
  lead?: string;
  title: string;
  sub?: string;
  value?: string;
  tone?: "warn" | "good";
  state?: "unseen" | "seen" | "deferred";
  verdict?: string;
  detail?: string;
  table?: { rows: PanelInvoiceRow[] };
  actions?: PanelAction[];
};

export type DeskPanel = {
  id: string;
  title: string;
  email?: { to: string; subject: string; body: string };
  meta?: string;
  rows: PanelRow[];
  actions?: PanelAction[];
};

export type PanelBundle = {
  panels: DeskPanel[];
  facts: Record<string, unknown>;
  intent: string | null;
};

const INTENTS: [string, string[]][] = [
  [
    "payments",
    [
      "pay",
      "payment",
      "sepa",
      "remesa",
      "transfer",
      "settle",
      "remittance",
      "pag",
      "abonar",
      "girar",
      "ready to pay",
    ],
  ],
  [
    "queue",
    [
      "review",
      "judgement",
      "judgment",
      "needs me",
      "need me",
      "escalat",
      "attention",
      "criterio",
      "decide",
      "approve",
      "pending",
      "escalad",
      "revis",
      "pendient",
      "aprob",
      "que tengo",
      "me toca",
    ],
  ],
  [
    "report",
    [
      "report",
      "inform",
      "informe",
      "close",
      "closing",
      "summary",
      "resumen",
      "brief",
      "how did it go",
      "how it went",
      "cierre",
      "como ha ido",
      "como fue",
      "balance",
    ],
  ],
  [
    "blocked",
    [
      "duplicate",
      "twice",
      "double",
      "repeated",
      "duplicad",
      "repetid",
      "dos veces",
      "do not pay",
      "blocked",
      "stop",
      "nopagar",
      "no pagar",
      "rechaz",
    ],
  ],
  ["rules", ["rule", "ruleset", "norma", "policy", "regla", "politica"]],
  [
    "waiting",
    [
      "waiting",
      "chase",
      "chasing",
      "owes us",
      "outstanding",
      "esperando",
      "reclamar",
      "nos deb",
    ],
  ],
  [
    "outreach",
    [
      "email supplier",
      "email suppliers",
      "write to supplier",
      "write to suppliers",
      "correo",
      "notify supplier",
      "notify suppliers",
      "message the supplier",
      "ask the supplier",
    ],
  ],
];

const FILE_RE = /([\w./\-\u00c0-\u024f]+\.pdf)/i;
const STUB = "Not connected yet — nothing will move";

function stubAct(label: string, act: string, kind?: PanelAction["kind"], key?: string): PanelAction {
  return { label, act, kind, key, title: STUB };
}

export function routeIntent(text: string): string | null {
  const t = fold(text);
  // Prefer explicit stop language over the substring "pay" inside "do not pay".
  if (
    t.includes("do not pay") ||
    t.includes("no pagar") ||
    t.includes("nopagar") ||
    t.includes("donotpay")
  ) {
    return "blocked";
  }
  const scored = INTENTS.map(([name, keys], i) => ({
    name,
    score: keys.reduce((n, k) => n + (t.includes(k) ? 1 : 0), 0),
    order: i,
  }));
  const best = scored.reduce((a, b) =>
    b.score > a.score || (b.score === a.score && b.order < a.order) ? b : a,
  );
  return best.score > 0 ? best.name : null;
}

export function namedFileHint(text: string): string | null {
  const m = FILE_RE.exec(text || "");
  if (!m) return null;
  const base = m[1].split(/[/\\]/).pop() || m[1];
  return base.length <= 255 ? base : null;
}

function sumTotals(rows: Invoice[]) {
  const byCurrency = new Map<string, { n: number; total: number; missing: number }>();
  for (const row of rows) {
    const currency = row.currency || "UNKNOWN";
    const slot = byCurrency.get(currency) || { n: 0, total: 0, missing: 0 };
    slot.n += 1;
    if (row.total == null) slot.missing += 1;
    else slot.total += Number(row.total);
    byCurrency.set(currency, slot);
  }
  return [...byCurrency.entries()].map(([currency, slot]) => ({
    currency,
    n: slot.n,
    total: currency === "UNKNOWN" ? null : slot.total,
    missing: slot.missing,
  }));
}

function metaMoney(rows: Invoice[]) {
  const parts = sumTotals(rows).map((slot) => {
    if (slot.total == null) return `${slot.n} · currency not recorded`;
    return `${slot.n} · ${money(slot.total, slot.currency)}`;
  });
  return parts.join(" · ") || "0 invoices";
}

function clusterByVendor(rows: Invoice[]) {
  const map = new Map<string, Invoice[]>();
  for (const row of rows) {
    const key = `${row.vendor}\u0000${row.currency || "UNKNOWN"}`;
    const list = map.get(key) || [];
    list.push(row);
    map.set(key, list);
  }
  return [...map.entries()].map(([key, invoices]) => {
    const [vendor] = key.split("\u0000");
    const currency = invoices[0]?.currency || null;
    const total = invoices.every((i) => i.total != null)
      ? invoices.reduce((s, i) => s + Number(i.total), 0)
      : null;
    return { vendor, currency, invoices, total };
  });
}

function invoiceTable(invoices: Invoice[]): PanelInvoiceRow[] {
  return invoices.map((inv) => ({
    file_id: inv.file_id,
    cells: [
      inv.file_id,
      inv.number || "—",
      inv.date || "—",
      money(inv.total, inv.currency),
      inv.reason || inv.lifecycle,
    ],
    fields: [
      { label: "File", value: inv.file_id },
      { label: "Number", value: inv.number || "Not recorded" },
      { label: "Date", value: inv.date || "Not recorded" },
      { label: "Amount", value: money(inv.total, inv.currency) },
      { label: "Lifecycle", value: inv.lifecycle },
      { label: "Reason", value: inv.reason || "Not recorded" },
    ],
  }));
}

async function fetchInvoices(action: string): Promise<Invoices> {
  const params = new URLSearchParams({
    action,
    lifecycle: "processed",
    limit: "25",
    page: "1",
  });
  return deskQuery("invoices", params);
}

function clusterActions(key: string, verdict: string): PanelAction[] {
  if (verdict === "ESCALAR") {
    return [
      stubAct("Defer", "defer", "quiet", key),
    ];
  }
  if (verdict === "PAGAR") {
    return [];
  }
  return [];
}

function buildClusterPanel(
  id: string,
  title: string,
  invoices: Invoice[],
  matched: number,
  verdict: string,
  foot: PanelAction[],
): DeskPanel {
  const clusters = clusterByVendor(invoices);
  return {
    id,
    title,
    meta: `${matched} matched · showing ${invoices.length} · ${metaMoney(invoices)}`,
    rows: clusters.map((c) => {
      const key = `cluster:${c.vendor}:${c.currency || "UNKNOWN"}`;
      return {
        key,
        lead: String(c.invoices.length),
        title: c.vendor,
        sub: c.currency && c.currency !== "UNKNOWN" ? c.currency : "currency not recorded",
        value: money(c.total, c.currency),
        state: "unseen" as const,
        verdict: actionClass(verdict),
        detail:
          c.invoices.find((i) => i.reason)?.reason ||
          "Recorded recommendation only — not an approval or payment.",
        table: { rows: invoiceTable(c.invoices) },
        actions: clusterActions(key, verdict),
      };
    }),
    actions: foot,
  };
}

async function buildQueue(): Promise<{ panel: DeskPanel; facts: Record<string, unknown> }> {
  const data = await fetchInvoices("ESCALAR");
  const panel = buildClusterPanel(
    "queue",
    "Needs your judgement",
    data.rows,
    data.matched,
    "ESCALAR",
    [
      { label: "Open the first one", kind: "primary", ui: "first" },
      { label: `See all ${data.matched} in Invoices`, ui: "invoices" },
    ],
  );
  return {
    panel,
    facts: {
      panel: "queue",
      matched: data.matched,
      shown: data.rows.length,
      by_currency: sumTotals(data.rows),
      suppliers: clusterByVendor(data.rows).map((c) => ({
        vendor: c.vendor,
        n: c.invoices.length,
        currency: c.currency,
        total: c.total,
      })),
    },
  };
}

async function buildPayments(): Promise<{ panel: DeskPanel; facts: Record<string, unknown> }> {
  const data = await fetchInvoices("PAGAR");
  const panel = buildClusterPanel(
    "payments",
    "Ready to pay today",
    data.rows,
    data.matched,
    "PAGAR",
    [
      stubAct("Download the SEPA file", "sepa"),
      { label: "What needs my judgement?", ask: "what needs my judgement" },
    ],
  );
  return {
    panel,
    facts: {
      panel: "payments",
      matched: data.matched,
      shown: data.rows.length,
      by_currency: sumTotals(data.rows),
      note: "Recommendation PAGAR only. Payment is not connected.",
    },
  };
}

async function buildBlocked(): Promise<{ panel: DeskPanel; facts: Record<string, unknown> }> {
  const data = await fetchInvoices("NO_PAGAR");
  const panel = buildClusterPanel(
    "blocked",
    "Do not pay",
    data.rows,
    data.matched,
    "NO_PAGAR",
    [
      { label: "See the rules", ask: "what rules are running" },
    ],
  );
  return {
    panel,
    facts: {
      panel: "blocked",
      matched: data.matched,
      shown: data.rows.length,
      by_currency: sumTotals(data.rows),
      note: "Recommendation NO_PAGAR. Supplier email drafts are simulated.",
    },
  };
}

async function buildReport(): Promise<{ panel: DeskPanel; facts: Record<string, unknown> }> {
  const summary = (await deskQuery("summary")) as Summary;
  const today = new Date().toISOString().slice(0, 10);
  const byVerdict = (verdict: string) =>
    summary.totals.filter((t) => t.verdict === verdict);
  const line = (verdict: string, title: string, tone?: "warn" | "good"): PanelRow => {
    const slots = byVerdict(verdict);
    const n = slots.reduce((s, t) => s + t.n, 0);
    const value =
      slots
        .map((t) => money(t.total, t.currency === "UNKNOWN" ? null : t.currency))
        .join(" · ") || "—";
    return {
      key: verdict,
      title,
      sub: `${n} invoices`,
      value,
      tone,
    };
  };
  const panel: DeskPanel = {
    id: "report",
    title: `${today} · day numbers`,
    meta: `${summary.total} recorded · ${summary.suppliers} suppliers`,
    rows: [
      {
        key: "processed",
        title: "Processed",
        sub: "Has an evaluation",
        value: String(summary.lifecycle.processed ?? 0),
      },
      line("PAGAR", "Recommend pay", "good"),
      line("ESCALAR", "Needs your judgement", "warn"),
      line("NO_PAGAR", "Do not pay"),
      {
        key: "stages",
        title: "Other stages",
        sub: Object.entries(summary.lifecycle)
          .filter(([k]) => k !== "processed")
          .map(([k, n]) => `${k} ${n}`)
          .join(" · ") || "none",
        value: String(summary.total),
      },
    ],
    actions: [
      stubAct("Send to Management", "report", "primary"),
      { label: "What needs my judgement?", ask: "what needs my judgement" },
    ],
  };
  return {
    panel,
    facts: {
      panel: "report",
      total: summary.total,
      suppliers: summary.suppliers,
      lifecycle: summary.lifecycle,
      totals: summary.totals,
      note: "Counts are recommendations from recorded evaluations, not payments.",
    },
  };
}

async function buildRules(): Promise<{ panel: DeskPanel; facts: Record<string, unknown> }> {
  const data = await deskQuery("rules");
  const rules = Array.isArray(data.rules) ? data.rules : [];
  const byRule = new Map<
    string,
    { rule_id: string; statuses: { status: string; n: number }[]; ruleset: unknown }
  >();
  for (const item of rules) {
    const id = String(item.rule_id || "unknown");
    const slot = byRule.get(id) ?? {
      rule_id: id,
      statuses: [] as { status: string; n: number }[],
      ruleset: item.ruleset,
    };
    slot.statuses.push({ status: String(item.status || "?"), n: Number(item.n) || 0 });
    byRule.set(id, slot);
  }
  const panel: DeskPanel = {
    id: "rules",
    title: "Rules in recorded evaluations",
    meta: `${data.checked ?? 0} invoices checked · ${byRule.size} rule ids`,
    rows: [...byRule.values()].slice(0, 40).map((r) => ({
      key: r.rule_id,
      title: r.rule_id,
      sub: r.statuses.map((s) => `${s.status} ${s.n}`).join(" · "),
      value: String(r.statuses.reduce((n, s) => n + s.n, 0)),
      detail: "Statuses come from saved evaluations. Rules cannot be mutated from this desk yet.",
    })),
    actions: [{ label: "What needs my judgement?", ask: "what needs my judgement" }],
  };
  return {
    panel,
    facts: {
      panel: "rules",
      checked: data.checked ?? 0,
      rule_ids: [...byRule.keys()],
      note: "Read-only snapshot of recorded rule outcomes.",
    },
  };
}

function buildWaiting(): { panel: DeskPanel; facts: Record<string, unknown> } {
  const panel: DeskPanel = {
    id: "waiting",
    title: "Waiting on someone else",
    meta: "not recorded",
    rows: [
      {
        key: "none",
        title: "No chase queue on live data",
        sub: "Deferrals and supplier follow-ups are not stored yet",
        value: "—",
        detail: "Ask for escalations instead; those come from recorded recommendations.",
      },
    ],
    actions: [
      { label: "What needs my judgement?", kind: "primary", ask: "what needs my judgement" },
      stubAct("Chase them", "chase"),
    ],
  };
  return {
    panel,
    facts: {
      panel: "waiting",
      recorded: false,
      note: "Waiting/chase is not connected to the live invoice store.",
    },
  };
}

export async function buildDossierPanel(fileId: string): Promise<{ panel: DeskPanel; facts: Record<string, unknown> } | null> {
  let detail: Detail;
  try {
    detail = await deskQuery("invoice", new URLSearchParams({ file: fileId }));
  } catch {
    return null;
  }
  const verdict = detail.salida?.verdict || "ESCALAR";
  const vendor = detail.row?.vendor || "Supplier not recorded";
  const amount = money(detail.row?.total ?? null, detail.row?.currency ?? null);
  const checks = (detail.checks || []).slice(0, 12);
  const panel: DeskPanel = {
    id: "dossier",
    title: fileId,
    meta: `${actionClass(verdict)} · ${amount}`,
    rows: [
      {
        key: fileId,
        title: vendor,
        sub: detail.row?.number || "number not recorded",
        value: amount,
        verdict: actionClass(verdict),
        state: "unseen",
        detail: detail.salida?.basis || detail.row?.reason || "Recorded recommendation only.",
        table: {
          rows: [
            {
              file_id: fileId,
              cells: [
                fileId,
                detail.row?.number || "—",
                detail.row?.date || "—",
                amount,
                detail.lifecycle?.find((s) => s.stage === "reviewed")?.state || detail.row?.lifecycle || "—",
              ],
              fields: [
                { label: "Verdict", value: verdict },
                { label: "Basis", value: detail.salida?.basis || "Not recorded" },
                { label: "Review", value: detail.review?.status || detail.row?.review_status || "Not recorded" },
                {
                  label: "Attention",
                  value: detail.attention_required ? "required" : "not flagged",
                },
              ],
            },
          ],
        },
        actions: [
          { label: "Open full history", file: fileId },
        ],
      },
      ...checks.map((check) => ({
        key: `${fileId}:${check.rule_id}`,
        title: check.rule_id,
        sub: check.explanation || "No explanation recorded",
        value: check.status,
      })),
    ],
    actions: [
      { label: "Open full history", kind: "primary", file: fileId },
    ],
  };
  return {
    panel,
    facts: {
      panel: "dossier",
      file_id: fileId,
      vendor,
      verdict,
      basis: detail.salida?.basis,
      amount,
      currency: detail.row?.currency,
      attention_required: detail.attention_required,
      rule_statuses: checks.map((c) => ({ rule_id: c.rule_id, status: c.status })),
      note: "Recommendation only. Approval and payment are not connected.",
    },
  };
}

export async function resolveNamedFile(text: string): Promise<string | null> {
  const hint = namedFileHint(text);
  if (!hint) return null;
  try {
    await deskQuery("invoice", new URLSearchParams({ file: hint }));
    return hint;
  } catch {
    /* fall through to search */
  }
  try {
    const found = (await deskQuery(
      "invoices",
      new URLSearchParams({ q: hint, limit: "5", page: "1" }),
    )) as Invoices;
    const exact = found.rows.find((r) => r.file_id === hint);
    return exact?.file_id || found.rows[0]?.file_id || null;
  } catch {
    return null;
  }
}

export async function buildPanelBundle(text: string): Promise<PanelBundle> {
  const named = await resolveNamedFile(text);
  if (named) {
    const dossier = await buildDossierPanel(named);
    if (dossier) {
      return { panels: [dossier.panel], facts: dossier.facts, intent: "dossier" };
    }
  }

  const intent = routeIntent(text);
  if (!intent) return { panels: [], facts: {}, intent: null };

  try {
    if (intent === "queue") {
      const built = await buildQueue();
      return { panels: [built.panel], facts: built.facts, intent };
    }
    if (intent === "payments") {
      const built = await buildPayments();
      return { panels: [built.panel], facts: built.facts, intent };
    }
    if (intent === "blocked") {
      const built = await buildBlocked();
      return { panels: [built.panel], facts: built.facts, intent };
    }
    if (intent === "report") {
      const built = await buildReport();
      return { panels: [built.panel], facts: built.facts, intent };
    }
    if (intent === "rules") {
      const built = await buildRules();
      return { panels: [built.panel], facts: built.facts, intent };
    }
    if (intent === "waiting") {
      const built = buildWaiting();
      return { panels: [built.panel], facts: built.facts, intent };
    }
  } catch (error) {
    return {
      panels: [],
      facts: {
        error: "panel_query_unavailable",
        intent,
        detail: error instanceof Error ? error.message : "unknown",
      },
      intent,
    };
  }

  return { panels: [], facts: {}, intent };
}

export const FALLBACK_SAY: Record<string, string> = {
  queue:
    "These need your judgement. I only have recorded recommendations — nothing is paid until you say so.",
  payments:
    "These look clean against the rules. Payment itself is not connected yet.",
  blocked:
    "A rule stopped these. Nothing moved at the bank from this screen.",
  report: "Here are today's recorded numbers. Recommendations are not payments.",
  rules: "These are the rule outcomes saved on evaluated invoices.",
  waiting: "I do not keep a chase list on live data yet. Ask me what needs your judgement.",
  dossier: "I opened the recorded file. The verdict is a recommendation, not an approval.",
  none: "I could not attach a list. Try naming an invoice PDF or ask what needs your judgement.",
};

export function fallbackSay(intent: string | null, facts: Record<string, unknown>) {
  if (facts.error) {
    return "I could not load the live list. Check the API connection and try again. Nothing moved.";
  }
  return FALLBACK_SAY[intent || "none"] || FALLBACK_SAY.none;
}

export const PHRASE_SYSTEM = `You are the desk that works Alberto's accounts payable.
Alberto is the accounts payable manager. Live invoice recommendations are already on screen under your sentence.

HOW YOU TALK
- English. Plain, operational, first person, like a colleague handing over a shift.
- Say what you checked or would do first. Never call yourself an agent, assistant, AI or model. Never greet. Never offer further help.
- AT MOST TWO SENTENCES, under 45 words total. Shorter is better.
- No bullet points, no headings, no markdown, no emoji.

WHAT TO SAY
- A list is already on screen under your sentence. Do NOT restate totals, counts, or supplier-by-supplier rundowns.
- Say the one thing the list cannot: what to do first and why, or the single risk not to miss. Then stop.

HARD RULES
- Every figure, supplier, invoice id and date must come from the FACTS JSON. If it is not there, say you do not have it.
- Never invent, estimate or re-round a number.
- Never claim you paid, approved, deferred, uploaded, or changed a rule. Those actions are not connected.
- You may draft supplier emails from recorded issues. Sending from this desk is simulated — say so if you mention a send.
- Recommendations (PAGAR / ESCALAR / NO_PAGAR) are not payments or approvals.`;
