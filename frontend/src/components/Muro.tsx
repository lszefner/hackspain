import Link from "next/link";
import type { Baldosa } from "@/lib/types";

const COLOR = {
  PAGAR: "bg-ok hover:bg-ok/70",
  ESCALAR: "bg-warn hover:bg-warn/70",
  NO_PAGAR: "bg-bad hover:bg-bad/70",
};

/** La pared de 500 baldosas: una por documento, clic = expediente. */
export function Muro({ baldosas, norma }: { baldosas: Baldosa[]; norma: string }) {
  return (
    <div className="grid grid-cols-[repeat(25,minmax(0,1fr))] gap-[3px]">
      {baldosas.map((b) => (
        <Link
          key={b.file_id}
          href={`/expediente/${encodeURIComponent(b.file_id)}?norma=${norma}`}
          title={`${b.file_id}${b.motivo ? ` — ${b.motivo}` : ""}`}
          className={`aspect-square rounded-[2px] transition-colors ${COLOR[b.result]}`}
        />
      ))}
    </div>
  );
}
