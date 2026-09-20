"use client";
import { useState } from "react";
import { ChevronRight, ExternalLink } from "lucide-react";
import {
  humanize,
  record,
  records,
  text,
  valueText,
  type EvidenceRecord,
} from "@/lib/desk/journey";
import { Json } from "./resource";

export function Disclosure({
  title,
  children,
  open = false,
}: {
  title: string;
  children: React.ReactNode;
  open?: boolean;
}) {
  const [expanded, setExpanded] = useState(open);
  return (
    <details
      className="journey-disclosure"
      open={expanded}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>
        <ChevronRight size={14} />
        {title}
      </summary>
      {expanded ? (
        <div className="journey-disclosure-body">{children}</div>
      ) : null}
    </details>
  );
}
export function AuditRecord({
  value,
  label = "Technical record",
}: {
  value: unknown;
  label?: string;
}) {
  return (
    <Disclosure title={label}>
      <Json value={value} />
    </Disclosure>
  );
}
export function FactList({ items }: { items: Record<string, unknown> }) {
  return (
    <dl className="journey-facts">
      {Object.entries(items).map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{valueText(value)}</dd>
        </div>
      ))}
    </dl>
  );
}
export function EvidenceRefs({ value }: { value: unknown }) {
  const refs = records(value);
  return refs.length ? (
    <ul className="journey-refs">
      {refs.map((ref, index) => (
        <li key={index}>
          <span>{text(ref.source, text(ref.field, "Evidence"))}</span>
          {ref.pointer != null ? (
            <code>{valueText(ref.pointer) || "/"}</code>
          ) : null}
          {ref.quote || ref.excerpt ? (
            <blockquote>{text(ref.quote ?? ref.excerpt)}</blockquote>
          ) : null}
          <AuditRecord value={ref} label="Reference details" />
        </li>
      ))}
    </ul>
  ) : (
    <p className="journey-muted">No evidence references recorded.</p>
  );
}
export function RuleResults({ value }: { value: unknown }) {
  const rules = records(value);
  if (!rules.length)
    return (
      <p className="journey-muted">
        No individual rule results are available in this record.
      </p>
    );
  const counts = rules.reduce<Record<string, number>>((all, rule) => {
    const status = text(rule.status);
    all[status] = (all[status] ?? 0) + 1;
    return all;
  }, {});
  return (
    <>
      <div className="journey-rule-counts">
        {Object.entries(counts).map(([status, count]) => (
          <span
            key={status}
            className={
              status === "PASS"
                ? "is-success"
                : ["VIOLATED", "ERROR", "FAIL"].includes(status)
                  ? "is-danger"
                  : ""
            }
          >
            {count} {humanize(status).toLowerCase()}
          </span>
        ))}
      </div>
      <div className="journey-rules">
        {rules.map((rule, index) => (
          <RuleResult key={`${text(rule.rule_id)}:${index}`} rule={rule} />
        ))}
      </div>
    </>
  );
}
export function Findings({ value }: { value: unknown }) {
  return (
    <div className="journey-findings">
      {records(value).map((finding, index) => (
        <div key={index}>
          <div className="journey-finding-title">
            <strong>
              {humanize(text(finding.code, text(finding.kind, "Finding")))}
            </strong>
            {finding.severity ? (
              <span>{humanize(text(finding.severity))}</span>
            ) : null}
          </div>
          <p>
            {text(
              finding.explanation,
              "No explanation recorded. Open the evidence to inspect this finding.",
            )}
          </p>
          {records(finding.evidence ?? finding.refs).length ? (
            <Disclosure title="Supporting evidence">
              <EvidenceRefs value={finding.evidence ?? finding.refs} />
            </Disclosure>
          ) : null}
        </div>
      ))}
    </div>
  );
}
export function InvoiceFacts({ invoice }: { invoice: EvidenceRecord }) {
  const supplier = record(invoice.supplier),
    payment = record(invoice.payment);
  return (
    <FactList
      items={{
        Supplier: supplier.name,
        "Tax ID": supplier.tax_id,
        "Invoice number": invoice.invoice_number,
        Issued: invoice.issue_date,
        "Purchase order": invoice.purchase_order_reference,
        Currency: invoice.currency,
        IBAN: payment.iban,
      }}
    />
  );
}
export function OriginalLink({ file }: { file: string }) {
  return (
    <a
      className="journey-document-link"
      href={`/api/pdf?file=${encodeURIComponent(file)}`}
      target="_blank"
      rel="noreferrer"
    >
      Open original PDF
      <ExternalLink size={14} />
      <span className="sr-only"> in a new tab</span>
    </a>
  );
}

function RuleResult({ rule }: { rule: EvidenceRecord }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <details
      className="journey-rule"
      open={expanded}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>
        <span
          className={`journey-rule-dot ${rule.status === "PASS" ? "success" : ["VIOLATED", "ERROR", "FAIL"].includes(text(rule.status)) ? "danger" : "attention"}`}
        />
        <span className="journey-rule-name">{text(rule.rule_id)}</span>
        <span className="journey-rule-state">
          {humanize(text(rule.status))}
        </span>
        <ChevronRight size={14} />
      </summary>
      {expanded ? (
        <div className="journey-rule-detail">
          <p>
            {text(rule.explanation, "No explanation was saved for this rule.")}
          </p>
          {rule.applied_consequence ? (
            <p className="journey-consequence">
              Effect on the decision:{" "}
              <strong>{humanize(text(rule.applied_consequence))}</strong>
            </p>
          ) : null}
          {records(rule.inputs).length ? (
            <div className="journey-table-wrap">
              <table className="journey-table">
                <caption>Evidence used by this rule</caption>
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Recorded value</th>
                    <th>Evidence state</th>
                  </tr>
                </thead>
                <tbody>
                  {records(rule.inputs).map((input, i) => (
                    <tr key={i}>
                      <td>{text(input.field)}</td>
                      <td>{valueText(input.value)}</td>
                      <td>{humanize(text(input.state))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <Disclosure title="Evidence references">
            <EvidenceRefs value={rule.evidence_refs} />
          </Disclosure>
          {records(rule.trace).length ? (
            <Disclosure title="How the rule reached this result">
              <ol className="journey-reasoning">
                {records(rule.trace).map((step, i) => (
                  <li key={i}>
                    <strong>
                      {humanize(text(step.operation, "Rule check"))} ·{" "}
                      {humanize(text(step.verdict, text(step.result)))}
                    </strong>
                    <p>{text(step.explanation, "No explanation recorded.")}</p>
                    <AuditRecord value={step} label="Inputs and operation" />
                  </li>
                ))}
              </ol>
            </Disclosure>
          ) : null}
          <AuditRecord value={rule} />
        </div>
      ) : null}
    </details>
  );
}
