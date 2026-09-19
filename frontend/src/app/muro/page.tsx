import Link from "next/link";
import { data } from "@/lib/data";
import type { Resultado } from "@/lib/types";
import { eur, num } from "@/lib/format";
import { Card } from "@/components/Card";
import { Muro } from "@/components/Muro";

const ETIQUETA: Record<Resultado, [string, string]> = {
  PAGAR: ["Aprobado", "text-ok"],
  ESCALAR: ["En espera de un humano", "text-warn"],
  NO_PAGAR: ["Bloqueado", "text-bad"],
};

export const metadata = { title: "El muro · tito.ai" };

export default async function MuroPage({
  searchParams,
}: {
  searchParams: Promise<{ norma?: string; result?: string; q?: string }>;
}) {
  const sp = await searchParams;
  const norma = sp.norma && data.normas().includes(sp.norma) ? sp.norma : data.normaActiva();
  const result = (["PAGAR", "NO_PAGAR", "ESCALAR"].includes(sp.result ?? "") ? sp.result : undefined) as Resultado | undefined;
  const q = sp.q || undefined;

  const [kpis, baldosas, motivos] = await Promise.all([
    data.kpis(norma),
    data.baldosas({ norma, result, q }),
    data.motivos(norma),
  ]);

  const urlCon = (r?: Resultado) =>
    `/muro?norma=${norma}${r ? `&result=${r}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`;

  return (
    <div className="space-y-6">
      {/* ── ficha de empleado ── */}
      <Card>
        <div className="flex flex-wrap items-start gap-x-10 gap-y-3">
          <div className="max-w-xl">
            <h1 className="text-2xl font-bold tracking-tight">
              Albertito <span className="font-normal text-muted">· técnico de pagos a proveedores</span>
            </h1>
            <p className="mt-1 text-sm text-muted">
              Decide <b className="text-ink">PAGAR / NO_PAGAR / ESCALAR</b> sobre cada factura,
              con la evidencia al lado. <b className="text-ink">No paga dos veces, no inventa
              cifras y no opina sobre lo que no puede leer</b>: eso lo escala a Alberto.
            </p>
          </div>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
            <dt className="text-muted">Manual de empleado</dt>
            <dd>
              <Link href={`/logica?version=${norma}`} className="font-mono text-accent underline underline-offset-2">
                norma_{norma}.yaml
              </Link>
            </dd>
            <dt className="text-muted">Escala a</dt>
            <dd>Alberto (bandeja de escalados)</dd>
            <dt className="text-muted">Última pasada</dt>
            <dd className="font-mono">{num(kpis.nDocs)} docs · {kpis.duracionPasada_s.toString().replace(".", ",")} s · {kpis.costeTotal_eur.toFixed(2).replace(".", ",")} €</dd>
          </dl>
        </div>
      </Card>

      {/* ── KPIs ── */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {kpis.porResultado.map((k) => {
          const [etiqueta, color] = ETIQUETA[k.result];
          const activo = result === k.result;
          return (
            <Link key={k.result} href={urlCon(activo ? undefined : k.result)}>
              <Card className={`transition-shadow hover:shadow-md ${activo ? "ring-2 ring-ink/60" : ""}`}>
                <div className="flex items-baseline justify-between">
                  <span className={`font-mono text-3xl font-bold ${color}`}>{k.n}</span>
                  <span className="font-mono text-xs text-muted">{k.result.replace("_", " ")}</span>
                </div>
                <div className="mt-1 text-sm text-muted">{etiqueta}</div>
                <div className="mt-2 font-mono text-lg">
                  {k.total_cent !== null ? eur(k.total_cent) : "importe desconocido"}
                  {k.sin_importe > 0 && k.total_cent !== null && (
                    <span className="ml-1 text-xs text-muted">+ {k.sin_importe} sin importe</span>
                  )}
                </div>
              </Card>
            </Link>
          );
        })}
      </div>

      {/* ── filtros ── */}
      <div className="flex flex-wrap items-center gap-3">
        <form className="flex items-center gap-2" action="/muro">
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
              href={`/muro?norma=${n}${result ? `&result=${result}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`}
              className={`rounded-md border px-3 py-1 font-mono ${
                n === norma ? "border-ink bg-ink text-paper" : "border-line bg-card text-muted hover:text-ink"
              }`}
              title={n === data.normaActiva() ? "versión activa (la última publicada)" : undefined}
            >
              {n}
              {n === data.normaActiva() && <span className="ml-1 text-[10px]">●</span>}
            </Link>
          ))}
        </div>
      </div>

      {/* ── el muro ── */}
      <Card titulo={`${baldosas.length} de ${kpis.nDocs} documentos · clic para seguir su decisión de punta a punta`}>
        <Muro baldosas={baldosas} norma={norma} />
      </Card>

      {/* ── motivos ── */}
      <Card titulo="Por qué no se paga todo · motivos medidos sobre la base de datos">
        <ul className="divide-y divide-line text-sm">
          {motivos.map((m) => (
            <li key={m.motivo} className="flex items-center gap-3 py-2">
              <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${m.result === "NO_PAGAR" ? "bg-bad" : "bg-warn"}`} />
              <span>{m.motivo}</span>
              <span className="ml-auto font-mono text-muted">{m.n}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
