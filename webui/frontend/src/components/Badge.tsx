const COLORS: Record<string, string> = {
  pendiente: "text-gray-500",
  procesando: "text-amber-600",
  hecha: "text-green-700",
  error: "text-red-700",
  PAGAR: "text-green-700 font-bold",
  ESCALAR: "text-amber-600 font-bold",
  NO_PAGAR: "text-red-700 font-bold",
  PASS: "text-green-700",
  FAIL: "text-red-700 font-bold",
  NEEDS_REVIEW: "text-amber-600 font-bold",
};

export function Badge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-gray-400">&mdash;</span>;
  return <span className={COLORS[value] ?? ""}>{value}</span>;
}
