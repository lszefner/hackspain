/**
 * The folds `desk/mock/state.py` does per request, in TypeScript.
 *
 * Only the ones that take arguments live here; everything with a fixed answer
 * was frozen by desk/mock/export_static.py and is served straight from JSON.
 * Keep these in step with state.py: same filters, same sort, same rounding.
 */
import archive from "@/desk-data/archive.json";

export type Row = {
  file: string; vendor: string; vendor_id: string; number: string; date: string;
  total: number; action: "PAY" | "ESCALATE" | "DO NOT PAY";
  blocking: string[]; found: string; scanned: boolean; terms: string;
};

const ROWS = archive as unknown as Row[];
const round2 = (n: number) => Math.round(n * 100) / 100;

/** state.py: lanes(q, action) -- one box per supplier. */
export function lanes(q = "", action = "") {
  let rows = ROWS;
  if (q) {
    const n = q.toLowerCase();
    rows = rows.filter(
      (r) =>
        r.file.toLowerCase().includes(n) ||
        r.vendor.toLowerCase().includes(n) ||
        r.number.toLowerCase().includes(n),
    );
  }
  if (action) rows = rows.filter((r) => r.action === action);

  const by = new Map<string, Row[]>();
  for (const r of rows) {
    const k = r.vendor_id;
    if (!by.has(k)) by.set(k, []);
    by.get(k)!.push(r);
  }

  const total = (items: Row[], kind: Row["action"]) =>
    round2(items.filter((i) => i.action === kind).reduce((s, i) => s + i.total, 0));

  const out = [...by.entries()].map(([id, items]) => ({
    id,
    name: items[0].vendor,
    count: items.length,
    pay_eur: total(items, "PAY"),
    review_eur: total(items, "ESCALATE"),
    nopay_eur: total(items, "DO NOT PAY"),
    pay_n: items.filter((i) => i.action === "PAY").length,
    review_n: items.filter((i) => i.action === "ESCALATE").length,
    nopay_n: items.filter((i) => i.action === "DO NOT PAY").length,
    terms: items[0].terms,
    // the ones that need him first, then by filename -- as in state.py
    rows: [...items]
      .sort((a, b) => {
        const ae = a.action !== "ESCALATE" ? 1 : 0;
        const be = b.action !== "ESCALATE" ? 1 : 0;
        // by code point, as Python sorts: localeCompare collates accents
        // differently and would reorder the Spanish filenames
        return ae - be || (a.file < b.file ? -1 : a.file > b.file ? 1 : 0);
      })
      .map(({ file, number, date, total, action, blocking, found, scanned }) => ({
        file, number, date, total, action, blocking, found, scanned,
      })),
  }));

  // the supplier that needs him most goes first
  out.sort(
    (a, b) => b.review_eur - a.review_eur || b.nopay_eur - a.nopay_eur || b.count - a.count,
  );

  const every = ROWS;
  return {
    lanes: out,
    matched: rows.length,
    grand: every.length,
    counts: {
      PAY: rows.filter((r) => r.action === "PAY").length,
      ESCALATE: rows.filter((r) => r.action === "ESCALATE").length,
      "DO NOT PAY": rows.filter((r) => r.action === "DO NOT PAY").length,
    },
  };
}

/** serve.py: route(text) -- the blocks are chosen here, never by the model. */
export function fold(s: string) {
  return s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();
}
