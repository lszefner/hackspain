"use client";

/**
 * El flujo del sábado a las 18:00, sin terminal:
 *   borrador (copia de la activa) → ensayo en seco sobre las 500 → publicar.
 * El ensayo NO persiste nada: enseña qué cambiaría antes de que exista.
 * Publicar congela la versión, reprocesa y la deja como activa.
 */
import { useState, useTransition } from "react";
import Link from "next/link";
import type { Ensayo } from "@/lib/normas";
import { Card } from "@/components/Card";
import { ResultBadge } from "@/components/Badge";
import { ensayar, publicar } from "../actions";

export function Editor({ inicial, base }: { inicial: string; base: string }) {
  const [yaml, setYaml] = useState(inicial);
  const [ensayo, setEnsayo] = useState<Ensayo | null>(null);
  const [erroresPub, setErroresPub] = useState<string[]>([]);
  const [autor, setAutor] = useState("alberto");
  const [motivo, setMotivo] = useState("");
  const [ensayando, startEnsayo] = useTransition();
  const [publicando, startPublicar] = useTransition();

  // el ensayo caduca si se toca el YAML: no se publica nada sin re-ensayar
  const tocar = (v: string) => { setYaml(v); setEnsayo(null); setErroresPub([]); };
  const listo = ensayo !== null && ensayo.errores.length === 0;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/manual" className="text-sm text-muted hover:text-ink">← el manual</Link>
        <h1 className="mt-1 text-2xl font-bold tracking-tight">Redactar norma nueva</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          Borrador sobre una copia de <b className="font-mono text-ink">{base}</b> (las versiones
          publicadas son inmutables). Primero <b className="text-ink">ensaya</b>: verás qué
          decidiría el motor sobre las 500 facturas <i>antes</i> de publicar nada.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* ── el YAML ── */}
        <div className="space-y-3">
          <textarea
            value={yaml}
            onChange={(e) => tocar(e.target.value)}
            rows={26}
            spellCheck={false}
            className="w-full rounded-xl border border-line bg-card p-4 font-mono text-[13px] leading-6 outline-none focus:border-accent"
          />
          <button
            onClick={() => startEnsayo(async () => setEnsayo(await ensayar(yaml)))}
            disabled={ensayando}
            className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-paper hover:bg-ink/85 disabled:opacity-50"
          >
            {ensayando ? "ensayando sobre las 500…" : "Ensayar sobre las 500"}
          </button>
        </div>

        {/* ── el resultado del ensayo ── */}
        <div className="space-y-4">
          {ensayo === null && (
            <Card>
              <p className="text-sm text-muted">
                Aún sin ensayar. El ensayo valida el YAML (tipos que el motor conoce, tolerancias
                como texto, ids únicos) y calcula el reparto y el diff contra{" "}
                <b className="font-mono text-ink">{base}</b> sin escribir nada en la base de datos.
              </p>
            </Card>
          )}

          {ensayo && ensayo.errores.length > 0 && (
            <Card titulo="El manual no se puede publicar así">
              <ul className="space-y-2 text-sm">
                {ensayo.errores.map((e) => (
                  <li key={e} className="rounded-md border border-bad/25 bg-bad/5 px-3 py-2 text-bad">{e}</li>
                ))}
              </ul>
            </Card>
          )}

          {listo && (
            <>
              <Card titulo={`Ensayo en seco · ${ensayo.version} contra ${ensayo.base} · nada se ha escrito todavía`}>
                <div className="mb-3 flex gap-6 font-mono text-sm">
                  <span className="text-ok">{ensayo.reparto.PAGAR} PAGAR</span>
                  <span className="text-warn">{ensayo.reparto.ESCALAR} ESCALAR</span>
                  <span className="text-bad">{ensayo.reparto.NO_PAGAR} NO PAGAR</span>
                  <span className="ml-auto text-muted">{ensayo.cambios.length} decisiones cambian</span>
                </div>
                {ensayo.cambios.length === 0 ? (
                  <p className="text-sm text-muted">
                    Ninguna decisión cambia respecto a {ensayo.base}. ¿Seguro que la regla nueva hace algo?
                  </p>
                ) : (
                  <div className="max-h-72 overflow-y-auto">
                    <table className="w-full text-sm">
                      <tbody>
                        {ensayo.cambios.map((c) => (
                          <tr key={c.file_id} className="border-t border-line">
                            <td className="py-1.5 pr-2">
                              <Link href={`/expediente/${encodeURIComponent(c.file_id)}`} className="font-mono text-xs text-accent underline underline-offset-2" target="_blank">
                                {c.file_id}
                              </Link>
                            </td>
                            <td className="py-1.5 pr-2"><ResultBadge result={c.de} /></td>
                            <td className="py-1.5 pr-2 text-muted">→</td>
                            <td className="py-1.5 pr-2"><ResultBadge result={c.a} /></td>
                            <td className="py-1.5 text-xs text-muted">{c.motivo}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              <Card titulo="Publicar · la versión se congela, se reprocesa y pasa a ser la activa">
                <div className="flex flex-wrap gap-3">
                  <label className="text-sm">
                    <span className="mb-1 block text-xs text-muted">autor</span>
                    <input value={autor} onChange={(e) => setAutor(e.target.value)}
                      className="w-36 rounded-md border border-line bg-paper px-3 py-1.5 font-mono text-sm outline-none focus:border-accent" />
                  </label>
                  <label className="flex-1 text-sm">
                    <span className="mb-1 block text-xs text-muted">motivo del cambio (lo verá el tribunal)</span>
                    <input value={motivo} onChange={(e) => setMotivo(e.target.value)}
                      placeholder="p. ej. norma v4 del sábado: vencimiento por condiciones de pago"
                      className="w-full rounded-md border border-line bg-paper px-3 py-1.5 text-sm outline-none focus:border-accent" />
                  </label>
                </div>
                {erroresPub.map((e) => (
                  <p key={e} className="mt-2 text-sm text-bad">{e}</p>
                ))}
                <button
                  onClick={() => startPublicar(async () => {
                    const errs = await publicar(yaml, autor, motivo);
                    if (errs) setErroresPub(errs);
                  })}
                  disabled={publicando}
                  className="mt-3 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-paper hover:bg-accent/85 disabled:opacity-50"
                >
                  {publicando ? "publicando y reprocesando…" : `Publicar ${ensayo.version} y reprocesar`}
                </button>
                <p className="mt-2 text-xs text-muted">
                  Las decisiones con {ensayo.base} no se borran: quedan consultables y el diff sale en Finanzas.
                </p>
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
