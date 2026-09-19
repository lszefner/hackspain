"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/Badge";
import {
  getResumen,
  lanzarLote,
  lanzarUna,
  type Resumen,
} from "@/lib/api";

function Stat({
  label,
  value,
  dot,
}: {
  label: string;
  value: number;
  dot?: string;
}) {
  return (
    <div className="flex items-baseline gap-2">
      {dot && <span className={`size-2 rounded-full ${dot}`} />}
      <span className="text-lg font-semibold tabular-nums">{value}</span>
      <span className="text-xs text-zinc-500">{label}</span>
    </div>
  );
}

export default function Home() {
  const [resumen, setResumen] = useState<Resumen | null>(null);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [filtro, setFiltro] = useState<string>("todas");

  const cargar = useCallback(async () => {
    try {
      setResumen(await getResumen());
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, []);

  useEffect(() => {
    const t0 = setTimeout(cargar, 0);
    const ms = resumen?.procesando ? 1500 : 5000;
    const t = setInterval(cargar, ms);
    return () => {
      clearTimeout(t0);
      clearInterval(t);
    };
  }, [cargar, resumen?.procesando]);

  const facturas = useMemo(() => {
    const all = resumen?.facturas ?? [];
    const q = query.trim().toLowerCase();
    return all.filter((f) => {
      if (q && !f.file_id.toLowerCase().includes(q)) return false;
      if (filtro === "aptas") return f.decision === "PAGAR";
      if (filtro === "escalar") return f.decision === "ESCALAR";
      if (filtro === "no_aptas") return f.decision === "NO_PAGAR";
      if (filtro === "pendientes") return f.estado === "pendiente";
      if (filtro === "errores") return f.estado === "error";
      return true;
    });
  }, [resumen, query, filtro]);

  const pendientes = resumen?.conteo.pendiente ?? 0;
  const disabled = !resumen || resumen.procesando || busy;

  const procesarLote = async () => {
    setBusy(true);
    try {
      await lanzarLote();
    } finally {
      await cargar();
      setBusy(false);
    }
  };

  const procesarUna = async (fileId: string) => {
    setBusy(true);
    try {
      await lanzarUna(fileId);
    } finally {
      await cargar();
      setBusy(false);
    }
  };

  return (
    <div className="min-h-full flex flex-col">
      <header className="sticky top-0 z-10 border-b border-zinc-200 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-4 px-5 py-3">
          <div>
            <h1 className="text-sm font-semibold tracking-tight">
              Revisión de facturas
            </h1>
            <p className="text-xs text-zinc-500">
              extracción + reglas · {resumen ? `${resumen.total} documentos` : "…"}
            </p>
          </div>
          <div className="ml-auto flex items-center gap-3">
            <span
              className={`inline-flex items-center gap-1.5 text-xs ${offline ? "text-red-600" : resumen?.procesando ? "text-amber-600" : "text-zinc-500"}`}
            >
              <span
                className={`size-1.5 rounded-full ${offline ? "bg-red-500" : resumen?.procesando ? "bg-amber-500 pulse-dot" : "bg-emerald-500"}`}
              />
              {offline
                ? "backend desconectado"
                : resumen?.procesando
                  ? "procesando…"
                  : "en línea"}
            </span>
            <button
              onClick={procesarLote}
              disabled={disabled || pendientes === 0}
              className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {resumen?.procesando
                ? "Procesando…"
                : pendientes > 0
                  ? `Procesar ${Math.min(resumen?.lote_tamano ?? 0, pendientes)} pendientes`
                  : "Todo revisado"}
            </button>
          </div>
        </div>
        {resumen?.procesando && (
          <div className="h-0.5 w-full overflow-hidden bg-amber-100">
            <div className="h-full w-1/3 animate-[pulse-dot_1.1s_ease-in-out_infinite] bg-amber-500" />
          </div>
        )}
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-5 py-5">
        {offline && (
          <div className="mb-4 flex items-center justify-between rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            <span>
              No se puede contactar con el backend en{" "}
              <code className="font-mono">127.0.0.1:8010</code>.
            </span>
            <button
              onClick={cargar}
              className="rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-medium hover:bg-red-100"
            >
              Reintentar
            </button>
          </div>
        )}

        {resumen?.error && (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            {resumen.error}
          </div>
        )}

        {resumen && (
          <div className="mb-5 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-zinc-200 bg-white px-4 py-3">
            <Stat label="pendientes" value={resumen.conteo.pendiente} dot="bg-zinc-300" />
            <Stat label="procesando" value={resumen.conteo.procesando} dot="bg-amber-400" />
            <Stat label="revisadas" value={resumen.conteo.hecha} dot="bg-zinc-500" />
            <Stat label="errores" value={resumen.conteo.error} dot="bg-red-400" />
            <span className="mx-1 hidden h-5 w-px bg-zinc-200 sm:block" />
            <Stat label="a pagar" value={resumen.decisiones.PAGAR} dot="bg-emerald-500" />
            <Stat label="a escalar" value={resumen.decisiones.ESCALAR} dot="bg-amber-500" />
            <Stat label="no pagar" value={resumen.decisiones.NO_PAGAR} dot="bg-red-500" />
          </div>
        )}

        <div className="mb-3 flex items-center gap-3">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Buscar factura…"
            className="w-64 rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-sm outline-none placeholder:text-zinc-400 focus:border-zinc-400"
          />
          <div className="flex gap-1 text-xs">
            {[
              ["todas", "todas"],
              ["aptas", "a pagar"],
              ["escalar", "a escalar"],
              ["no_aptas", "no pagar"],
              ["pendientes", "pendientes"],
              ["errores", "errores"],
            ].map(([k, label]) => (
              <button
                key={k}
                onClick={() => setFiltro(k)}
                className={`rounded-full border px-2.5 py-1 transition ${
                  filtro === k
                    ? "border-zinc-900 bg-zinc-900 text-white"
                    : "border-zinc-200 bg-white text-zinc-500 hover:border-zinc-400"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <span className="ml-auto text-xs tabular-nums text-zinc-400">
            {facturas.length} / {resumen?.total ?? 0}
          </span>
        </div>

        <div className="overflow-hidden rounded-xl border border-zinc-200 bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-200 bg-zinc-50 text-left text-xs text-zinc-500">
                <th className="px-4 py-2 font-medium">factura</th>
                <th className="px-4 py-2 font-medium">estado</th>
                <th className="px-4 py-2 font-medium">decisión</th>
                <th className="px-4 py-2 text-right font-medium">acción</th>
              </tr>
            </thead>
            <tbody>
              {facturas.map((f) => (
                <tr
                  key={f.file_id}
                  className="border-b border-zinc-100 last:border-0 hover:bg-zinc-50/60"
                >
                  <td className="px-4 py-2">
                    <Link
                      href={`/factura/${encodeURIComponent(f.file_id)}`}
                      className="font-mono text-[13px] text-zinc-800 hover:underline"
                    >
                      {f.file_id}
                    </Link>
                  </td>
                  <td className="px-4 py-2">
                    <Badge value={f.estado} />
                  </td>
                  <td className="px-4 py-2">
                    <Badge value={f.decision} />
                  </td>
                  <td className="px-4 py-2 text-right">
                    <button
                      onClick={() => procesarUna(f.file_id)}
                      disabled={disabled || f.estado === "procesando"}
                      className="rounded-md border border-zinc-200 px-2.5 py-1 text-xs font-medium text-zinc-600 transition hover:border-zinc-400 hover:text-zinc-900 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {f.estado === "pendiente" ? "Revisar" : "Revisar de nuevo"}
                    </button>
                  </td>
                </tr>
              ))}
              {facturas.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-10 text-center text-sm text-zinc-400">
                    {resumen ? "Sin resultados para este filtro." : "Cargando…"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </main>
    </div>
  );
}
