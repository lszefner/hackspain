"use client";

import { useTransition } from "react";
import { activar } from "./actions";

export function BotonActivar({ version }: { version: string }) {
  const [pendiente, start] = useTransition();
  return (
    <button
      onClick={() => start(() => activar(version))}
      disabled={pendiente}
      className="rounded-full border border-accent/40 bg-accent/10 px-3 py-0.5 font-mono text-xs font-bold text-accent hover:bg-accent/20 disabled:opacity-50"
      title="El muro, la bandeja y los KPIs pasan a usar esta versión por defecto"
    >
      {pendiente ? "activando…" : "usar esta versión"}
    </button>
  );
}
