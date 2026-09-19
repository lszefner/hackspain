"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/Badge";
import { getFactura, lanzarUna, type FacturaDetalle } from "@/lib/api";

export default function FacturaPage(props: PageProps<"/factura/[id]">) {
  const { id } = use(props.params);
  const fileId = decodeURIComponent(id);
  const [factura, setFactura] = useState<FacturaDetalle | null>(null);
  const [loading, setLoading] = useState(false);

  const cargar = useCallback(async () => {
    try {
      setFactura(await getFactura(fileId));
    } catch {
      // ignore transient errors, keep last known state
    }
  }, [fileId]);

  useEffect(() => {
    cargar();
  }, [cargar]);

  return (
    <main className="p-6 font-sans max-w-3xl mx-auto">
      <p className="mb-3">
        <Link href="/" className="text-blue-700 underline">
          &larr; volver
        </Link>
      </p>
      <h1 className="text-xl font-semibold mb-3">
        {fileId} &middot; <Badge value={factura?.decision} />
      </h1>

      {factura?.error && (
        <div className="bg-amber-50 border border-amber-300 rounded px-3 py-2 my-3 text-sm">
          {factura.error}
        </div>
      )}

      <button
        disabled={loading || factura?.estado === "procesando"}
        onClick={async () => {
          setLoading(true);
          await lanzarUna(fileId);
          await cargar();
          setLoading(false);
        }}
        className="px-3 py-1.5 border rounded bg-gray-50 disabled:opacity-50"
      >
        {factura?.estado === "hecha" || factura?.estado === "error" ? "Volver a revisar" : "Revisar ahora"}
      </button>

      {factura && factura.estado !== "pendiente" && (
        <>
          <h3 className="text-base font-semibold mt-6 mb-2">Checks (rules_ingestion/checks.py)</h3>
          <div className="space-y-2">
            {factura.checks.length === 0 && <p className="text-sm text-gray-500">sin checks</p>}
            {factura.checks.map((c, i) => (
              <div key={i} className="bg-white border rounded px-3 py-2 text-sm">
                <b>
                  <Badge value={c.verdict} />
                </b>{" "}
                &middot; {c.canonical}
                <br />
                {c.reason}
              </div>
            ))}
          </div>

          <h3 className="text-base font-semibold mt-6 mb-2">
            Campos extraidos (mapeados a rules_ingestion.invoice)
          </h3>
          <table className="w-full bg-white border-collapse text-sm">
            <tbody>
              {Object.entries(factura.campos)
                .filter(([k]) => k !== "file_id")
                .map(([k, v]) => (
                  <tr key={k}>
                    <td className="border px-2 py-1">{k}</td>
                    <td className="border px-2 py-1">{JSON.stringify(v)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </>
      )}
    </main>
  );
}
