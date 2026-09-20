"use client";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { ThinkingOrb } from "thinking-orbs";
import type {
  DeskPanel,
  PanelAction,
  PanelRow,
} from "@/lib/desk/panels";
import { labelForEtapa } from "@/lib/desk/ingest-ui";
import { fold } from "@/lib/desk/fold";
import type { OutreachDraft } from "@/lib/desk/outreach";
import type { Ejecucion, Flujo } from "@/lib/engine/types";
import { EmailDraftDialog } from "./email-draft-dialog";

type HistoryItem = { role: "user" | "assistant"; content: string };
type RunStage = {
  id: string;
  label: string;
  state: "pending" | "hot" | "done";
  subtasks?: { id: string; label: string; state: "pending" | "hot" | "done" }[];
};
type ThreadMsg =
  | {
      id: string;
      kind: "user";
      text: string;
      fileName?: string;
      fileKind?: "pdf" | "zip";
    }
  | {
      id: string;
      kind: "desk";
      text: string;
      panels: DeskPanel[];
      meta?: string;
      undo?: boolean;
    }
  | { id: string; kind: "think"; label: string }
  | {
      id: string;
      kind: "run";
      fileId: string;
      title: string;
      status: "running" | "done" | "failed";
      stages: RunStage[];
    }
  | {
      id: string;
      kind: "batch";
      title: string;
      status: "running" | "done" | "failed";
      phase: string;
      items: {
        file_id: string;
        state: "pending" | "hot" | "done";
      }[];
    };

type StagedFile = {
  file: File;
  name: string;
  size: number;
  kind: "pdf" | "zip";
};

const BATCH_PHASES = ["Receiving", "Extracting", "Evaluating"] as const;

const CHIPS = [
  "What needs my judgement?",
  "Ready to pay today",
  "Do not pay",
  "Email suppliers that need a reply",
  "Day report",
  "What rules are running?",
];

const THINK_LABELS = [
  "Checking recorded invoices",
  "Reading recommendations",
  "Building the list",
];

const FALLBACK_RUN_LABELS = [
  "Receiving the file",
  "Extracting invoice fields",
  "Evaluating rules",
  "Writing the recommendation",
];

function now() {
  return new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function uid() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function isOutreachAsk(text: string) {
  const t = fold(text);
  if (t.includes("email suppliers that need")) return true;
  const wantsMail =
    t.includes("email") ||
    t.includes("correo") ||
    t.includes("write to") ||
    t.includes("notify supplier") ||
    t.includes("notify suppliers");
  const aboutSuppliers =
    t.includes("supplier") ||
    t.includes("suppliers") ||
    t.includes("proveedor") ||
    t.includes("proveedores");
  return wantsMail && aboutSuppliers;
}

function tokenise(text: string) {
  return text.split(/(\s+)/).filter((x) => x !== "");
}

function SayFade({ text }: { text: string }) {
  const parts = tokenise(text);
  return (
    <>
      {parts.map((part, i) =>
        /^\s+$/.test(part) ? (
          <span key={i}>{part}</span>
        ) : (
          <span
            key={i}
            className="w"
            style={{ animationDelay: `${Math.min(i, 40) * 28}ms` }}
          >
            {part}
          </span>
        ),
      )}
    </>
  );
}

function ActionButtons({
  actions,
  onAction,
  onAsk,
  onUi,
  onOpenFile,
}: {
  actions: PanelAction[];
  onAction: (act: PanelAction) => void;
  onAsk: (text: string) => void;
  onUi: (ui: "first" | "invoices") => void;
  onOpenFile: (file: string) => void;
}) {
  return (
    <>
      {actions.map((action, i) => {
        const className = `btn ${action.kind || ""}`.trim();
        if (action.file) {
          return (
            <button
              key={i}
              type="button"
              className={className}
              onClick={() => onOpenFile(action.file!)}
            >
              {action.label}
            </button>
          );
        }
        if (action.ask) {
          return (
            <button
              key={i}
              type="button"
              className={className}
              onClick={() => onAsk(action.ask!)}
            >
              {action.label}
            </button>
          );
        }
        if (action.ui) {
          return (
            <button
              key={i}
              type="button"
              className={className}
              onClick={() => onUi(action.ui!)}
            >
              {action.label}
            </button>
          );
        }
        if (action.href) {
          return (
            <a
              key={i}
              className={className}
              href={action.href}
              title={action.title}
            >
              {action.label}
            </a>
          );
        }
        return (
          <button
            key={i}
            type="button"
            className={className}
            title={action.title || "Not connected yet — nothing will move"}
            onClick={() => onAction(action)}
          >
            {action.label}
          </button>
        );
      })}
    </>
  );
}

function ClusterRow({
  row,
  onAction,
  onAsk,
  onUi,
  onOpenFile,
}: {
  row: PanelRow;
  onAction: (act: PanelAction) => void;
  onAsk: (text: string) => void;
  onUi: (ui: "first" | "invoices") => void;
  onOpenFile: (file: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [openFile, setOpenFile] = useState<string | null>(null);
  const expandable = Boolean(row.table || row.detail || row.actions?.length);
  return (
    <div className={`exp ${open ? "open" : ""}`} data-key={row.key}>
      <button
        type="button"
        className={`row ${expandable ? "tap" : ""} ${row.tone || ""}`}
        onClick={() => expandable && setOpen((v) => !v)}
      >
        <span className="lead">
          {row.state ? <i className={`dotk ${row.state}`} /> : null}
          {row.lead || ""}
        </span>
        <span className="mid">
          <span className="t">
            {row.title}
            {row.verdict ? <span className={`vd ${row.verdict}`}>{row.verdict}</span> : null}
          </span>
          {row.sub ? <span className="s">{row.sub}</span> : null}
        </span>
        <span className={`v ${row.tone || ""}`}>{row.value || ""}</span>
        <span className="chev" aria-hidden />
      </button>
      {expandable ? (
        <div className="drop">
          <div className="in">
            {row.detail ? <p>{row.detail}</p> : null}
            {row.table ? (
              <div className="tbl">
                <div className="th">
                  <span>File</span>
                  <span>Number</span>
                  <span>Date</span>
                  <span>Amount</span>
                  <span>Note</span>
                </div>
                {row.table.rows.map((inv) => (
                  <div
                    key={inv.file_id}
                    className={`frow ${openFile === inv.file_id ? "open" : ""}`}
                  >
                    <button
                      type="button"
                      className="tr"
                      onClick={() =>
                        setOpenFile((cur) =>
                          cur === inv.file_id ? null : inv.file_id,
                        )
                      }
                    >
                      <span className="c c1">{inv.cells[0]}</span>
                      <span className="c c2">{inv.cells[1]}</span>
                      <span className="c c3">{inv.cells[2]}</span>
                      <span className="c c4">{inv.cells[3]}</span>
                      <span className="c c5">{inv.cells[4]}</span>
                    </button>
                    {inv.fields ? (
                      <div className="fields">
                        <div className="in">
                          <dl>
                            {inv.fields.map((f) => (
                              <div key={f.label}>
                                <dt>{f.label}</dt>
                                <dd>{f.value}</dd>
                              </div>
                            ))}
                          </dl>
                          <div className="acts">
                            <button
                              type="button"
                              className="btn"
                              onClick={() => onOpenFile(inv.file_id)}
                            >
                              Open in Invoices
                            </button>
                          </div>
                        </div>
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : null}
            {row.actions?.length ? (
              <div className="acts">
                <ActionButtons
                  actions={row.actions}
                  onAction={onAction}
                  onAsk={onAsk}
                  onUi={onUi}
                  onOpenFile={onOpenFile}
                />
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function PanelView({
  panel,
  onAction,
  onAsk,
  onUi,
  onOpenFile,
}: {
  panel: DeskPanel;
  onAction: (act: PanelAction) => void;
  onAsk: (text: string) => void;
  onUi: (ui: "first" | "invoices", panelId: string) => void;
  onOpenFile: (file: string) => void;
}) {
  const [beam, setBeam] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => setBeam(false), 1600);
    return () => clearTimeout(t);
  }, []);
  return (
    <div
      className={`panel ${beam ? "beam" : "beam-out"}`}
      data-panel={panel.id}
    >
      <div className="panel-head">
        <h4>{panel.title}</h4>
        {panel.meta ? <span className="m">{panel.meta}</span> : null}
      </div>
      <div className="rows">
        {panel.rows.map((row) => (
          <ClusterRow
            key={row.key}
            row={row}
            onAction={onAction}
            onAsk={onAsk}
            onUi={(ui) => onUi(ui, panel.id)}
            onOpenFile={onOpenFile}
          />
        ))}
      </div>
      {panel.actions?.length ? (
        <div className="panel-foot">
          <ActionButtons
            actions={panel.actions}
            onAction={onAction}
            onAsk={onAsk}
            onUi={(ui) => onUi(ui, panel.id)}
            onOpenFile={onOpenFile}
          />
        </div>
      ) : null}
    </div>
  );
}

function jobDone(state: string | undefined) {
  return ["succeeded", "completed", "needs_review"].includes(state ?? "");
}

function jobHot(state: string | undefined) {
  return ["running", "processing", "pending", "queued"].includes(state ?? "");
}

function isVisionJob(provider: string | null | undefined, model: string | null | undefined) {
  const blob = `${provider ?? ""} ${model ?? ""}`.toLowerCase();
  return blob.includes("vision") || blob.includes("helmcode");
}

function extractionSubtasks(flujo: Flujo | null): RunStage["subtasks"] {
  const extraction = flujo?.extraccion;
  if (!extraction) return undefined;
  const jobs = Array.isArray(extraction.trabajos) ? extraction.trabajos : [];
  const reading = jobs.find((job) => job.stage === "reading");
  const interpretation = jobs.find((job) => job.stage === "interpretation");
  const subs: NonNullable<RunStage["subtasks"]> = [];

  if (reading) {
    const vision = isVisionJob(reading.provider, reading.model);
    const done = jobDone(reading.state);
    const hot = !done && (jobHot(reading.state) || reading.state !== "failed");
    subs.push({
      id: "reading",
      label: vision
        ? `Vision is reading the pages (${reading.model || reading.provider || "vision"}) — this takes longer`
        : `Reading the document text (${reading.provider || "native-text"})`,
      state: done ? "done" : hot ? "hot" : "pending",
    });
  } else if (
    extraction.ruta === "vision" ||
    (extraction.por_que_vision?.length ?? 0) > 0
  ) {
    subs.push({
      id: "vision",
      label: "Vision is needed for this PDF — reading pages can take longer",
      state: "hot",
    });
  }

  if (interpretation) {
    const done = jobDone(interpretation.state);
    const hot = !done && (jobHot(interpretation.state) || interpretation.state !== "failed");
    subs.push({
      id: "interpretation",
      label: `Interpreting invoice fields (${interpretation.provider || "model"})`,
      state: done ? "done" : hot ? "hot" : "pending",
    });
  } else if (reading && jobDone(reading.state)) {
    subs.push({
      id: "interpretation",
      label: "Interpreting invoice fields",
      state: "hot",
    });
  }

  return subs.length ? subs : undefined;
}

function stagesFromFlujo(flujo: Flujo | null): RunStage[] {
  if (!flujo?.etapas?.length) {
    return FALLBACK_RUN_LABELS.map((label, i) => ({
      id: `fallback-${i}`,
      label,
      state: i === 0 ? "hot" : "pending",
    }));
  }
  const visible = flujo.etapas.filter(
    (e) => e.etapa !== "resuelta" && e.etapa !== "pagada",
  );
  const subs = extractionSubtasks(flujo);
  let blocked = false;
  let hotAssigned = false;
  return visible.map((etapa) => {
    const hecha = etapa.estado === "hecha";
    let state: RunStage["state"] = "pending";
    if (!blocked && hecha) {
      state = "done";
    } else if (!blocked && !hotAssigned) {
      state = "hot";
      hotAssigned = true;
      blocked = true;
    } else {
      state = "pending";
      blocked = true;
    }
    return {
      id: etapa.etapa,
      label: labelForEtapa(etapa.etapa),
      state,
      subtasks: etapa.etapa === "extraida" ? subs : undefined,
    };
  });
}

export function AgentPage({
  active = true,
  onOpenInvoice,
}: {
  active?: boolean;
  onOpenInvoice: (file: string) => void;
}) {
  const [chatting, setChatting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ThreadMsg[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [staged, setStaged] = useState<StagedFile | null>(null);
  const [emailDraft, setEmailDraft] = useState<OutreachDraft | null>(null);
  const [emailOpen, setEmailOpen] = useState(false);
  const [pendingOutreach, setPendingOutreach] = useState<OutreachDraft[] | null>(
    null,
  );
  const [toast, setToast] = useState<string | null>(null);
  const [hello, setHello] = useState(
    "I worked the recorded invoices. Ask what needs your judgement, or attach one PDF.",
  );
  const [thinkLabel, setThinkLabel] = useState(THINK_LABELS[0]);
  const threadRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const runBusy = useRef(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const summary = await fetch("/api/summary", { cache: "no-store" }).then(
          (r) => (r.ok ? r.json() : null),
        );
        if (cancelled || !summary) return;
        const esc =
          summary.totals
            ?.filter((t: { verdict: string }) => t.verdict === "ESCALAR")
            .reduce((n: number, t: { n: number }) => n + t.n, 0) ?? 0;
        const pay =
          summary.totals
            ?.filter((t: { verdict: string }) => t.verdict === "PAGAR")
            .reduce((n: number, t: { n: number }) => n + t.n, 0) ?? 0;
        setHello(
          `${summary.total ?? 0} recorded · ${esc} need judgement · ${pay} recommend pay. Ask me for a list, or attach one PDF.`,
        );
      } catch {
        /* keep default */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, busy, pendingOutreach]);

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 4200);
    return () => clearTimeout(id);
  }, [toast]);

  useEffect(() => {
    if (!busy || runBusy.current) return;
    let i = 0;
    const id = setInterval(() => {
      i = (i + 1) % THINK_LABELS.length;
      setThinkLabel(THINK_LABELS[i]);
    }, 1900);
    return () => clearInterval(id);
  }, [busy]);

  const pushDesk = useCallback(
    (text: string, panels: DeskPanel[] = [], meta?: string, undo?: boolean) => {
      setMessages((prev) => [
        ...prev.filter((m) => m.kind !== "think" && m.kind !== "run"),
        { id: uid(), kind: "desk", text, panels, meta, undo },
      ]);
    },
    [],
  );

  const showToast = useCallback((message: string) => {
    setToast(message);
  }, []);

  const openSingleDraft = useCallback(async (fileId: string) => {
    try {
      const res = await fetch(
        `/api/outreach?file=${encodeURIComponent(fileId)}`,
        { cache: "no-store" },
      );
      const data = (await res.json().catch(() => ({}))) as {
        needed?: boolean;
        draft?: OutreachDraft;
        reason?: string;
        error?: string;
      };
      if (!res.ok) {
        pushDesk(
          `I could not build an email draft for ${fileId}. ${data.error || "Try again."}`,
        );
        return;
      }
      if (!data.needed || !data.draft) {
        pushDesk(
          data.reason ||
            `Nothing on ${fileId} looks like a supplier email would fix it.`,
        );
        return;
      }
      setEmailDraft(data.draft);
      setEmailOpen(true);
    } catch {
      pushDesk(`I could not reach outreach for ${fileId}. Nothing moved.`);
    }
  }, [pushDesk]);

  const proposeOutreach = useCallback(
    async (files?: string[]) => {
      try {
        const res = await fetch("/api/outreach", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(files?.length ? { files } : { limit: 25 }),
        });
        const data = (await res.json().catch(() => ({}))) as {
          drafts?: OutreachDraft[];
          error?: string;
        };
        if (!res.ok) {
          pushDesk(
            data.error ||
              "I could not scan invoices for supplier outreach. Nothing moved.",
          );
          return;
        }
        const drafts = Array.isArray(data.drafts) ? data.drafts : [];
        if (!drafts.length) {
          pushDesk(
            "I checked the escalated invoices. None of them have a clear supplier-fixable issue for an email — I will not invent one.",
          );
          setPendingOutreach(null);
          return;
        }
        if (drafts.length === 1) {
          setPendingOutreach(null);
          setEmailDraft(drafts[0]);
          setEmailOpen(true);
          pushDesk(
            `I drafted one supplier email for ${drafts[0].file_id} (${drafts[0].intent_label}). Review it in the dialog — send is simulated.`,
          );
          return;
        }
        setPendingOutreach(drafts);
        const lines = drafts
          .slice(0, 8)
          .map(
            (d) =>
              `• ${d.file_id} → ${d.to || "recipient not recorded"} · ${d.intent_label}`,
          )
          .join("\n");
        const more =
          drafts.length > 8 ? `\n• and ${drafts.length - 8} more` : "";
        pushDesk(
          `I would write ${drafts.length} supplier emails for issues a message can fix — not every escalation. Confirm to simulate sending them:\n${lines}${more}`,
        );
      } catch {
        pushDesk("Outreach scan failed. Nothing moved.");
      }
    },
    [pushDesk],
  );

  const confirmPendingOutreach = useCallback(() => {
    if (!pendingOutreach?.length) return;
    const drafts = pendingOutreach;
    setPendingOutreach(null);
    const recipients = [
      ...new Set(drafts.map((d) => d.to || "recipient not recorded")),
    ];
    showToast(
      `Demo send · ${drafts.length} message${drafts.length === 1 ? "" : "s"} marked sent`,
    );
    pushDesk(
      `I marked ${drafts.length} supplier emails as sent (demo only — nothing left over SMTP). Recipients: ${recipients.slice(0, 5).join(", ")}${recipients.length > 5 ? ` and ${recipients.length - 5} more` : ""}. Intents covered: ${[...new Set(drafts.map((d) => d.intent_label))].join("; ")}.`,
      [],
      "demo send",
    );
  }, [pendingOutreach, pushDesk, showToast]);

  const sendSingleDraft = useCallback(
    (draft: OutreachDraft) => {
      setEmailOpen(false);
      setEmailDraft(null);
      showToast(`Demo send · message to ${draft.to || "recipient"} marked sent`);
      pushDesk(
        `I wrote to ${draft.to || "the supplier"} about ${draft.intent_label.toLowerCase()} on ${draft.file_id}. This desk does not send real SMTP — the send is simulated.`,
        [],
        "demo send",
      );
    },
    [pushDesk, showToast],
  );

  const filesForActionKey = useCallback(
    (key?: string) => {
      if (!key) return [] as string[];
      if (key.startsWith("file:")) return [key.slice(5)];
      const panels = messages
        .filter(
          (m): m is Extract<ThreadMsg, { kind: "desk" }> => m.kind === "desk",
        )
        .flatMap((m) => m.panels);
      for (const panel of panels) {
        for (const row of panel.rows) {
          if (row.key === key && row.table?.rows?.length) {
            return row.table.rows.map((r) => r.file_id);
          }
        }
      }
      return [];
    },
    [messages],
  );

  const runAction = useCallback(
    async (action: PanelAction) => {
      if (!action.act) return;
      if (action.act === "email") {
        const files = filesForActionKey(action.key);
        if (files.length === 1) {
          await openSingleDraft(files[0]);
          return;
        }
        if (files.length > 1) {
          await proposeOutreach(files);
          return;
        }
        await proposeOutreach();
        return;
      }
      try {
        const res = await fetch("/api/action", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            act: action.act,
            key: action.key,
          }),
        });
        const data = await res.json().catch(() => ({}));
        const said =
          typeof data.said === "string"
            ? data.said
            : "No action was taken. Nothing moved.";
        pushDesk(said, [], undefined, Boolean(data.undo));
      } catch {
        pushDesk("Nothing moved after all. The action request failed.", []);
      }
    },
    [filesForActionKey, openSingleDraft, proposeOutreach, pushDesk],
  );

  const handleUi = useCallback(
    (ui: "first" | "invoices", panelId: string) => {
      if (ui === "invoices") {
        const panel = messages
          .filter(
            (m): m is Extract<ThreadMsg, { kind: "desk" }> => m.kind === "desk",
          )
          .flatMap((m) => m.panels)
          .find((p) => p.id === panelId);
        const file =
          panel?.rows
            .flatMap((r) => r.table?.rows || [])
            .map((r) => r.file_id)[0] || null;
        if (file) onOpenInvoice(file);
        else {
          const next = new URLSearchParams(window.location.search);
          next.set("view", "invoices");
          next.delete("invoice");
          window.history.pushState(null, "", `/?${next}`);
        }
        return;
      }
      if (ui === "first") {
        const first = document.querySelector(
          `.panel[data-panel="${panelId}"] .exp`,
        );
        first?.classList.add("open");
        first?.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    },
    [messages, onOpenInvoice],
  );

  const pollRun = useCallback(
    async (fileId: string, requestKey: string, runId: string) => {
      const started = Date.now();
      let fallbackTick = 0;
      let missingEjecucionPolls = 0;

      const failRun = (title: string, said: string) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === runId && m.kind === "run"
              ? { ...m, status: "failed" as const, title }
              : m,
          ),
        );
        pushDesk(said);
      };

      for (;;) {
        if (Date.now() - started > 15 * 60 * 1000) {
          failRun(
            "Timed out waiting for the engine",
            `I staged ${fileId}, but the engine did not finish in time. Check make api and try again.`,
          );
          return;
        }

        let ejecucion: Ejecucion | null = null;
        let flujo: Flujo | null = null;
        let engineBusy = true;
        let engineError: string | null = null;
        try {
          const [ejRes, flRes, stRes] = await Promise.all([
            fetch(`/api/engine/ejecucion/${encodeURIComponent(requestKey)}`, {
              cache: "no-store",
            }),
            fetch(`/api/engine/factura/${encodeURIComponent(fileId)}/flujo`, {
              cache: "no-store",
            }),
            fetch("/api/engine/estado", { cache: "no-store" }),
          ]);
          if (ejRes.ok) ejecucion = (await ejRes.json()) as Ejecucion;
          if (flRes.ok) flujo = (await flRes.json()) as Flujo;
          if (stRes.ok) {
            const estado = (await stRes.json()) as {
              procesando?: boolean;
              error?: string | null;
            };
            engineBusy = Boolean(estado.procesando);
            engineError =
              typeof estado.error === "string" && estado.error
                ? estado.error
                : null;
          }
        } catch {
          /* keep polling */
        }

        if (!ejecucion) {
          missingEjecucionPolls += 1;
          // lanzar accepted, but the background thread never wrote this run
          if (
            !engineBusy &&
            engineError &&
            Date.now() - started > 2500 &&
            missingEjecucionPolls >= 2
          ) {
            const hint = /ValueError|evaluation date|REVISION_EVALUATION_DATE/i.test(
              engineError,
            )
              ? " Check REVISION_EVALUATION_DATE in .env, then restart make api."
              : " Check the engine logs and restart make api if needed.";
            failRun(
              "Engine failed to start the run",
              `I staged ${fileId}, but the engine did not start this run (${engineError}).${hint} Nothing was approved or paid.`,
            );
            return;
          }
        } else {
          missingEjecucionPolls = 0;
        }

        const state = ejecucion?.state ?? "running";
        let stages = stagesFromFlujo(flujo);
        if (!flujo?.etapas?.length) {
          fallbackTick += 1;
          stages = FALLBACK_RUN_LABELS.map((label, i) => ({
            id: `fallback-${i}`,
            label,
            state:
              i < Math.min(fallbackTick, FALLBACK_RUN_LABELS.length - 1)
                ? "done"
                : i === Math.min(fallbackTick, FALLBACK_RUN_LABELS.length - 1)
                  ? "hot"
                  : "pending",
          }));
        }

        const hot = stages.find((s) => s.state === "hot");
        const hotSub = hot?.subtasks?.find((s) => s.state === "hot");
        const liveLabel = hotSub?.label || hot?.label || "Working the invoice";
        if (hot || hotSub) setThinkLabel(liveLabel.split(" — ")[0]);

        if (state === "running" || state === "unknown" || !ejecucion) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === runId && m.kind === "run"
                ? {
                    ...m,
                    stages,
                    title: liveLabel,
                    status: "running",
                  }
                : m,
            ),
          );
          await new Promise((r) => setTimeout(r, 1500));
          continue;
        }

        if (state === "failed") {
          failRun(
            "Processing failed",
            `I could not finish ${fileId}. ${ejecucion.error || "The engine reported a failure."} Nothing was approved or paid.`,
          );
          return;
        }

        // completed or partial — stay in Agent with summary card
        setMessages((prev) =>
          prev.map((m) =>
            m.id === runId && m.kind === "run"
              ? {
                  ...m,
                  status: "done",
                  title:
                    state === "partial"
                      ? "Finished with gaps"
                      : "Processing complete",
                  stages: stages.map((s) => ({ ...s, state: "done" as const })),
                }
              : m,
          ),
        );

        try {
          const panelRes = await fetch(
            `/api/invoice-panel?file=${encodeURIComponent(fileId)}`,
            { cache: "no-store" },
          );
          if (panelRes.ok) {
            const data = (await panelRes.json()) as {
              said?: string;
              panels?: DeskPanel[];
            };
            pushDesk(
              data.said ||
                `I finished ${fileId}. Open the summary below — approval and payment are not connected.`,
              Array.isArray(data.panels) ? data.panels : [],
            );
            try {
              const outreachRes = await fetch(
                `/api/outreach?file=${encodeURIComponent(fileId)}`,
                { cache: "no-store" },
              );
              if (outreachRes.ok) {
                const outreach = (await outreachRes.json()) as {
                  needed?: boolean;
                  draft?: OutreachDraft;
                };
                if (outreach.needed && outreach.draft) {
                  setPendingOutreach(null);
                  setEmailDraft(outreach.draft);
                  pushDesk(
                    `This one may need a supplier email (${outreach.draft.intent_label}). Say if you want the draft — send stays simulated.`,
                    [
                      {
                        id: `outreach-nudge-${fileId}`,
                        title: fileId,
                        meta: outreach.draft.intent_label,
                        rows: [
                          {
                            key: fileId,
                            title: outreach.draft.vendor,
                            sub: outreach.draft.why,
                            value: outreach.draft.to || "no recipient",
                            actions: [
                              {
                                label: "Review email draft",
                                kind: "primary",
                                act: "email",
                                key: `file:${fileId}`,
                              },
                            ],
                          },
                        ],
                      },
                    ],
                  );
                }
              }
            } catch {
              /* nudge is optional */
            }
          } else {
            pushDesk(
              `I finished processing ${fileId}, but the summary card is not ready yet. Open it from Invoices.`,
              [],
            );
          }
        } catch {
          pushDesk(
            `I finished processing ${fileId}, but I could not load the summary card.`,
            [],
          );
        }
        return;
      }
    },
    [pushDesk],
  );

  const runInvoice = useCallback(
    async (file: File, note: string) => {
      if (busy) return;
      setChatting(true);
      setBusy(true);
      runBusy.current = true;
      setStaged(null);
      setInput("");

      const userText = note.trim() || `Process ${file.name}`;
      const runId = uid();
      setMessages((prev) => [
        ...prev,
        {
          id: uid(),
          kind: "user",
          text: userText,
          fileName: file.name,
          fileKind: "pdf",
        },
        {
          id: runId,
          kind: "run",
          fileId: file.name,
          title: "Sending to the engine",
          status: "running",
          stages: FALLBACK_RUN_LABELS.map((label, i) => ({
            id: `fallback-${i}`,
            label,
            state: i === 0 ? "hot" : "pending",
          })),
        },
      ]);
      setThinkLabel("Sending to the engine");

      try {
        const body = new FormData();
        body.set("file", file);
        const res = await fetch("/api/ingest", { method: "POST", body });
        const data = (await res.json().catch(() => ({}))) as {
          ok?: boolean;
          error?: string;
          file_id?: string;
          request_key?: string;
          staged?: boolean;
        };

        if (!res.ok || !data.ok || !data.file_id || !data.request_key) {
          const reason =
            data.error === "engine_busy"
              ? "The engine is already processing another invoice."
              : data.error === "backend_unavailable"
                ? "The engine API is not reachable on this machine."
                : data.staged
                  ? `I saved ${data.file_id || file.name}, but could not start processing (${data.error || res.status}).`
                  : `I could not start processing (${data.error || res.status}).`;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === runId && m.kind === "run"
                ? { ...m, status: "failed", title: "Could not start" }
                : m,
            ),
          );
          pushDesk(reason);
          return;
        }

        setMessages((prev) =>
          prev.map((m) =>
            m.id === runId && m.kind === "run"
              ? {
                  ...m,
                  fileId: data.file_id!,
                  title: "Engine is working",
                }
              : m,
          ),
        );
        await pollRun(data.file_id, data.request_key, runId);
      } catch {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === runId && m.kind === "run"
              ? { ...m, status: "failed", title: "Upload failed" }
              : m,
          ),
        );
        pushDesk(
          "The upload did not reach the desk. Nothing was saved or processed.",
        );
      } finally {
        runBusy.current = false;
        setBusy(false);
        inputRef.current?.focus();
      }
    },
    [busy, pollRun, pushDesk],
  );

  const runZipBatch = useCallback(
    async (file: File, note: string) => {
      if (busy) return;
      setChatting(true);
      setBusy(true);
      runBusy.current = true;
      setStaged(null);
      setInput("");

      const userText = note.trim() || `Process ${file.name}`;
      const batchId = uid();
      setMessages((prev) => [
        ...prev,
        {
          id: uid(),
          kind: "user",
          text: userText,
          fileName: file.name,
          fileKind: "zip",
        },
        {
          id: batchId,
          kind: "batch",
          title: "Opening the zip",
          status: "running",
          phase: "Staging PDFs on disk",
          items: [],
        },
      ]);

      try {
        const body = new FormData();
        body.set("file", file);
        const res = await fetch("/api/ingest-zip", { method: "POST", body });
        const data = (await res.json().catch(() => ({}))) as {
          ok?: boolean;
          error?: string;
          files?: { file_id: string; bytes: number }[];
          staged?: number;
          skipped?: number;
          zip_name?: string;
        };

        if (!res.ok || !data.ok || !data.files?.length) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === batchId && m.kind === "batch"
                ? {
                    ...m,
                    status: "failed",
                    title: "Could not open the zip",
                    phase: data.error || `HTTP ${res.status}`,
                  }
                : m,
            ),
          );
          pushDesk(
            data.error === "no_pdfs_in_zip"
              ? "That zip had no usable PDFs. Nothing was staged."
              : `I could not stage the zip (${data.error || res.status}). Nothing moved.`,
          );
          return;
        }

        const files = data.files;
        const pace = Math.min(400, Math.max(150, Math.round(20000 / files.length)));
        setMessages((prev) =>
          prev.map((m) =>
            m.id === batchId && m.kind === "batch"
              ? {
                  ...m,
                  title: `Working through the zip · ${files.length}`,
                  phase: BATCH_PHASES[0],
                  items: files.map((f) => ({
                    file_id: f.file_id,
                    state: "pending" as const,
                  })),
                }
              : m,
          ),
        );

        for (let i = 0; i < files.length; i++) {
          const fileId = files[i].file_id;
          for (let p = 0; p < BATCH_PHASES.length; p++) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === batchId && m.kind === "batch"
                  ? {
                      ...m,
                      title: fileId,
                      phase: BATCH_PHASES[p],
                      items: m.items.map((item, idx) => ({
                        ...item,
                        state:
                          idx < i ? "done" : idx === i ? "hot" : "pending",
                      })),
                    }
                  : m,
              ),
            );
            setThinkLabel(`${BATCH_PHASES[p]} · ${fileId}`);
            await new Promise((r) => setTimeout(r, Math.round(pace / BATCH_PHASES.length)));
          }
          setMessages((prev) =>
            prev.map((m) =>
              m.id === batchId && m.kind === "batch"
                ? {
                    ...m,
                    items: m.items.map((item, idx) => ({
                      ...item,
                      state: idx <= i ? "done" : "pending",
                    })),
                  }
                : m,
            ),
          );
        }

        setMessages((prev) =>
          prev.map((m) =>
            m.id === batchId && m.kind === "batch"
              ? {
                  ...m,
                  status: "done",
                  title: `Staged ${files.length} invoices`,
                  phase: "Demo walkthrough finished",
                  items: m.items.map((item) => ({ ...item, state: "done" })),
                }
              : m,
          ),
        );

        const preview = files.slice(0, 10);
        const more = files.length - preview.length;
        const panel: DeskPanel = {
          id: "zip-batch",
          title: data.zip_name || file.name,
          meta: `${files.length} staged · demo walkthrough`,
          rows: preview.map((f) => ({
            key: f.file_id,
            title: f.file_id,
            sub: `${Math.max(1, Math.round(f.bytes / 1024))} KB · staged only`,
            value: "—",
            state: "unseen" as const,
          })),
          actions: [
            { label: "Open Invoices", kind: "primary", ui: "invoices" },
          ],
        };
        if (more > 0) {
          panel.rows.push({
            key: "more",
            title: `And ${more} more`,
            sub: "Staged on disk — not engine-evaluated in this walkthrough",
            value: String(more),
          });
        }

        pushDesk(
          `I staged ${files.length} invoice${files.length === 1 ? "" : "s"} from the zip${data.skipped ? ` (${data.skipped} entries skipped)` : ""}. This walkthrough is a demo — I did not run the full engine on each file. Drop one PDF for a live run, or open Invoices for records already processed.`,
          [panel],
          "demo",
        );
      } catch {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === batchId && m.kind === "batch"
              ? {
                  ...m,
                  status: "failed",
                  title: "Zip upload failed",
                  phase: "Nothing was staged",
                }
              : m,
          ),
        );
        pushDesk(
          "The zip did not reach the desk. Nothing was saved or processed.",
        );
      } finally {
        runBusy.current = false;
        setBusy(false);
        inputRef.current?.focus();
      }
    },
    [busy, pushDesk],
  );

  const ask = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || busy) return;
      setChatting(true);
      setBusy(true);
      setInput("");
      setMessages((prev) => [
        ...prev,
        { id: uid(), kind: "user", text },
        { id: uid(), kind: "think", label: THINK_LABELS[0] },
      ]);
      setThinkLabel(THINK_LABELS[0]);

      try {
        if (isOutreachAsk(text)) {
          setMessages((prev) => prev.filter((m) => m.kind !== "think"));
          await proposeOutreach();
          return;
        }

        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, history }),
        });
        if (!res.ok || !res.body) throw new Error("chat unavailable");

        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let rawBuf = "";
        let reply = "";
        let panels: DeskPanel[] = [];
        let meta = "";

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          rawBuf += dec.decode(value, { stream: true });
          const parts = rawBuf.split("\n\n");
          rawBuf = parts.pop() || "";
          for (const part of parts) {
            const line = part.trim();
            if (!line.startsWith("data:")) continue;
            let ev: {
              type?: string;
              text?: string;
              panels?: DeskPanel[];
              model?: string;
              ms?: number;
              tokens?: number;
              error?: boolean;
            };
            try {
              ev = JSON.parse(line.slice(5).trim());
            } catch {
              continue;
            }
            if (ev.type === "delta" && typeof ev.text === "string")
              reply += ev.text;
            if (ev.type === "done") {
              panels = Array.isArray(ev.panels) ? ev.panels : [];
              const bits = [
                ev.model,
                ev.ms != null ? `${ev.ms}ms` : null,
                ev.tokens != null ? `${ev.tokens} tok` : null,
              ].filter(Boolean);
              meta = bits.join(" · ");
            }
          }
        }

        if (!reply)
          reply =
            "I could not complete this lookup. Try a specific invoice or ask what needs your judgement.";

        setHistory((h) => {
          const next: HistoryItem[] = [
            ...h,
            { role: "user", content: text },
            { role: "assistant", content: reply },
          ];
          return next.slice(-12);
        });
        pushDesk(reply, panels, meta);
      } catch {
        pushDesk(
          "The desk did not answer. Nothing moved. Check the API connection and retry.",
        );
      } finally {
        setBusy(false);
        inputRef.current?.focus();
      }
    },
    [busy, history, proposeOutreach, pushDesk],
  );

  function resetChat() {
    if (busy) return;
    setMessages([]);
    setHistory([]);
    setChatting(false);
    setStaged(null);
    setInput("");
    setBusy(false);
    runBusy.current = false;
    setPendingOutreach(null);
    setEmailOpen(false);
    setEmailDraft(null);
    inputRef.current?.focus();
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (staged) {
      if (staged.kind === "zip") void runZipBatch(staged.file, input);
      else void runInvoice(staged.file, input);
      return;
    }
    void ask(input);
  }

  function onPickFile(list: FileList | null) {
    const file = list?.[0];
    if (!file) return;
    const name = file.name.toLowerCase();
    const isPdf =
      file.type === "application/pdf" || name.endsWith(".pdf");
    const isZip =
      file.type === "application/zip" ||
      file.type === "application/x-zip-compressed" ||
      name.endsWith(".zip");
    if (!isPdf && !isZip) {
      pushDesk("I accept one PDF or one zip of PDFs.");
      return;
    }
    setStaged({
      file,
      name: file.name,
      size: file.size,
      kind: isZip ? "zip" : "pdf",
    });
  }

  const canSend = !busy && (Boolean(input.trim()) || Boolean(staged));

  let body: ReactNode;
  if (!chatting) {
    body = (
      <div className="hello">
        <h1>Good morning.</h1>
        <p>{hello}</p>
      </div>
    );
  }

  return (
    <section
      className={`view agent-view ${active ? "on" : ""} ${chatting ? "chatting" : ""}`}
      aria-label="Agent"
      aria-hidden={!active}
      inert={!active ? true : undefined}
    >
      {chatting ? (
        <div className="agent-session">
          <button
            type="button"
            className="btn quiet new-chat"
            disabled={busy}
            onClick={resetChat}
          >
            New chat
          </button>
        </div>
      ) : null}
      <div className="thread" ref={threadRef} id="thread">
        {messages.map((msg) => {
          if (msg.kind === "user") {
            return (
              <div key={msg.id} className="msg user">
                <div className="stack">
                  {msg.text ? <div className="bubble">{msg.text}</div> : null}
                  {msg.fileName ? (
                    <div className="att ok">
                      <span className="ico">
                        {msg.fileKind === "zip" ? "ZIP" : "PDF"}
                      </span>
                      <span>
                        <span className="nm">{msg.fileName}</span>
                        <span className="st">attached</span>
                      </span>
                    </div>
                  ) : null}
                </div>
              </div>
            );
          }
          if (msg.kind === "think") {
            return (
              <div key={msg.id} className="msg">
                <div className="who">
                  Desk <time>{now()}</time>
                </div>
                <div className="say">
                  <span className="think">
                    <span className="orbit" aria-hidden>
                      <i />
                      <i />
                      <i />
                    </span>
                    <span className="orb-slot" aria-hidden>
                      <ThinkingOrb size={20} theme="light" />
                    </span>
                    <span className="lbl">{thinkLabel}</span>
                  </span>
                </div>
              </div>
            );
          }
          if (msg.kind === "run") {
            return (
              <div key={msg.id} className="msg">
                <div className="who">
                  Desk <time>{now()}</time>
                </div>
                <div className={`run-card ${msg.status}`}>
                  <p className="run-title">{msg.title}</p>
                  <ol className="run-stages">
                    {msg.stages.map((stage) => (
                      <li
                        key={stage.id}
                        className={
                          stage.state === "done"
                            ? "is-done"
                            : stage.state === "hot"
                              ? "is-hot"
                              : "is-pending"
                        }
                      >
                        <span className="run-stage-main">
                          <span className="mark" aria-hidden />
                          {stage.label}
                        </span>
                        {stage.subtasks?.length ? (
                          <ol className="run-subtasks">
                            {stage.subtasks.map((sub) => (
                              <li
                                key={sub.id}
                                className={
                                  sub.state === "done"
                                    ? "is-done"
                                    : sub.state === "hot"
                                      ? "is-hot"
                                      : "is-pending"
                                }
                              >
                                <span className="mark" aria-hidden />
                                {sub.label}
                              </li>
                            ))}
                          </ol>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                </div>
              </div>
            );
          }
          if (msg.kind === "batch") {
            return (
              <div key={msg.id} className="msg">
                <div className="who">
                  Desk <time>{now()}</time>
                </div>
                <div className={`run-card batch-card ${msg.status}`}>
                  <div className="batch-head">
                    <p className="run-title">{msg.title}</p>
                    <span className="demo-badge" title="Not a full engine run">
                      demo
                    </span>
                  </div>
                  <p className="batch-phase">{msg.phase}</p>
                  {msg.items.length ? (
                    <ol className="batch-files">
                      {msg.items.map((item) => (
                        <li
                          key={item.file_id}
                          className={
                            item.state === "done"
                              ? "is-done"
                              : item.state === "hot"
                                ? "is-hot"
                                : "is-pending"
                          }
                        >
                          <span className="mark" aria-hidden />
                          <span className="nm">{item.file_id}</span>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p className="batch-phase">Reading the archive…</p>
                  )}
                </div>
              </div>
            );
          }
          return (
            <div key={msg.id} className="msg">
              <div className="who">
                Desk <time>{now()}</time>
              </div>
              <div className="say">
                <SayFade text={msg.text} />
              </div>
              <div className="blocks">
                {msg.panels.map((panel) => (
                  <PanelView
                    key={`${msg.id}-${panel.id}`}
                    panel={panel}
                    onAction={runAction}
                    onAsk={(t) => void ask(t)}
                    onUi={handleUi}
                    onOpenFile={onOpenInvoice}
                  />
                ))}
              </div>
              {msg.meta ? <div className="meta">{msg.meta}</div> : null}
              {msg.undo ? (
                <div className="row-undo">
                  <button
                    type="button"
                    className="btn"
                    title="Not connected yet — nothing will move"
                    onClick={() =>
                      void runAction({ label: "Put it back", act: "undo" })
                    }
                  >
                    Put it back
                  </button>
                </div>
              ) : null}
            </div>
          );
        })}
        {pendingOutreach?.length ? (
          <div className="outreach-pending" role="region" aria-label="Pending emails">
            <h5>
              Pending supplier emails · {pendingOutreach.length} · demo
            </h5>
            <ol>
              {pendingOutreach.map((d) => (
                <li key={d.file_id}>
                  <strong>
                    {d.file_id} · {d.intent_label}
                  </strong>
                  To {d.to || "recipient not recorded"} — {d.subject}
                </li>
              ))}
            </ol>
            <div className="acts">
              <button
                type="button"
                className="btn primary"
                disabled={busy}
                onClick={confirmPendingOutreach}
              >
                Confirm send
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => {
                  setPendingOutreach(null);
                  pushDesk("Cancelled. No supplier emails were marked sent.");
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : null}
      </div>

      {body}

      <EmailDraftDialog
        open={emailOpen}
        draft={emailDraft}
        onOpenChange={(open) => {
          setEmailOpen(open);
          if (!open) setEmailDraft(null);
        }}
        onSend={sendSingleDraft}
      />
      {toast ? (
        <div className="desk-toast" role="status">
          {toast}
        </div>
      ) : null}

      <div className="composer-wrap">
        <div className="composer-in">
          <div className="tray" aria-live="polite">
            {staged ? (
              <div className="att ok">
                <span className="ico">{staged.kind === "zip" ? "ZIP" : "PDF"}</span>
                <span>
                  <span className="nm">{staged.name}</span>
                  <span className="st">
                    {formatBytes(staged.size)} ·{" "}
                    {staged.kind === "zip" ? "zip ready" : "ready"}
                  </span>
                </span>
                <button
                  type="button"
                  className="x"
                  aria-label="Remove attachment"
                  disabled={busy}
                  onClick={() => setStaged(null)}
                >
                  ×
                </button>
              </div>
            ) : null}
          </div>
          <form className="composer" onSubmit={onSubmit}>
            <input
              ref={fileRef}
              type="file"
              accept="application/pdf,.pdf,application/zip,.zip"
              hidden
              onChange={(e) => {
                onPickFile(e.target.files);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              className={`icon-btn ${staged ? "has" : ""}`}
              disabled={busy}
              title="Attach one PDF or zip"
              aria-label="Attach one PDF or zip"
              onClick={() => fileRef.current?.click()}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
                <path
                  d="M7 3.5v6.2a2 2 0 1 0 4 0V4.8a1.2 1.2 0 1 0-2.4 0v4.4"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
              </svg>
            </button>
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                staged ? "Add a note, or just send…" : "Ask the desk…"
              }
              disabled={busy}
              aria-label="Message the desk"
              autoComplete="off"
            />
            <button
              type="submit"
              className="send"
              disabled={!canSend}
              aria-label="Send"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
                <path
                  d="M3 8h10M9 4l4 4-4 4"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </form>
          <div className="chips">
            {CHIPS.map((chip) => (
              <button
                key={chip}
                type="button"
                className="chip"
                disabled={busy}
                onClick={() => void ask(chip)}
              >
                {chip}
              </button>
            ))}
          </div>
          <p
            className={`hint ${busy ? "busy" : ""} ${staged && !busy ? "ready" : ""}`}
          >
            {busy
              ? runBusy.current
                ? "Working through your upload…"
                : "Working on the live records…"
              : staged
                ? staged.kind === "zip"
                  ? "Zip ready — send for a demo walkthrough (files are staged, not fully engine-run)."
                  : "One PDF ready — send to run it through the engine."
                : "Attach one PDF for a live run, or a zip for a staged demo batch. Approve, pay and email are not connected."}
          </p>
        </div>
      </div>
    </section>
  );
}

