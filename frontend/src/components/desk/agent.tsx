"use client";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import type {
  DeskPanel,
  PanelAction,
  PanelRow,
} from "@/lib/desk/panels";
import { labelForEtapa, stoppedRunError } from "@/lib/desk/ingest-ui";
import { emailReply } from "@/lib/desk/write-email";
import { fold } from "@/lib/desk/fold";
import type { OutreachDraft } from "@/lib/desk/outreach";
import type { Ejecucion, Flujo } from "@/lib/engine/types";
import { AttachMenu, type AttachKind } from "./attach-menu";
import { EmailDraftDialog } from "./email-draft-dialog";
import { EmailPreview } from "./email-preview";
import { FileIcon } from "./file-icon";
import { ProgressRing } from "./progress-ring";
import { RunPath } from "./run-path";
import { ThinkPill } from "./think-pill";

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
  status: "uploading" | "ready";
  progress: number;
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

/** Desk design: nothing answers in under four seconds. */
const MIN_THINK_MS = 4000;

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
      {actions.filter((action) => action.act !== "email").map((action, i) => {
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
  // Also render email cards already present in an open conversation.
  const email = panel.email || (panel.id.startsWith("written-email-") ? {
    to: panel.rows[0]?.title.replace(/^To:\s*/, "") || "",
    subject: panel.title,
    body: panel.rows[0]?.detail || "",
  } : null);
  if (email) return <EmailPreview {...email} />;
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
    (e) => e.etapa !== "revisada" && e.etapa !== "resuelta" && e.etapa !== "pagada",
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
  const [emailProposal, setEmailProposal] = useState<OutreachDraft | null>(null);
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
  const runBusy = useRef(false);
  const attachAnim = useRef<number | null>(null);

  useEffect(() => {
    if (!staged || staged.status !== "uploading") return;
    const token = staged.file;
    const duration = 2200 + Math.min(1800, Math.round(token.size / 12_000));
    const started = performance.now();
    const id = window.setInterval(() => {
      const t = Math.min(1, (performance.now() - started) / duration);
      const eased = 1 - (1 - t) ** 3;
      const progress = Math.max(1, Math.round(eased * 100));
      setStaged((prev) => {
        if (!prev || prev.file !== token) return prev;
        if (t >= 1) return { ...prev, progress: 100, status: "ready" };
        return { ...prev, progress, status: "uploading" };
      });
      if (t >= 1) {
        window.clearInterval(id);
        attachAnim.current = null;
      }
    }, 50);
    attachAnim.current = id;
    return () => {
      window.clearInterval(id);
      if (attachAnim.current === id) attachAnim.current = null;
    };
  }, [staged?.file, staged?.status]);

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
            `I drafted one supplier email for ${drafts[0].file_id} (${drafts[0].intent_label}). Review the draft below.`,
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
          `I would write ${drafts.length} supplier emails for issues a message can fix — not every escalation. Confirm to send them:\n${lines}${more}`,
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
      `${drafts.length} email${drafts.length === 1 ? "" : "s"} sent`,
    );
    pushDesk(
      `I sent ${drafts.length} supplier emails. Recipients: ${recipients.slice(0, 5).join(", ")}${recipients.length > 5 ? ` and ${recipients.length - 5} more` : ""}. Intents covered: ${[...new Set(drafts.map((d) => d.intent_label))].join("; ")}.`,
      [],
      "Email sent",
    );
  }, [pendingOutreach, pushDesk, showToast]);

  const sendSingleDraft = useCallback(
    (draft: OutreachDraft) => {
      setEmailOpen(false);
      setEmailDraft(null);
      showToast(`Email sent to ${draft.to || "recipient"}`);
      pushDesk(
        `I sent an email to ${draft.to || "the supplier"} about ${draft.intent_label.toLowerCase()} on ${draft.file_id}.`,
        [],
        "Email sent",
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
      setEmailProposal(null);
      const started = Date.now();
      let lastStages = stagesFromFlujo(null);
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
        const stoppedError = stoppedRunError(state, engineBusy, engineError, ejecucion?.error);
        if (stoppedError) {
          failRun(
            "Processing stopped",
            `I could not finish ${fileId}. ${stoppedError} This run was not retried automatically. Nothing was approved or paid.`,
          );
          return;
        }
        // Missing status is not progress. Keep the last recorded stage while polling.
        if (flujo?.etapas?.length) lastStages = stagesFromFlujo(flujo);
        const stages = lastStages;

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
                  setEmailProposal(outreach.draft);
                  pushDesk(
                    `Would you like me to write an email about ${fileId} to ${outreach.draft.to || "the customer"}? ${outreach.draft.to ? 'Reply "yes" to write it.' : "Tell me their email address to write it."}`,
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
      setEmailProposal(null);
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
      setEmailProposal(null);
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
          meta: `${files.length} staged`,
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
          `I staged ${files.length} invoice${files.length === 1 ? "" : "s"} from the zip${data.skipped ? ` (${data.skipped} entries skipped)` : ""}. The files are ready for review. Upload one PDF to process it, or open Invoices for processed records.`,
          [panel],
          "Files staged",
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
      const thinkStarted = Date.now();
      const holdThink = async () => {
        const left = MIN_THINK_MS - (Date.now() - thinkStarted);
        if (left > 0) await new Promise((r) => setTimeout(r, left));
      };

      try {
        const emailResponse = emailProposal ? emailReply(text) : null;
        if (emailProposal && emailResponse) {
          if (emailResponse.action === "cancel") {
            setEmailProposal(null);
            pushDesk("Okay, I will not write an email.");
            return;
          }
          const response = await fetch("/api/write-email", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ file: emailProposal.file_id, recipient: emailResponse.recipient }),
          });
          const result = await response.json();
          if (!response.ok) {
            pushDesk(result.error || "I could not write the email. You can ask me to try again.");
            return;
          }
          if (result.status === "recipient_required") {
            pushDesk("What is the customer's email address? I need it to write the email.");
            return;
          }
          if (result.status !== "written" || !result.draft) throw new Error("Invalid email tool result");
          setEmailProposal(null);
          const draft = result.draft as OutreachDraft;
          pushDesk(`Email sent to ${draft.to}.`, [{
            id: `written-email-${uid()}`, title: draft.subject,
            email: { to: draft.to, subject: draft.subject, body: draft.body },
            rows: [],
          }]);
          return;
        }
        // A different topic ends the proposal, so a later yes cannot target a stale invoice.
        setEmailProposal(null);
        if (isOutreachAsk(text)) {
          await holdThink();
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
        await holdThink();
        pushDesk(reply, panels, meta);
      } catch {
        await holdThink();
        pushDesk(
          "The desk did not answer. Nothing moved. Check the API connection and retry.",
        );
      } finally {
        setBusy(false);
        inputRef.current?.focus();
      }
    },
    [busy, history, emailProposal, proposeOutreach, pushDesk],
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

  function onPickFile(kind: AttachKind, list: FileList | null) {
    const file = list?.[0];
    if (!file) return;
    if (kind === "image") {
      pushDesk(
        "I need a PDF for a live run. A photo or scan is not enough yet — export it as PDF, or drop a zip.",
      );
      return;
    }
    const name = file.name.toLowerCase();
    const isPdf =
      kind === "pdf" ||
      file.type === "application/pdf" ||
      name.endsWith(".pdf");
    const isZip =
      kind === "zip" ||
      file.type === "application/zip" ||
      file.type === "application/x-zip-compressed" ||
      name.endsWith(".zip");
    if (!isPdf && !isZip) {
      pushDesk("I accept one PDF or one zip of PDFs.");
      return;
    }

    if (attachAnim.current != null) {
      window.clearInterval(attachAnim.current);
      attachAnim.current = null;
    }

    setStaged({
      file,
      name: file.name,
      size: file.size,
      kind: isZip ? "zip" : "pdf",
      status: "uploading",
      progress: 0,
    });
  }

  const attaching = staged?.status === "uploading";
  const canSend =
    !busy &&
    !attaching &&
    (Boolean(input.trim()) || staged?.status === "ready");

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
                        <FileIcon kind={msg.fileKind === "zip" ? "zip" : "pdf"} />
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
                  <ThinkPill label={thinkLabel} />
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
                <RunPath
                  title={msg.title}
                  status={msg.status}
                  stages={msg.stages}
                />
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
                    <span className="demo-badge" title="Files ready for review">
                      staged
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
              Pending supplier emails · {pendingOutreach.length}
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
                Send emails
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => {
                  setPendingOutreach(null);
                  pushDesk("Cancelled.");
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
              <div
                className={`att glass ${staged.status === "ready" ? "ok" : "busy"}`}
              >
                {staged.status === "uploading" ? (
                  <ProgressRing
                    progress={staged.progress}
                    size={36}
                    stroke={2.75}
                    label="Attaching file"
                  />
                ) : (
                  <span className="ico">
                    <FileIcon kind={staged.kind} />
                  </span>
                )}
                <span>
                  <span className="nm">{staged.name}</span>
                  <span className="st">
                    {staged.status === "uploading"
                      ? `${formatBytes(staged.size)} · attaching ${staged.progress}%`
                      : `${formatBytes(staged.size)} · ${
                          staged.kind === "zip" ? "zip ready" : "ready"
                        }`}
                  </span>
                </span>
                <button
                  type="button"
                  className="x"
                  aria-label="Remove attachment"
                  disabled={busy || attaching}
                  onClick={() => {
                    if (attachAnim.current != null) {
                      window.clearInterval(attachAnim.current);
                      attachAnim.current = null;
                    }
                    setStaged(null);
                  }}
                >
                  ×
                </button>
              </div>
            ) : null}
          </div>
          <form className="composer" onSubmit={onSubmit}>
            <AttachMenu
              disabled={busy || attaching}
              hasAttachment={staged?.status === "ready"}
              onPick={onPickFile}
            />
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                attaching
                  ? "Attaching…"
                  : staged
                    ? "Add a note, or just send…"
                    : "Ask the desk…"
              }
              disabled={busy || attaching}
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
                disabled={busy || attaching}
                onClick={() => void ask(chip)}
              >
                {chip}
              </button>
            ))}
          </div>
          <p
            className={`hint ${busy || attaching ? "busy" : ""} ${staged?.status === "ready" && !busy ? "ready" : ""}`}
          >
            {busy
              ? runBusy.current
                ? "Working through your upload…"
                : "Working on the live records…"
              : attaching
                ? "Attaching to the desk…"
                : staged?.status === "ready"
                  ? staged.kind === "zip"
                    ? "Zip ready — send to stage the files for review."
                    : "One PDF ready — send to run it through the engine."
                  : "Attach a PDF to review it, then tell me what to do next."}
          </p>
        </div>
      </div>
    </section>
  );
}
