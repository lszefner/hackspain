"use client";
import Link from "next/link";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { actionName, money, type Summary } from "@/lib/desk/types";
import { useResource, Loading, Failure } from "./resource";
export function SummaryPage() {
  const { data, error, mutate, isValidating } =
    useResource<Summary>("/api/summary");
  return (
    <div className="scroll">
      <div className="wide">
        {error ? (
          <Failure error={error} retry={() => void mutate()} />
        ) : !data ? (
          <Loading label="Loading recorded totals…" />
        ) : (
          <>
            <div className="summary-toolbar">
              <h2>Recorded invoices</h2>
              <Button
                className="btn"
                disabled={isValidating}
                onClick={() => void mutate()}
              >
                <RefreshCw size={13} />
                Refresh
              </Button>
            </div>
            <p className="lede">
              {data.total} persisted invoices · {data.suppliers} suppliers.
              Amounts are separated by currency; missing amounts are excluded.
            </p>
            {data.total === 0 ? (
              <p className="empty-note">No invoices recorded yet.</p>
            ) : (
              <div className="stats">
                {data.totals.map((total) => (
                  <div
                    key={`${total.currency}:${total.verdict}`}
                    className={`stat ${total.verdict === "PAGAR" ? "pay" : total.verdict === "NO_PAGAR" ? "stop" : "rev"}`}
                  >
                    <span className="k5">
                      {actionName(total.verdict)} · {total.currency}
                    </span>
                    <div className="v5">
                      {total.currency === "UNKNOWN"
                        ? "Not aggregated"
                        : money(total.total, total.currency)}
                    </div>
                    <div className="s5">
                      {total.n} invoices · {total.missing_amounts} missing
                      amounts
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div className="card2">
              <h3>Processing stages</h3>
              {Object.entries(data.lifecycle).map(([stage, count]) => (
                <div className="stage-row" key={stage}>
                  <Link
                    href={`/?view=invoices&stage=${encodeURIComponent(stage)}`}
                  >
                    {stage}
                  </Link>
                  <span className="mono">{count}</span>
                </div>
              ))}
            </div>
            <p className="lede">
              Recommendations from saved results. Resolution and payment are not
              recorded.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
