import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const runtime = "nodejs";

type Payload = { pay?: number; pay_eur?: number };

const money = (v: number) =>
  "€" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/**
 * state.py: notify(). The payment confirmation a supplier gets once a batch
 * clears. Nothing is sent, and nothing here pretends otherwise: the verdicts
 * in a dropped batch are demo data, and La Caja carries no supplier address --
 * the master holds id, NIF, IBAN, city and terms, and no way to reach anybody.
 * Pure, so Vercel answers exactly what the local desk answers.
 */
function notify(payload: Payload | null) {
  const n = Number(payload?.pay ?? 0);
  const eur = Number(payload?.pay_eur ?? 0);
  return {
    said:
      `Drafted the payment confirmation for the ${n} approved, ${money(eur)}, ` +
      `one per supplier: what we owe, against which order, and the date it runs. ` +
      `Nothing left the building -- these verdicts are demo data, and La Caja ` +
      `carries no supplier address to send them to.`,
    undo: false,
    refresh: false,
  };
}

export async function POST(request: NextRequest) {
  let body: { act?: string; payload?: Payload };
  try {
    body = await request.json();
  } catch {
    return new NextResponse("bad request", { status: 400 });
  }
  const act = String(body.act ?? "");
  if (act === "notify") return NextResponse.json(notify(body.payload ?? null));
  const said =
    act === "undo"
      ? "There is nothing to put back: this deployment does not keep state between requests."
      : "This is the read-only deployment, so I did not do it. The numbers and the reasoning are real, " +
        "but approving, rejecting and writing to anyone only work on the desk that runs locally.";
  return NextResponse.json({ said, undo: false, refresh: false });
}
