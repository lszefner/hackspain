import Link from "next/link";
import { data } from "@/lib/data";
import type { Resultado } from "@/lib/types";
import { eur, num } from "@/lib/format";
import { Card } from "@/components/Card";
import { ResultBadge } from "@/components/Badge";

export const metadata = { title: "Facturas · Albertito" };
export const dynamic = "force-dynamic";

const RESULTADOS: Resultado[] = ["PAGAR", "ESCALAR", "NO_PAGAR"];

export default async function FacturasPage({
  searchParams,
}: {
  searchParams: Promise<{ norma?: string; result?: string; q?: string }>;
}) {
  const sp = await searchParams;
  const norma = sp.norma && data.normas().includes(sp.norma) ? sp.norma : data.normaActiva();
  const result = (RESULTADOS.includes(sp.result as Resultado) ? sp.result : undefined) as Resultado | undefined;
  const q = sp.q || undefined;
  const filas = await data.facturas({ norma, result, q });

  const url = (r?: Resultado) =>
    `/facturas?norma=${norma}${r ? `&result=${r}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Facturas</h1>
        <p className="mt-1 text-sm text-muted">
          El listado crudo: en qué punto está cada documento y todo lo relevante de su expediente.
          Clic en cualquiera para seguir su decisión de punta a punta.
        </p>
      </div>

      {/* filtros */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-2">
          {RESULTADOS.map((r) => (
            <Link
              key={r}
              href={url(result === r ? undefined : r)}
              className={`rounded-full border px-3 py-1 font-mono text-xs font-semibold ${
                result === r ? "border-ink bg-ink text-paper" : "border-line bg-card text-muted hover:text-ink"
              }`}
            >
              {r.replace("_", " ")}
            </Link>
          ))}
        </div>
        <form action="/facturas" className="flex items-center gap-2">
          <input type="hidden" name="norma" value={norma} />
          {result && <input type="hidden" name="result" value={result} />}
          <input
            name="q"
            defaultValue={q ?? ""}
            placeholder="buscar file_id…"
            className="w-56 rounded-md border border-line bg-card px-3 py-1.5 font-mono text-sm outline-none focus:border-accent"
          />
        </form>
        <div className="ml-auto flex items-center gap-2 text-sm">
          <span className="text-muted">norma</span>
          {data.normas().map((n) => (
            <Link
              key={n}
              href={`/facturas?norma=${n}${result ? `&result=${result}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`}
              className={`rounded-md border px-3 py-1 font-mono ${
                n === norma ? "border-ink bg-ink text-paper" : "border-line bg-card text-muted hover:text-ink"
              }`}
            >
              {n}
            </Link>
          ))}
        </div>
      </div>

      <Card titulo={`${num(filas.length)} facturas · norma ${norma}`} className="!p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-card">
              <tr className="text-left uppercase tracking-wide text-muted">
                <th className="px-4 py-2.5">factura</th>
                <th className="px-2 py-2.5">etapa</th>
                <th className="px-2 py-2.5">vía</th>
                <th className="px-2 py-2.5">proveedor</th>
                <th className="px-2 py-2.5">nif</th>
                <th className="px-2 py-2.5">pedido</th>
                <th className="px-2 py-2.5 text-right">total</th>
                <th className="px-2 py-2.5">decisión</th>
                <th className="px-2 py-2.5">motivo</th>
                <th className="px-2 py-2.5 text-right">ms</th>
                <th className="px-4 py-2.5 text-right">€</th>
              </tr>
            </thead>
            <tbody>
              {filas.map((f) => (
                <tr key={f.file_id} className="border-t border-line hover:bg-soft/50">
                  <td className="px-4 py-2">
                    <Link
                      href={`/expediente/${encodeURIComponent(f.file_id)}?norma=${norma}`}
                      className="font-mono text-accent underline underline-offset-2"
                    >
                      {f.file_id}
                    </Link>
                  </td>
                  <td className="px-2 py-2">
                    <span className="rounded-full bg-soft px-2 py-0.5 font-mono text-[10px] font-semibold text-muted">
                      {f.etapa}
                    </span>
                  </td>
                  <td className="px-2 py-2 font-mono">
                    {f.via ?? <span className="text-warn">sin extraer</span>}
                  </td>
                  <td className="max-w-44 truncate px-2 py-2">{f.proveedor ?? "—"}</td>
                  <td className="px-2 py-2 font-mono">{f.nif ?? "—"}</td>
                  <td className="px-2 py-2 font-mono">{f.pedido ?? "—"}</td>
                  <td className="px-2 py-2 text-right font-mono">{eur(f.total_cent)}</td>
                  <td className="px-2 py-2"><ResultBadge result={f.result} /></td>
                  <td className="max-w-64 truncate px-2 py-2 text-muted" title={f.motivo ?? undefined}>
                    {f.motivo ?? "—"}
                  </td>
                  <td className="px-2 py-2 text-right font-mono">{f.latencia_ms}</td>
                  <td className="px-4 py-2 text-right font-mono">
                    {f.coste_eur === 0 ? "0" : f.coste_eur.toFixed(2).replace(".", ",")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
