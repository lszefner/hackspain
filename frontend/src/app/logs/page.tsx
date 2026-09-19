import Link from "next/link";
import { data } from "@/lib/data";
import { hora, num } from "@/lib/format";
import { Card } from "@/components/Card";

export const metadata = { title: "Logs · tito.ai" };
export const dynamic = "force-dynamic";

const ETAPAS = ["ingesta", "extrae", "decide"];
const NIVELES = ["info", "warn", "error"];
const PUNTO: Record<string, string> = { info: "bg-ok", warn: "bg-warn", error: "bg-bad" };

export default async function LogsPage({
  searchParams,
}: {
  searchParams: Promise<{ etapa?: string; nivel?: string; q?: string }>;
}) {
  const sp = await searchParams;
  const etapa = ETAPAS.includes(sp.etapa ?? "") ? sp.etapa : undefined;
  const nivel = NIVELES.includes(sp.nivel ?? "") ? sp.nivel : undefined;
  const q = sp.q || undefined;
  const { filas, total } = await data.eventos({ etapa, nivel, q, limit: 200 });

  const url = (p: { etapa?: string; nivel?: string }) => {
    const e = "etapa" in p ? p.etapa : etapa;
    const n = "nivel" in p ? p.nivel : nivel;
    const qs = [e && `etapa=${e}`, n && `nivel=${n}`, q && `q=${encodeURIComponent(q)}`]
      .filter(Boolean).join("&");
    return `/logs${qs ? `?${qs}` : ""}`;
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Logs</h1>
        <p className="mt-1 text-sm text-muted">
          El registro de eventos del pipeline: cuándo se ingirió, extrajo y decidió cada factura.
          Nada ocurre sin dejar rastro — esta tabla es la prueba.
        </p>
      </div>

      {/* filtros */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-2">
          {ETAPAS.map((e) => (
            <Link key={e} href={url({ etapa: etapa === e ? undefined : e })}
              className={`rounded-full border px-3 py-1 font-mono text-xs font-semibold ${
                etapa === e ? "border-ink bg-ink text-paper" : "border-line bg-card text-muted hover:text-ink"
              }`}>
              {e}
            </Link>
          ))}
        </div>
        <div className="flex gap-2">
          {NIVELES.map((n) => (
            <Link key={n} href={url({ nivel: nivel === n ? undefined : n })}
              className={`flex items-center gap-1.5 rounded-full border px-3 py-1 font-mono text-xs font-semibold ${
                nivel === n ? "border-ink bg-ink text-paper" : "border-line bg-card text-muted hover:text-ink"
              }`}>
              <span className={`h-1.5 w-1.5 rounded-full ${PUNTO[n]}`} />
              {n}
            </Link>
          ))}
        </div>
        <form action="/logs" className="flex items-center gap-2">
          {etapa && <input type="hidden" name="etapa" value={etapa} />}
          {nivel && <input type="hidden" name="nivel" value={nivel} />}
          <input
            name="q"
            defaultValue={q ?? ""}
            placeholder="buscar factura o mensaje…"
            className="w-64 rounded-md border border-line bg-card px-3 py-1.5 font-mono text-sm outline-none focus:border-accent"
          />
        </form>
        <span className="ml-auto text-xs text-muted">
          {filas.length < total ? `últimos ${num(filas.length)} de ${num(total)} eventos` : `${num(total)} eventos`}
        </span>
      </div>

      <Card className="!p-0">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left uppercase tracking-wide text-muted">
              <th className="px-4 py-2.5">cuándo</th>
              <th className="px-2 py-2.5">nivel</th>
              <th className="px-2 py-2.5">etapa</th>
              <th className="px-2 py-2.5">factura</th>
              <th className="px-4 py-2.5">qué pasó</th>
            </tr>
          </thead>
          <tbody className="font-mono">
            {filas.map((e) => (
              <tr key={e.id} className="border-t border-line hover:bg-soft/50">
                <td className="whitespace-nowrap px-4 py-1.5 text-muted">
                  {e.at.slice(0, 10)} {hora(e.at)}
                </td>
                <td className="px-2 py-1.5">
                  <span className="flex items-center gap-1.5">
                    <span className={`h-1.5 w-1.5 rounded-full ${PUNTO[e.nivel]}`} />
                    {e.nivel}
                  </span>
                </td>
                <td className="px-2 py-1.5 font-semibold">{e.etapa}</td>
                <td className="px-2 py-1.5">
                  {e.file_id ? (
                    <Link href={`/expediente/${encodeURIComponent(e.file_id)}`}
                      className="text-accent underline underline-offset-2">
                      {e.file_id}
                    </Link>
                  ) : "—"}
                </td>
                <td className="px-4 py-1.5 text-muted">{e.mensaje}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
