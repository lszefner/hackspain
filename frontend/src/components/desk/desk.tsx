"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FileText, ChartNoAxesColumnIncreasing } from "lucide-react";
import { InvoicePage } from "./invoices";
import { SummaryPage } from "./summary";
import { Dossier } from "./dossier";
export function Desk() {
  const params = useSearchParams();
  const view = params.get("view") === "summary" ? "summary" : "invoices";
  function selectInvoice(file: string | null) {
    const next = new URLSearchParams(params);
    if (file) next.set("invoice", file);
    else next.delete("invoice");
    // Invoice selection is client state: do not wait for a server-component
    // navigation before starting the two invoice API reads. Next synchronises
    // native history updates with useSearchParams.
    window.history.replaceState(null, "", `/?${next}`);
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
                ["invoices", "Invoices", FileText],
                ["summary", "Summary", ChartNoAxesColumnIncreasing],
              ] as const
            ).map(([key, label, Icon]) => (
              <Link
                key={key}
                href={`/?view=${key}`}
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
        </main>
      </div>
    </div>
  );
}
