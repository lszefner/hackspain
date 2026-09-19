"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/Badge";
import { getFactura, lanzarUna, type FacturaDetalle } from "@/lib/api";

const VERDICT_ICON: Record<string, string> = {
  PASS: "text-emerald-600",
  FAIL: "text-red-600",
  NEEDS_REVIEW: "text-amber-600",
};

export default function FacturaPage(props: PageProps<"/factura/[id]">) {
  const { id } = use(props.params);
  const fileId = decodeURIComponent(id);
  const [factura, setFactura] = useState<FacturaDetalle | null>(null);
  const [loading, setLoading] = useState(false);

  const cargar = useCallback(async () => {
    try {
      setFactura(await getFactura(fileId));
    } catch {
      // keep last known state on transient errors
    }
  }, [fileId]);

  useEffect(() => {
    const t0 = setTimeout(cargar, 0);
    const t =
      factura?.estado === "procesando" ? setInterval(cargar, 1500) : null;
    return () => {
      clearTimeout(t0);
      if (t) clearInterval(t);
    };
  }, [cargar, factura?.estado]);

  return (
    <div className="min-h-full flex flex-col">
      <header className="sticky top-0 z-10 border-b border-zinc-200 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-4xl items-center gap-4 px-5 py-3">
          <Link href="/" className="text-sm text-zinc-500 hover:text-zinc-900">
            ← facturas
          </Link>
          <h1 className="truncate font-mono text-sm text-zinc-800">{fileId}</h1>
          <div className="ml-auto flex items-center gap-3">
            <Badge value={factura?.estado} />
            <button
              disabled={loading || factura?.estado === "procesando"}
              onClick={async () => {
                setLoading(true);
                try {
                  await lanzarUna(fileId);
                } finally {
                  await cargar();
                  setLoading(false);
                }
              }}
              className="rounded-lg bg-zinc-900 px-3.5 py-1.5 text-xs font-medium text-white transition hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {factura?.estado === "hecha" || factura?.estado === "error"
                ? "Revisar de nuevo"
                : "Revisar"}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-4xl flex-1 px-5 py-6">
        <div className="mb-6 flex items-center gap-3">
          <span className="text-sm text-zinc-500">decisión</span>
          <Badge value={factura?.decision} />
        </div>

        {factura?.error && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {factura.error}
          </div>
        )}

        {!factura && <p className="text-sm text-zinc-400">Cargando…</p>}

        {factura && factura.estado === "pendiente" && (
          <p className="text-sm text-zinc-500">
            Esta factura aún no ha sido revisada.
          </p>
        )}

        {factura && factura.estado !== "pendiente" && (
          <>
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wider text-zinc-500">
              Comprobaciones
            </h2>
            <div className="mb-8 divide-y divide-zinc-100 overflow-hidden rounded-xl border border-zinc-200 bg-white">
              {factura.checks.length === 0 && (
                <p className="px-4 py-3 text-sm text-zinc-400">sin checks</p>
              )}
              {factura.checks.map((c, i) => (
                <div key={i} className="flex items-start gap-3 px-4 py-3">
                  <span className={`mt-0.5 text-sm font-bold ${VERDICT_ICON[c.verdict] ?? ""}`}>
                    {c.verdict === "PASS" ? "✓" : c.verdict === "FAIL" ? "✗" : "!"}
                  </span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-zinc-800">
                        {c.canonical}
                      </span>
                      <Badge value={c.verdict} />
                    </div>
                    <p className="mt-0.5 text-sm text-zinc-500">{c.reason}</p>
                  </div>
                </div>
              ))}
            </div>

            <h2 className="mb-2 text-xs font-medium uppercase tracking-wider text-zinc-500">
              Campos extraídos
            </h2>
            <div className="overflow-hidden rounded-xl border border-zinc-200 bg-white">
              <table className="w-full text-sm">
                <tbody className="divide-y divide-zinc-100">
                  {Object.entries(factura.campos).filter(([k]) => k !== "file_id")
                    .length === 0 && (
                    <tr>
                      <td className="px-4 py-3 text-sm text-zinc-400">sin campos</td>
                    </tr>
                  )}
                  {Object.entries(factura.campos)
                    .filter(([k]) => k !== "file_id")
                    .map(([k, v]) => (
                      <tr key={k}>
                        <td className="w-48 px-4 py-2 font-mono text-xs text-zinc-500">
                          {k}
                        </td>
                        <td className="px-4 py-2 font-mono text-[13px] text-zinc-800">
                          {v === null || v === undefined ? (
                            <span className="text-zinc-300">—</span>
                          ) : (
                            JSON.stringify(v)
                          )}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
