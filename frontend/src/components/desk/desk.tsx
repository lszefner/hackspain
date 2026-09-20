"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  MessageSquare,
  FileText,
  ChartNoAxesColumnIncreasing,
} from "lucide-react";
import { AgentPage } from "./agent";
import { InvoicePage } from "./invoices";
import { SummaryPage } from "./summary";
import { Dossier } from "./dossier";

type DeskView = "agent" | "invoices" | "summary";

function resolveView(raw: string | null): DeskView {
  if (raw === "invoices" || raw === "summary") return raw;
  return "agent";
}

export function Desk() {
  const params = useSearchParams();
  const view = resolveView(params.get("view"));

  function selectInvoice(file: string | null) {
    const next = new URLSearchParams(params);
    next.set("view", "invoices");
    if (file) next.set("invoice", file);
    else next.delete("invoice");
    window.history.replaceState(null, "", `/?${next}`);
  }

  function openInvoiceFromAgent(file: string) {
    const next = new URLSearchParams(params);
    next.set("view", "invoices");
    next.set("invoice", file);
    window.history.pushState(null, "", `/?${next}`);
  }

  const file = params.get("invoice");
  return (
    <div className="desk-root" lang="en">
      <div className="app">
        <aside className="side">
          <div className="brand">
            <span className="tile">A</span>
            <span className="who2">
              <span className="k">Owner</span>
              <span className="n">Albertito to guapo</span>
            </span>
          </div>
          <nav className="nav" aria-label="Main navigation">
            {(
              [
                ["agent", "Agent", MessageSquare],
                ["invoices", "Invoices", FileText],
                ["summary", "Summary", ChartNoAxesColumnIncreasing],
              ] as const
            ).map(([key, label, Icon]) => (
              <Link
                key={key}
                href={key === "agent" ? "/" : `/?view=${key}`}
                title={label}
                aria-current={view === key ? "page" : undefined}
                className={`nav-item ${view === key ? "active" : ""}`}
              >
                <Icon strokeWidth={1.7} />
                <span>{label}</span>
              </Link>
            ))}
          </nav>
        </aside>
        <main className="main">
          <AgentPage
            active={view === "agent"}
            onOpenInvoice={openInvoiceFromAgent}
          />
          {view !== "agent" ? (
            <section
              className="view on"
              aria-label={view === "summary" ? "Summary" : "Invoices"}
            >
              {file ? (
                <Dossier
                  key={file}
                  file={file}
                  onClose={() => selectInvoice(null)}
                />
              ) : view === "summary" ? (
                <SummaryPage />
              ) : (
                <InvoicePage onSelect={selectInvoice} />
              )}
            </section>
          ) : null}
        </main>
      </div>
    </div>
  );
}
