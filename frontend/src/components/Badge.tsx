const TONES: Record<string, string> = {
  pendiente: "border-zinc-200 bg-zinc-50 text-zinc-500",
  procesando: "border-amber-200 bg-amber-50 text-amber-700",
  hecha: "border-zinc-200 bg-white text-zinc-600",
  error: "border-red-200 bg-red-50 text-red-700",
  PAGAR: "border-emerald-200 bg-emerald-50 text-emerald-700",
  ESCALAR: "border-amber-200 bg-amber-50 text-amber-700",
  NO_PAGAR: "border-red-200 bg-red-50 text-red-700",
  PASS: "border-emerald-200 bg-emerald-50 text-emerald-700",
  FAIL: "border-red-200 bg-red-50 text-red-700",
  NEEDS_REVIEW: "border-amber-200 bg-amber-50 text-amber-700",
};

const DOTS: Record<string, string> = {
  pendiente: "bg-zinc-400",
  procesando: "bg-amber-500 pulse-dot",
  hecha: "bg-zinc-400",
  error: "bg-red-500",
  PAGAR: "bg-emerald-500",
  ESCALAR: "bg-amber-500",
  NO_PAGAR: "bg-red-500",
  PASS: "bg-emerald-500",
  FAIL: "bg-red-500",
  NEEDS_REVIEW: "bg-amber-500",
};

const LABELS: Record<string, string> = {
  pendiente: "pendiente",
  procesando: "procesando",
  hecha: "revisada",
  error: "error",
  PAGAR: "PAGAR",
  ESCALAR: "ESCALAR",
  NO_PAGAR: "NO PAGAR",
  PASS: "pasa",
  FAIL: "falla",
  NEEDS_REVIEW: "revisar",
};

export function Badge({ value }: { value: string | null | undefined }) {
  if (!value)
    return <span className="text-zinc-300">—</span>;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium tracking-wide ${TONES[value] ?? "border-zinc-200 bg-zinc-50 text-zinc-600"}`}
    >
      <span className={`size-1.5 rounded-full ${DOTS[value] ?? "bg-zinc-400"}`} />
      {LABELS[value] ?? value}
    </span>
  );
}
