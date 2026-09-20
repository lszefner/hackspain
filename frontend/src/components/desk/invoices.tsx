"use client";
import { useEffect, useState, type ComponentType } from "react";
import { useSearchParams } from "next/navigation";
import {
  BookOpen,
  Building2,
  Monitor,
  Package,
  RefreshCw,
  Send,
  Shield,
  Sparkles,
  Truck,
  UtensilsCrossed,
  Zap,
  type LucideProps,
} from "lucide-react";
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

const STAGES = [
  ["", "All stages"],
  ["processed", "Processed"],
  ["extracted", "Extracted"],
  ["processing", "Processing"],
  ["error", "Errors"],
] as const;

const SUPPLIER_ICONS: {
  match: RegExp;
  Icon: ComponentType<LucideProps>;
  tint: string;
}[] = [
  { match: /catering|restaur|comida|food|hotel/i, Icon: UtensilsCrossed, tint: "tint-meal" },
  { match: /transport|guadaira|logist|flete/i, Icon: Truck, tint: "tint-haul" },
  { match: /mensaj|courier|env[ií]o|postal|mail/i, Icon: Send, tint: "tint-post" },
  { match: /limpiez|clean|higiene/i, Icon: Sparkles, tint: "tint-clean" },
  { match: /papel|office|ruzafa|print|ofim[aá]tica/i, Icon: BookOpen, tint: "tint-paper" },
  { match: /suminist|supply|levante|material/i, Icon: Package, tint: "tint-supply" },
  { match: /inform[aá]tica|software|tech|digital/i, Icon: Monitor, tint: "tint-post" },
  { match: /electric|montcada|energ/i, Icon: Zap, tint: "tint-haul" },
  { match: /segurid|alcores|vigil/i, Icon: Shield, tint: "tint-meal" },
  { match: /construc|obras|benimac/i, Icon: Building2, tint: "tint-paper" },
  { match: /clim[aá]tica|aire|hvac/i, Icon: Sparkles, tint: "tint-clean" },
  { match: /consult|documental|aljarafe/i, Icon: BookOpen, tint: "tint-supply" },
];

function supplierMark(name: string) {
  const hit = SUPPLIER_ICONS.find(({ match }) => match.test(name));
  if (hit) return hit;
  const hue = [...name].reduce((n, c) => n + c.charCodeAt(0), 0) % 6;
  return {
    Icon: Building2,
    tint: (["tint-meal", "tint-haul", "tint-post", "tint-clean", "tint-paper", "tint-supply"] as const)[
      hue
    ],
  };
}

function countKey(value: string) {
  return value === "PAGAR"
    ? "PAY"
    : value === "ESCALAR"
      ? "ESCALATE"
      : "DO NOT PAY";
}

export function InvoicePage({
  onSelect,
}: {
  onSelect: (file: string) => void;
}) {
  const params = useSearchParams();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [action, setAction] = useState("");
  const [stage, setStage] = useState(params.get("stage") ?? "");
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
        <div className="inv-tools">
          <div className="seg inv-rec" aria-label="Recommendation">
            {actions.map(([value, label]) => {
              const n =
                value && data
                  ? (data.counts?.[countKey(value)] ?? 0)
                  : data?.matched;
              return (
                <button
                  key={value || "all"}
                  type="button"
                  className={action === value ? "on" : ""}
                  aria-pressed={action === value}
                  onClick={() => {
                    setAction(value);
                    setPage(1);
                  }}
                >
                  <span className="seg-lbl">{label}</span>
                  {n != null ? <span className="seg-n">{n}</span> : null}
                </button>
              );
            })}
          </div>
          <label className="inv-stage">
            <span className="inv-stage-lbl">Stage</span>
            <select
              value={stage}
              aria-label="Stage"
              onChange={(e) => {
                setStage(e.target.value);
                setPage(1);
              }}
            >
              {STAGES.map(([value, label]) => (
                <option key={value || "all"} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <Button
            className="btn inv-refresh"
            disabled={isValidating}
            onClick={() => {
              setRevision((v) => v + 1);
              void mutate();
            }}
          >
            <RefreshCw size={13} />
            Refresh
          </Button>
          <span className="inv-count" aria-live="polite">
            {data ? `${data.matched} / ${data.grand}` : ""}
          </span>
        </div>
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
  const { Icon, tint } = supplierMark(lane.name);
  return (
    <div className={`lane ${open ? "open" : ""}`}>
      <button
        className="lane-head"
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="cv2" aria-hidden />
        <span className={`prov ${tint}`} aria-hidden>
          <Icon size={14} strokeWidth={2.2} />
        </span>
        <span className="who3">
          <span className="nm2">{lane.name}</span>
          <span className="sub2">
            {lane.count}
            {" · "}
            {lane.currency === "UNKNOWN"
              ? "no currency"
              : lane.currency}
            {lane.missing_amounts
              ? ` · ${lane.missing_amounts} missing`
              : ""}
          </span>
        </span>
        <span className="figs">
          {(
            [
              ["pay", "Pay", lane.pay_total, lane.pay_n],
              ["rev", "Review", lane.review_total, lane.review_n],
              ["stop", "Hold", lane.nopay_total, lane.nopay_n],
            ] as const
          ).map(([kind, label, total, n]) => (
            <span key={kind} className={`fig ${kind}`}>
              <span className="lbl2">{label}</span>
              <span className={`amt ${n === 0 || total == null ? "zero" : ""}`}>
                {total == null ? "—" : money(total, lane.currency)}
              </span>
              <span className="n2">{n}</span>
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
          <span>Rec.</span>
          <span>Reason</span>
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
            <span className="a">
              {row.total == null ? "—" : money(row.total, row.currency)}
            </span>
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
