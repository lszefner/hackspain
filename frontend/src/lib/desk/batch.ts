/**
 * state.py: batch_block() -- the result of a dropped batch, as one panel.
 * Ported rather than frozen because the batch depends on what the reader
 * just dropped, so it cannot be precomputed like the other blocks.
 */
const money = (v: number) =>
  "€" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export type Batch = {
  label: string; total: number; size: string; seconds: string;
  pay: number; pay_eur: number; escalate: number; escalate_eur: number;
  patterns: number; nopay: number; nopay_eur: number;
  dupes: string[]; rejected: [string, string][];
};

export function batchBlock(b: Batch) {
  const rows: Record<string, unknown>[] = [];
  if (b.pay)
    rows.push({ lead: String(b.pay), title: "pay", sub: "clean against the rules",
                value: money(b.pay_eur), tone: "good" });
  if (b.escalate)
    rows.push({ lead: String(b.escalate), title: "escalate",
                sub: `${b.patterns} pattern${b.patterns !== 1 ? "s" : ""}, waiting on you`,
                value: money(b.escalate_eur), tone: "warn" });
  if (b.nopay)
    rows.push({ lead: String(b.nopay), title: "do not pay", sub: "blocked by a rule",
                value: money(b.nopay_eur) });
  if (b.dupes.length)
    rows.push({ lead: String(b.dupes.length), title: "already had them",
                sub: b.dupes.slice(0, 3).join(", ") + (b.dupes.length > 3 ? "…" : ""),
                value: "not run again" });
  if (b.rejected.length)
    rows.push({ lead: String(b.rejected.length), title: "could not read",
                sub: b.rejected.slice(0, 3).map(([n, why]) => `${n} — ${why}`).join("; "),
                value: "not in" });

  const actions: Record<string, unknown>[] = [];
  if (b.escalate)
    actions.push({ label: `Start with the ${b.escalate} escalated`, kind: "primary",
                   ask: "which invoices do I need to review" });
  if (b.pay)
    // the confirmation the supplier gets once the payment is set to run;
    // the button carries the batch because no request keeps it
    actions.push({
      label: `Confirm payment to the suppliers of the ${b.pay}`,
      act: "notify",
      payload: { pay: b.pay, pay_eur: b.pay_eur },
    });
  actions.push(
    { label: "See the whole batch", ui: "invoices" },
    { label: "See the batch trace", kind: "quiet", ask: "how did it go" },
  );

  return {
    id: "batch", title: b.label,
    meta: `${b.total} invoices · ${b.size} · ${b.seconds} · demo`,
    rows, actions,
  };
}
