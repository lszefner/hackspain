"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/Badge";
import { getResumen, lanzarLote, lanzarUna, type Resumen } from "@/lib/api";

export default function Home() {
  const [resumen, setResumen] = useState<Resumen | null>(null);
  const [loading, setLoading] = useState(false);

  const cargar = useCallback(async () => {
    try {
      setResumen(await getResumen());
    } catch {
      // backend not reachable yet; keep last known state
    }
  }, []);

  useEffect(() => {
    cargar();
    const id = setInterval(cargar, 4000);
    return () => clearInterval(id);
  }, [cargar]);

  if (!resumen) {
    return (
      <main className="p-6 font-sans">
        <p>Conectando con el backend ({process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8010"})&hellip;</p>
      </main>
    );
  }

  const disabled = resumen.procesando || loading;

  return (
    <main className="p-6 font-sans max-w-5xl mx-auto">
      <h1 className="text-xl font-semibold mb-2">
        Revision de facturas &middot; {resumen.total} en facturas/
      </h1>

      {resumen.procesando && (
        <div className="bg-amber-50 border border-amber-300 rounded px-3 py-2 my-3 text-sm">
          Procesando un lote&hellip; esta pagina se actualiza sola cada 4s.
        </div>
      )}
      {!resumen.procesando && resumen.error && (
        <div className="bg-amber-50 border border-amber-300 rounded px-3 py-2 my-3 text-sm">
          Ultimo error: {resumen.error}
        </div>
      )}

      <div className="flex gap-4 my-5 flex-wrap">
        {[
          ["Pendientes", resumen.conteo.pendiente],
          ["Hechas", resumen.conteo.hecha],
          ["Con error", resumen.conteo.error],
        ].map(([label, n]) => (
          <div key={label as string} className="bg-white border rounded-md px-4 py-2 min-w-[110px]">
            {label}
            <b className="block text-xl">{n}</b>
          </div>
        ))}
        {(["PAGAR", "ESCALAR", "NO_PAGAR"] as const).map((k) => (
          <div key={k} className="bg-white border rounded-md px-4 py-2 min-w-[110px]">
            {k}
            <b className="block text-xl">
              <Badge value={k} /> {resumen.decisiones[k]}
            </b>
          </div>
        ))}
      </div>

      <button
        disabled={disabled}
        onClick={async () => {
          setLoading(true);
          await lanzarLote();
          await cargar();
          setLoading(false);
        }}
        className="px-3 py-1.5 border rounded bg-gray-50 disabled:opacity-50"
      >
        Lanzar revision (siguiente lote de {resumen.lote_tamano} pendientes)
      </button>

      <p className="text-xs text-gray-500 mt-3">
        Cada revision llama de verdad a Helmcode (OCR + DeepSeek) y aplica las 6 reglas de
        rules_ingestion/checks.py contra el maestro de proveedores/pedidos y el ERP local.
      </p>

      <table className="w-full bg-white border-collapse mt-4 text-sm">
        <thead>
          <tr className="bg-gray-100">
            <th className="border px-2 py-1 text-left">Factura</th>
            <th className="border px-2 py-1 text-left">Estado</th>
            <th className="border px-2 py-1 text-left">Decision</th>
            <th className="border px-2 py-1" />
          </tr>
        </thead>
        <tbody>
          {resumen.facturas.map((f) => (
            <tr key={f.file_id}>
              <td className="border px-2 py-1">
                <Link href={`/factura/${encodeURIComponent(f.file_id)}`} className="text-blue-700 underline">
                  {f.file_id}
                </Link>
              </td>
              <td className="border px-2 py-1">
                <Badge value={f.estado} />
              </td>
              <td className="border px-2 py-1">
                <Badge value={f.decision} />
              </td>
              <td className="border px-2 py-1">
                <button
                  disabled={disabled}
                  onClick={async () => {
                    setLoading(true);
                    await lanzarUna(f.file_id);
                    await cargar();
                    setLoading(false);
                  }}
                  className="px-2 py-1 border rounded bg-gray-50 disabled:opacity-50 text-xs"
                >
                  Revisar
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
