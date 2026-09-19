import Link from "next/link";
import { getNorma, listaNormas, normaActiva, POLITICA_FALLA, TIPOS_MOTOR } from "@/lib/normas";
import { fecha, hora } from "@/lib/format";
import { Card } from "@/components/Card";
import { ResultBadge } from "@/components/Badge";
import { BotonActivar } from "./ui";

export const metadata = { title: "Lógica · Albertito" };
export const dynamic = "force-dynamic";

export default async function ManualPage({
  searchParams,
}: {
  searchParams: Promise<{ version?: string; publicada?: string }>;
}) {
  const sp = await searchParams;
  const activa = normaActiva();
  const version = sp.version && getNorma(sp.version) ? sp.version : activa;
  const norma = getNorma(version)!;
  const esActiva = version === activa;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-4">
        <div className="max-w-2xl">
          <h1 className="text-2xl font-bold tracking-tight">El manual de empleado</h1>
          <p className="mt-1 text-sm text-muted">
            Las reglas de Albertito son datos, no código: se suben como YAML, se leen como manual.
            Cada versión publicada es <b className="text-ink">inmutable</b> — cambiar la norma es
            publicar una nueva y reprocesar; las decisiones antiguas se conservan.
          </p>
        </div>
        <Link
          href="/logica/editor"
          className="ml-auto rounded-md bg-ink px-4 py-2 text-sm font-semibold text-paper hover:bg-ink/85"
        >
          Redactar norma nueva
        </Link>
      </div>

      {sp.publicada && (
        <div className="rounded-lg border border-ok/30 bg-ok/5 px-4 py-3 text-sm text-ok">
          Norma <b className="font-mono">{version}</b> publicada y reprocesada. Ahora es la versión
          activa; las decisiones anteriores siguen ahí —{" "}
          <Link href="/finanzas" className="underline underline-offset-2">el diff está en Finanzas</Link>.
        </div>
      )}

      {/* ── versiones ── */}
      <div className="flex flex-wrap gap-2">
        {listaNormas().map((n) => (
          <Link
            key={n.version}
            href={`/logica?version=${n.version}`}
            className={`rounded-lg border px-4 py-2 text-sm transition-colors ${
              n.version === version ? "border-ink bg-ink text-paper" : "border-line bg-card hover:bg-soft"
            }`}
          >
            <span className="font-mono font-bold">{n.version}</span>
            {n.version === activa && (
              <span className={`ml-2 rounded-full px-2 py-0.5 font-mono text-[10px] font-bold ${n.version === version ? "bg-paper/20 text-paper" : "bg-ok/10 text-ok"}`}>
                ACTIVA
              </span>
            )}
            <span className={`ml-2 text-xs ${n.version === version ? "text-paper/70" : "text-muted"}`}>
              {fecha(n.creado_at)} · {n.autor}
            </span>
          </Link>
        ))}
      </div>

      {/* ── cabecera de la versión ── */}
      <Card>
        <div className="flex flex-wrap items-center gap-x-8 gap-y-2 text-sm">
          <div>
            <span className="font-mono text-lg font-bold">norma {norma.version}</span>
            {esActiva ? (
              <span className="ml-3 rounded-full bg-ok/10 px-2.5 py-0.5 font-mono text-xs font-bold text-ok">ACTIVA — la que usa el muro</span>
            ) : (
              <span className="ml-3"><BotonActivar version={version} /></span>
            )}
          </div>
          <div className="text-muted">
            publicada el {fecha(norma.creado_at)} a las {hora(norma.creado_at)}
          </div>
          <div className="text-muted">por <b className="text-ink">{norma.autor}</b></div>
          <div className="text-muted">motivo: <span className="text-ink">{norma.motivo}</span></div>
          <Link
            href={`/logica/editor?desde=${version}`}
            className="ml-auto rounded-md border border-line bg-card px-3 py-1.5 text-sm font-semibold hover:bg-soft"
            title="Abre esta norma en el editor. Los cambios se publican como versión nueva: las publicadas son inmutables."
          >
            Editar esta norma
          </Link>
        </div>
      </Card>

      {/* ── las reglas, legibles ── */}
      <ol className="space-y-3">
        {norma.reglas.map((r, i) => {
          const falla = POLITICA_FALLA[r.tipo] ?? "ESCALAR";
          return (
            <li key={r.id}>
              <Card>
                <div className="flex flex-wrap items-start gap-4">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-soft font-mono text-sm font-bold text-muted">
                    {i + 1}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span className="font-mono text-sm font-bold">{r.id}</span>
                      <span className="rounded bg-soft px-1.5 py-0.5 font-mono text-[11px] text-muted" title={TIPOS_MOTOR[r.tipo]}>
                        {r.tipo}
                      </span>
                    </div>
                    <p className="mt-1 text-[15px] leading-snug">{r.descripcion}.</p>
                    <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs text-muted">
                      {r.requiere.length > 0 && (
                        <span>
                          necesita{" "}
                          {r.requiere.map((c) => (
                            <code key={c} className="mr-1 rounded bg-soft px-1.5 py-0.5 font-mono text-ink">{c}</code>
                          ))}
                        </span>
                      )}
                      {Object.entries(r.params).map(([k, v]) => (
                        <span key={k}>
                          {k.replace(/_/g, " ")} <code className="rounded bg-soft px-1.5 py-0.5 font-mono text-ink">{String(v)}</code>
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="shrink-0 text-right text-xs text-muted">
                    <div className="mb-1">si falla</div>
                    <ResultBadge result={falla} />
                    <div className="mt-1.5">si faltan datos</div>
                    <ResultBadge result="ESCALAR" />
                  </div>
                </div>
              </Card>
            </li>
          );
        })}
      </ol>

      <p className="text-xs text-muted">
        La consecuencia de cada veredicto la fija <code className="rounded bg-soft px-1 font-mono">politica.yaml</code>,
        no la regla: fallar en identidad (NIF/IBAN) o en estado del ERP bloquea el pago; todo lo demás
        —y cualquier dato que falte— va a un humano. Por defecto, se paga.
      </p>

      {/* ── el YAML, para quien lo quiera ── */}
      <details className="group">
        <summary className="cursor-pointer text-sm font-medium text-accent underline underline-offset-2">
          Ver el YAML tal y como se subió
        </summary>
        <Card className="mt-3">
          <pre className="overflow-x-auto whitespace-pre font-mono text-[13px] leading-6">{norma.yaml}</pre>
        </Card>
      </details>
    </div>
  );
}
