"use client";
import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  actions,
  actionName,
  actionClass,
  money,
  type Lanes,
  type Supplier,
  type Invoices,
} from "@/lib/desk/types";
import { useResource, Loading, Failure, Pages } from "./resource";
export function InvoicePage({
  onSelect,
}: {
  onSelect: (file: string) => void;
}) {
  const params = useSearchParams();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [action, setAction] = useState("");
  const [stage, setStage] = useState(params.get("stage") ?? "processed");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search);
      setPage(1);
    }, 250);
    return () => clearTimeout(timer);
  }, [search]);
  const filters = new URLSearchParams({ q: query, action, lifecycle: stage });
  const url = `/api/lanes?${filters}&page=${page}&limit=25`;
  const { data, error, mutate, isValidating } = useResource<Lanes>(url);
  return (
    <>
      <div className="inv-head">
        <Input
          type="search"
          className="search"
          aria-label="Search invoices"
          placeholder="Search a file, a supplier or an invoice number…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <label className="live-filter">
          Stage{" "}
          <select
            value={stage}
            onChange={(e) => {
              setStage(e.target.value);
              setPage(1);
            }}
          >
            <option value="">All recorded</option>
            <option value="processed">Processed</option>
            <option value="extracted">Extracted</option>
            <option value="processing">Processing</option>
            <option value="error">Errors</option>
          </select>
        </label>
        <Button
          className="btn"
          disabled={isValidating}
          onClick={() => {
            setRevision((v) => v + 1);
            void mutate();
          }}
        >
          <RefreshCw size={13} />
          Refresh
        </Button>
        <div className="seg" aria-label="Recommendation">
          {actions.map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={action === value ? "on" : ""}
              aria-pressed={action === value}
              onClick={() => {
                setAction(value);
                setPage(1);
              }}
            >
              {label}
              {value && data
                ? ` · ${data.counts?.[value === "PAGAR" ? "PAY" : value === "ESCALAR" ? "ESCALATE" : "DO NOT PAY"] ?? 0}`
                : ""}
            </button>
          ))}
        </div>
        <span className="inv-count" aria-live="polite">
          {data ? `${data.matched} / ${data.grand} invoices` : ""}
        </span>
      </div>
      <div className="scroll" aria-busy={isValidating}>
        {error ? (
          <Failure error={error} retry={() => void mutate()} />
        ) : !data ? (
          <Loading />
        ) : (
          <>
            <div className="lanes">
              {data.lanes.length ? (
                data.lanes.map((lane) => (
                  <SupplierLane
                    key={`${lane.id}:${lane.currency}:${filters}:${revision}`}
                    lane={lane}
                    filters={filters.toString()}
                    onSelect={onSelect}
                  />
                ))
              ) : (
                <p className="empty-note">
                  {data.grand
                    ? "No recorded invoices match these filters."
                    : "No invoices recorded yet."}
                </p>
              )}
            </div>
            <Pages
              page={page}
              total={data.supplier_count}
              limit={25}
              onChange={setPage}
              label="Supplier page"
            />
          </>
        )}
      </div>
    </>
  );
}
function SupplierLane({
  lane,
  filters,
  onSelect,
}: {
  lane: Supplier;
  filters: string;
  onSelect: (file: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`lane ${open ? "open" : ""}`}>
      <button
        className="lane-head"
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="cv2" />
        <span className="who3">
          <span className="nm2">{lane.name}</span>
          <span className="sub2">
            {lane.count} ·{" "}
            {lane.currency === "UNKNOWN"
              ? "currency not recorded"
              : lane.currency}
            {lane.missing_amounts
              ? ` · ${lane.missing_amounts} missing amounts`
              : ""}
          </span>
        </span>
        <span className="figs">
          {(
            [
              ["pay", "Recommend pay", lane.pay_total, lane.pay_n],
              ["rev", "Review", lane.review_total, lane.review_n],
              ["stop", "Do not pay", lane.nopay_total, lane.nopay_n],
            ] as const
          ).map(([kind, label, total, n]) => (
            <span key={kind} className={`fig ${kind}`}>
              <span className="lbl2">{label}</span>
              <span className={`amt ${n === 0 ? "zero" : ""}`}>
                {lane.currency === "UNKNOWN"
                  ? "—"
                  : money(total, lane.currency)}
              </span>
              <span className="n2">{n} invoices</span>
            </span>
          ))}
        </span>
      </button>
      {open ? (
        <div className="lane-body">
          <div className="in2">
            <SupplierInvoices
              lane={lane}
              filters={filters}
              onSelect={onSelect}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}
function SupplierInvoices({
  lane,
  filters,
  onSelect,
}: {
  lane: Supplier;
  filters: string;
  onSelect: (file: string) => void;
}) {
  const [page, setPage] = useState(1);
  const params = new URLSearchParams(filters);
  params.set("vendor", lane.id);
  params.set("currency", lane.currency);
  params.set("page", String(page));
  params.set("limit", "25");
  const { data, error, mutate } = useResource<Invoices>(
    `/api/invoices?${params}`,
  );
  if (error) return <Failure error={error} retry={() => void mutate()} />;
  if (!data) return <Loading />;
  return (
    <div className="pad">
      <div className="itbl">
        <div className="ihead">
          <span>Invoice</span>
          <span>Number</span>
          <span>Issued</span>
          <span>Amount</span>
          <span>Recommendation</span>
          <span>Reason / stage</span>
        </div>
        {data.rows.map((row) => (
          <button
            type="button"
            className="irow"
            key={row.file_id}
            onClick={() => onSelect(row.file_id)}
          >
            <span className="f" title={row.file_id}>
              {row.file_id}
            </span>
            <span className="n">{row.number ?? "Not recorded"}</span>
            <span className="d">{row.date ?? "—"}</span>
            <span className="a">{money(row.total, row.currency)}</span>
            <span className={`k ${actionClass(row.verdict)}`}>
              {actionName(row.verdict)}
            </span>
            <span className="b" title={row.reason ?? row.lifecycle}>
              {row.reason ?? row.lifecycle}
              {row.attention_required ? " · attention required" : ""}
            </span>
          </button>
        ))}
      </div>
      <Pages page={page} total={data.matched} limit={25} onChange={setPage} />
    </div>
  );
}
