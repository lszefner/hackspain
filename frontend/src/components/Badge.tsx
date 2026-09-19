import type { Resultado, Veredicto } from "@/lib/types";

const RES: Record<Resultado, string> = {
  PAGAR: "bg-ok/10 text-ok border-ok/30",
  ESCALAR: "bg-warn/10 text-warn border-warn/30",
  NO_PAGAR: "bg-bad/10 text-bad border-bad/30",
};

export function ResultBadge({ result, grande = false }: { result: Resultado; grande?: boolean }) {
  return (
    <span
      className={`inline-block rounded-full border font-mono font-semibold tracking-wide ${RES[result]} ${
        grande ? "px-4 py-1 text-lg" : "px-2.5 py-0.5 text-xs"
      }`}
    >
      {result.replace("_", " ")}
    </span>
  );
}

const VER: Record<Veredicto, [string, string]> = {
  CUMPLE: ["✓", "text-ok"],
  FALLA: ["✗", "text-bad"],
  SIN_DATOS: ["–", "text-warn"],
};

export function VeredictoIcono({ veredicto }: { veredicto: Veredicto }) {
  const [icono, color] = VER[veredicto];
  return <span className={`font-mono text-lg font-bold ${color}`}>{icono}</span>;
}
