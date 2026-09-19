"use client";
import { useEffect, useRef } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Circle,
  FileText,
  RefreshCw,
  AlertTriangle,
  Minus,
  Clock3,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Flujo, FlujoTrabajo } from "@/lib/engine/types";
import type { Detail } from "@/lib/desk/types";
import { money } from "@/lib/desk/types";
import {
  attempts,
  decisionTitle,
  decisionTone,
  humanize,
  jobs,
  matchingDetail,
  outputExplanation,
  record,
  records,
  stageTime,
  stageTitles,
  text,
} from "@/lib/desk/journey";
import { useResource, Failure } from "./resource";
import {
  AuditRecord,
  Disclosure,
  FactList,
  Findings,
  InvoiceFacts,
  OriginalLink,
  RuleResults,
  EvidenceRefs,
} from "./journey-evidence";

// An invoice is a case history: recorded events, then the evidence behind each
// decision. The cream/brown reference palette remains the visual authority.
export function Dossier({
  file,
  onClose,
}: {
  file: string | null;
  onClose: () => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const {
    data: flow,
    error,
    mutate,
    isValidating,
  } = useResource<Flujo>(
    file ? `/api/engine/factura/${encodeURIComponent(file)}/flujo` : null,
  );
  const { data: detail, mutate: refreshDetail } = useResource<Detail>(
    file ? `/api/dossier?file=${encodeURIComponent(file)}` : null,
  );
  useEffect(() => {
    heading.current?.focus();
  }, [file, flow?.file_id]);
  if (!file) return null;
  return (
    <div className="invoice-case" lang="en">
      <div className="case-toolbar">
        <button className="case-back" onClick={onClose}>
          <ArrowLeft size={15} />
          All invoices
        </button>
        <span className="case-toolbar-file">
          <FileText size={14} />
          {file}
        </span>
        <Button
          className="btn"
          disabled={isValidating}
          onClick={() => {
            void mutate();
            void refreshDetail();
          }}
          aria-label="Refresh invoice history"
        >
          <RefreshCw size={14} />
          <span>Refresh</span>
        </Button>
      </div>
      <div className="case-scroll">
        {error ? (
          <Failure error={error} retry={() => void mutate()} />
        ) : !flow ? (
          <div className="case-loading" role="status">
            <h1>Opening invoice history</h1>
            <p>Retrieving saved attempts, decisions and supporting evidence.</p>
            <div />
            <div />
            <div />
          </div>
        ) : (
          <CaseHistory
            flow={flow}
            detail={matchingDetail(flow, detail)}
            headingRef={heading}
          />
        )}
      </div>
    </div>
  );
}
function Timestamp({
  value,
  label,
}: {
  value: string | null | undefined;
  label?: string;
}) {
  if (!value || !Number.isFinite(Date.parse(value)))
    return (
      <span className="event-time-missing">{label ?? "Time not recorded"}</span>
    );
  const date = new Date(value);
  return (
    <time dateTime={value} title={value}>
      <span>
        {date.toLocaleTimeString("en-GB", {
          timeZone: "Europe/Madrid",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })}
      </span>
      <small>
        {date.toLocaleDateString("en-GB", {
          timeZone: "Europe/Madrid",
          day: "2-digit",
          month: "short",
          year: "numeric",
        })}
      </small>
    </time>
  );
}
function Event({
  id,
  title,
  time,
  status,
  tone = "neutral",
  children,
}: {
  id: string;
  title: string;
  time?: string | null;
  status: string;
  tone?: string;
  children?: React.ReactNode;
}) {
  const Icon =
    tone === "success"
      ? Check
      : tone === "danger"
        ? AlertTriangle
        : tone === "absent"
          ? Minus
          : tone === "attention"
            ? Clock3
            : Circle;
  return (
    <li id={id} className={`journey-event tone-${tone}`}>
      <div className="event-clock">
        <Timestamp value={time} />
      </div>
      <div className="event-marker">
        <Icon size={13} />
      </div>
      <article className="event-content">
        <header>
          <h3>{title}</h3>
          <span className={`event-status ${tone}`}>{status}</span>
        </header>
        {children}
      </article>
    </li>
  );
}
function CaseHistory({
  flow,
  detail,
  headingRef,
}: {
  flow: Flujo;
  detail?: Detail;
  headingRef: React.RefObject<HTMLHeadingElement | null>;
}) {
  const extraction = flow.extraccion;
  const invoice = record(extraction?.factura);
  const supplier = record(invoice.supplier);
  const totals = record(invoice.totals);
  const evaluation = flow.evaluacion;
  const result = record(evaluation?.resultado);
  const review = flow.revision;
  const savedAttempts = attempts(flow);
  const reasons = records(result.decision_reasons);
  const title = text(invoice.invoice_number, flow.file_id);
  const hasError =
    extraction?.status === "failed" || extraction?.status === "unknown";
  const matchingPdf = detail?.pdf_available;
  return (
    <div className="case-container">
      <header className="case-header">
        <div>
          <h1 tabIndex={-1} ref={headingRef}>
            {title}
          </h1>
          <p>
            {text(supplier.name, "Supplier not identified")}
            {invoice.issue_date ? (
              <>
                <span className="case-separator">/</span>Issued{" "}
                {text(invoice.issue_date)}
              </>
            ) : null}
          </p>
        </div>
        <div className="case-amount">
          {money(
            typeof totals.total === "number" || typeof totals.total === "string"
              ? totals.total
              : null,
            typeof invoice.currency === "string" ? invoice.currency : null,
          )}
          <span>Invoice total</span>
        </div>
      </header>
      <div className={`case-outcome ${decisionTone(flow.salida.verdict)}`}>
        <span className="case-outcome-dot" />
        <div>
          <strong>{decisionTitle(flow.salida.verdict)}</strong>
          <p>{outputExplanation(flow)}</p>
          {review?.attention_required ? (
            <p className="case-review-note">
              The recorded review still requires attention.
            </p>
          ) : null}
        </div>
        <a href="#event-output">
          Trace this decision
          <ArrowRight size={14} />
        </a>
      </div>
      <div className="case-columns">
        <div className="case-history">
          <div className="history-heading">
            <h2>Invoice journey</h2>
            <span>Processing order · times in Madrid</span>
          </div>
          <ol className="journey-events">
            <Event
              id="event-received"
              title={
                flow.identidad ? "Invoice received" : "Receipt not recorded"
              }
              time={stageTime(flow, "recibida", detail)}
              tone={flow.identidad ? "success" : "absent"}
              status={flow.identidad ? "Recorded" : "No record"}
            >
              <p>
                {flow.identidad ? (
                  <>
                    The original file <strong>{flow.file_id}</strong> was
                    registered for processing.
                  </>
                ) : (
                  "There is no persisted receipt for this document."
                )}
              </p>
              {flow.identidad ? (
                <Disclosure title="File identity">
                  <FactList
                    items={{
                      File: flow.file_id,
                      Size:
                        flow.identidad.bytes == null
                          ? null
                          : `${flow.identidad.bytes.toLocaleString()} bytes`,
                      "SHA-256": flow.identidad.sha256,
                      "Input ID": flow.identidad.input_id,
                      "Batch ID": flow.identidad.batch_id,
                    }}
                  />
                </Disclosure>
              ) : null}
            </Event>
            <Event
              id="event-extraction"
              title={
                hasError
                  ? "Extraction could not be completed"
                  : extraction?.status === "completed"
                    ? "Invoice data extracted"
                    : "Extracting the invoice"
              }
              time={stageTime(flow, "extraida", detail)}
              tone={
                hasError
                  ? "danger"
                  : extraction?.status === "completed"
                    ? "success"
                    : extraction?.status === "needs_review"
                      ? "attention"
                      : "absent"
              }
              status={humanize(extraction?.status ?? "not_recorded")}
            >
              <p>
                {extraction?.ruta === "vision"
                  ? "The document was read using vision, then interpreted into invoice fields."
                  : extraction?.ruta === "deterministic"
                    ? "Invoice fields were read from the document’s text using deterministic extraction."
                    : "No extraction route is recorded."}
              </p>
              {extraction?.por_que_vision?.length ? (
                <div className="journey-route">
                  <strong>Why vision was used</strong>
                  <ul>
                    {extraction.por_que_vision.map((gap, i) => (
                      <li key={i}>{humanize(gap)}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {extraction?.error ? (
                <div className="journey-problem">
                  <strong>Extraction error</strong>
                  <p>
                    {text(
                      record(extraction.error).message,
                      text(record(extraction.error).code),
                    )}
                  </p>
                  <AuditRecord value={extraction.error} label="Error details" />
                </div>
              ) : null}
              <div className="attempt-heading">
                <h4>Processing attempts</h4>
                <span>{savedAttempts.length} recorded</span>
              </div>
              {savedAttempts.length ? (
                <ol className="journey-attempts">
                  {savedAttempts.map(({ job, attempt }) => (
                    <li
                      key={`${job.id}:${attempt.attempt_number}`}
                      className={
                        attempt.status === "failed" ? "attempt-failed" : ""
                      }
                    >
                      <div className="attempt-head">
                        <span>
                          {stageTitles[job.stage] ?? humanize(job.stage)}
                          <small>Attempt {attempt.attempt_number}</small>
                        </span>
                        <strong
                          className={
                            attempt.status === "succeeded"
                              ? "is-success"
                              : attempt.status === "failed"
                                ? "is-danger"
                                : ""
                          }
                        >
                          {humanize(attempt.status)}
                        </strong>
                      </div>
                      <div className="attempt-provider">
                        {[job.provider, job.model]
                          .filter(Boolean)
                          .join(" / ") || "Provider not recorded"}
                      </div>
                      <div className="attempt-times">
                        <div>
                          <span>Started</span>
                          <Timestamp value={attempt.started_at} />
                        </div>
                        <ArrowRight size={13} />
                        <div>
                          <span>Finished</span>
                          <Timestamp value={attempt.finished_at} />
                        </div>
                        {attempt.latency_seconds != null ? (
                          <span className="attempt-duration">
                            {Number(attempt.latency_seconds).toFixed(2)} s
                          </span>
                        ) : null}
                      </div>
                      {attempt.error ? (
                        <p className="attempt-error">
                          {text(
                            record(attempt.error).message,
                            text(
                              record(attempt.error).code,
                              "An error was recorded for this attempt.",
                            ),
                          )}
                        </p>
                      ) : null}
                      <AuditRecord
                        label="Attempt evidence and identifiers"
                        value={{
                          ...attempt,
                          job_id: job.id,
                          stage: job.stage,
                          provider: job.provider,
                          model: job.model,
                          artifact_id: job.artifact_id,
                          prompt_version: job.prompt_version,
                          config_version: job.config_version,
                        }}
                      />
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="journey-muted">
                  No individual attempts are available in this history.
                </p>
              )}
              {jobs(flow).map((job) => (
                <JobGap key={job.id} job={job} />
              ))}
              {extraction?.trabajos && !Array.isArray(extraction.trabajos) ? (
                <p className="journey-problem">
                  The processing log could not be read:{" "}
                  {extraction.trabajos.error.code}.
                </p>
              ) : null}
              {extraction?.gaps?.length ? (
                <Disclosure title="Unresolved extraction gaps">
                  <ul>
                    {extraction.gaps.map((gap, i) => (
                      <li key={i}>{humanize(gap)}</li>
                    ))}
                  </ul>
                </Disclosure>
              ) : null}
              {extraction?.factura ? (
                <Disclosure title="Extracted invoice fields">
                  <InvoiceFacts invoice={invoice} />
                  <AuditRecord value={invoice} label="All extracted fields" />
                </Disclosure>
              ) : null}
              {extraction?.checks ? (
                <AuditRecord
                  value={extraction.checks}
                  label="Structural validation record"
                />
              ) : null}
            </Event>
            <Event
              id="event-evaluation"
              title={
                evaluation ? "Rules evaluated" : "Rule evaluation not recorded"
              }
              time={stageTime(flow, "evaluada", detail)}
              status={
                evaluation?.decision
                  ? decisionTitle(evaluation.decision)
                  : evaluation?.error
                    ? "Record unavailable"
                    : "No decision"
              }
              tone={
                evaluation?.error
                  ? "danger"
                  : evaluation?.decision
                    ? decisionTone(evaluation.decision)
                    : "absent"
              }
            >
              {evaluation ? (
                <>
                  <p>
                    {evaluation.decision
                      ? `The evaluator returned “${decisionTitle(evaluation.decision).toLowerCase()}” using the recorded rules and evidence.`
                      : "An evaluation record exists, but its decision could not be loaded."}
                  </p>
                  {evaluation.error ? (
                    <p className="journey-problem">
                      Evaluation record unavailable: {evaluation.error.code}.
                    </p>
                  ) : null}
                  {result.code ? (
                    <p className="journey-problem">
                      Rule results unavailable: {text(result.code)}.
                    </p>
                  ) : null}
                  {reasons.length ? (
                    <div className="decision-reasons">
                      <h4>Why this decision</h4>
                      {reasons.map((reason, i) => (
                        <div key={i}>
                          <p>
                            {text(
                              reason.explanation,
                              "No explanation recorded.",
                            )}
                          </p>
                          {reason.rule_id ? (
                            <span>{text(reason.rule_id)}</span>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <RuleResults value={result.rule_results} />
                  <Disclosure title="Rules and source evidence">
                    <SourceEvidence flow={flow} />
                  </Disclosure>
                  <AuditRecord
                    value={evaluation}
                    label="Evaluation record and lineage"
                  />
                </>
              ) : (
                <p>
                  {hasError
                    ? "No rule decision is recorded after the extraction failure."
                    : "There is no saved rule evaluation for this invoice."}
                </p>
              )}
            </Event>
            <Event
              id="event-review"
              title={
                review?.status === "DISABLED"
                  ? "Contextual review was disabled"
                  : review?.status === "INCOMPLETE"
                    ? "Contextual review is incomplete"
                    : review?.status === "FAILED"
                      ? "Contextual review failed"
                      : review
                        ? "Contextual review recorded"
                        : "Contextual review not recorded"
              }
              time={stageTime(flow, "revisada", detail)}
              tone={
                review?.status === "FAILED" || review?.error
                  ? "danger"
                  : review?.status === "INCOMPLETE" ||
                      review?.attention_required
                    ? "attention"
                    : review?.status === "COMPLETED"
                      ? "success"
                      : "absent"
              }
              status={
                review?.status === "DISABLED"
                  ? "Disabled"
                  : review?.status
                    ? humanize(review.status)
                    : review?.error
                      ? "Record unavailable"
                      : "No record"
              }
            >
              <p>
                {review?.status === "DISABLED"
                  ? "This run explicitly disabled contextual review. No reviewer decision was made."
                  : review?.status === "INCOMPLETE"
                    ? "A review was saved with limitations. Its findings remain part of the decision trail."
                    : review?.status === "COMPLETED"
                      ? "The reviewer assessed the evaluator’s conclusions against the recorded evidence."
                      : review?.status === "FAILED"
                        ? "The review attempt failed. It does not confirm the evaluator’s recommendation."
                        : review
                          ? "A review record exists, but a completed assessment is not available."
                          : "No contextual review is available for this invoice."}
              </p>
              {review?.model ? (
                <p className="journey-byline">
                  Reviewed by {review.provider} / {review.model}
                </p>
              ) : null}
              {review?.error ? (
                <div className="journey-problem">
                  <p>
                    {text(
                      record(review.error).message,
                      text(
                        record(review.error).code,
                        "The saved review could not be loaded.",
                      ),
                    )}
                  </p>
                  <AuditRecord value={review.error} label="Review error" />
                </div>
              ) : null}
              <Findings value={review?.findings} />
              {records(review?.rule_reviews).length ? (
                <Disclosure
                  title={`${records(review?.rule_reviews).length} rule assessments`}
                >
                  {records(review?.rule_reviews).map((item, i) => (
                    <div className="review-assessment" key={i}>
                      <strong>
                        {text(item.rule_id)}
                        <span>{humanize(text(item.assessment))}</span>
                      </strong>
                      <p>
                        {text(item.explanation, "No explanation recorded.")}
                      </p>
                      <EvidenceRefs value={item.evidence} />
                    </div>
                  ))}
                </Disclosure>
              ) : null}
              {review ? (
                <AuditRecord value={review} label="Review record" />
              ) : null}
            </Event>
            <Event
              id="event-output"
              title={decisionTitle(flow.salida.verdict)}
              time={stageTime(flow, "emitida", detail)}
              tone={decisionTone(flow.salida.verdict)}
              status="Recommendation"
            >
              <p>{outputExplanation(flow)}</p>
              {evaluation?.decision &&
              evaluation.decision !== flow.salida.verdict ? (
                <div className="decision-change">
                  <span>
                    {decisionTitle(evaluation.decision)}
                    <small>Evaluator</small>
                  </span>
                  <ArrowRight size={16} />
                  <span>
                    {decisionTitle(flow.salida.verdict)}
                    <small>Final recommendation</small>
                  </span>
                </div>
              ) : null}
              <p className="journey-muted">
                This is a recommendation derived from saved results. It does not
                authorise or confirm a payment.
              </p>
              <AuditRecord value={flow.salida} label="Decision basis" />
            </Event>
            <Event
              id="event-resolution"
              title="Human resolution"
              tone="absent"
              status="Not recorded"
            >
              <p>
                No human approval, rejection or resolution is recorded in this
                system.
              </p>
            </Event>
            <Event
              id="event-payment"
              title="Payment"
              tone="absent"
              status="Not recorded"
            >
              <p>
                No payment execution or confirmation is recorded in this system.
              </p>
            </Event>
          </ol>
          <footer className="journey-footer">
            This history covers the latest stored processing run for this file.
            An absent timestamp or record is left explicit.
          </footer>
        </div>
        <aside className="case-reference" aria-label="Invoice reference">
          <div className="case-reference-inner">
            <div className="reference-document">
              <FileText size={22} strokeWidth={1.4} />
              <strong>{flow.file_id}</strong>
              <span>
                {extraction?.paginas != null
                  ? `${extraction.paginas} ${extraction.paginas === 1 ? "page" : "pages"}`
                  : "Page count not recorded"}
                {flow.identidad?.bytes != null
                  ? ` · ${(flow.identidad.bytes / 1024).toFixed(1)} KB`
                  : ""}
              </span>
              {matchingPdf ? (
                <OriginalLink file={flow.file_id} />
              ) : (
                <span className="journey-muted">
                  {detail
                    ? "Original PDF not available"
                    : "PDF availability not verified"}
                </span>
              )}
            </div>
            <section>
              <h2>Invoice details</h2>
              <InvoiceFacts invoice={invoice} />
            </section>
            <nav aria-label="Invoice journey sections">
              <h2>In this history</h2>
              {[
                ["received", "Receipt"],
                ["extraction", "Extraction & attempts"],
                ["evaluation", "Rule decisions"],
                ["review", "Contextual review"],
                ["output", "Recommendation"],
                ["payment", "Resolution & payment"],
              ].map(([id, label]) => (
                <a key={id} href={`#event-${id}`}>
                  {label}
                  <ArrowRight size={12} />
                </a>
              ))}
            </nav>
            {flow.ejecucion ? (
              <Disclosure title="Processing run">
                <FactList
                  items={{
                    Request: flow.ejecucion.request_key,
                    State: flow.ejecucion.state,
                    Rules: flow.ejecucion.rule_generation,
                  }}
                />
                {flow.ejecucion.error ? (
                  <p className="journey-problem">{flow.ejecucion.error}</p>
                ) : null}
                <AuditRecord
                  value={flow.ejecucion}
                  label="Run configuration and evidence snapshot"
                />
              </Disclosure>
            ) : null}
            <AuditRecord value={flow} label="Complete audit record" />
          </div>
        </aside>
      </div>
    </div>
  );
}
function JobGap({ job }: { job: FlujoTrabajo }) {
  const count = Array.isArray(job.intentos) ? job.intentos.length : 0;
  if (count >= job.attempt_count && count > 0) return null;
  return (
    <div className="journey-problem">
      <p>
        {stageTitles[job.stage] ?? humanize(job.stage)}:{" "}
        {humanize(job.state).toLowerCase()}.{" "}
        {job.attempt_count > count
          ? `${job.attempt_count - count} attempt records are unavailable.`
          : "No attempt timestamps are recorded."}
      </p>
      {job.last_error ? (
        <AuditRecord value={job.last_error} label="Last recorded error" />
      ) : null}
      <AuditRecord value={job} label="Job record" />
    </div>
  );
}
function SourceEvidence({ flow }: { flow: Flujo }) {
  const evaluation = flow.evaluacion;
  const result = record(evaluation?.resultado);
  return (
    <>
      <FactList
        items={{
          Ruleset: record(result.ruleset).ruleset_version,
          Policy: record(result.ruleset).policy_id,
          "Evaluation date": evaluation?.evaluation_date,
          "Evidence captured at": evaluation?.captured_at,
        }}
      />
      <p className="journey-muted">
        The evidence capture time is not the time the evaluator made its
        decision.
      </p>
      {Object.entries(record(evaluation?.fuentes)).map(([name, source]) => {
        const value = record(source);
        return (
          <div className="journey-source" key={name}>
            <strong>
              {humanize(name)}
              <span>{humanize(text(value.availability))}</span>
            </strong>
            <p>{text(value.scope, "Source scope not recorded")}</p>
            <FactList
              items={{
                Captured: value.captured_at,
                "As of": value.as_of,
                "Asserted by": value.asserted_by,
                Authority: value.authoritative_for,
              }}
            />
            <AuditRecord value={source} label="Source reference" />
          </div>
        );
      })}
    </>
  );
}
