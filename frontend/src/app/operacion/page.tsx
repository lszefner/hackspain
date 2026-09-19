import Link from "next/link";
import { data } from "@/lib/data";
import { eur, hora, num } from "@/lib/format";
import { Card } from "@/components/Card";
import { ResultBadge } from "@/components/Badge";

export const metadata = { title: "Operación · Albertito" };
export const dynamic = "force-dynamic";

const coma = (n: number, dec = 2) => n.toFixed(dec).replace(".", ",");

export default async function OperacionPage() {
  const versiones = data.normas();
  const [normaA, normaB] = versiones.slice(-2); // las dos últimas publicadas
  const [coste, diff, partes, salud] = await Promise.all([
    data.coste(),
    data.diffNormas(normaA, normaB),
    data.partes(),
    data.salud(),
  ]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Operación</h1>
        <p className="mt-1 text-sm text-muted">
          Coste medido (no estimado), capacidad, salud del sistema y qué cambia cuando cambia la norma.
        </p>
      </div>

      {/* salud */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        {([
          ["ERP", salud.erp.ok ? "conectado" : "caído", salud.erp.ok, `${salud.erp.snapshot} · ${salud.erp.asientos} asientos`],
          ["Proveedor LLM", salud.llm.ok ? "operativo" : "caído", salud.llm.ok, `modo ${salud.llm.modo}`],
          ["Pendientes", String(salud.pendientes), salud.pendientes === 0, "documentos sin decidir"],
          ["Reintentos", String(salud.reintentos), true, "recuperados sin duplicar"],
          ["Errores 24 h", String(salud.errores24h), salud.errores24h === 0, "en el registro de eventos"],
        ] as [string, string, boolean, string][]).map(([t, v, ok, sub]) => (
          <Card key={t}>
            <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">{t}</div>
            <div className={`mt-1 flex items-center gap-2 font-mono text-lg font-bold ${ok ? "text-ok" : "text-bad"}`}>
              <span className={`h-2.5 w-2.5 rounded-full ${ok ? "bg-ok" : "bg-bad"}`} />
              {v}
            </div>
            <div className="mt-0.5 text-xs text-muted">{sub}</div>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* coste por ruta */}
        <Card titulo="Coste y latencia por ruta · medido sobre la base de datos">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted">
                <th className="pb-2">vía</th><th className="pb-2 text-right">docs</th>
                <th className="pb-2 text-right">€/doc</th><th className="pb-2 text-right">p50</th>
                <th className="pb-2 text-right">p95</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {coste.rutas.map((r) => (
                <tr key={r.via} className="border-t border-line">
                  <td className="py-2">{r.via}</td>
                  <td className="py-2 text-right">{r.docs}</td>
                  <td className="py-2 text-right">{coma(r.coste_por_doc_eur, 2)}</td>
                  <td className="py-2 text-right">{num(Math.round(r.latencia_p50_ms))} ms</td>
                  <td className="py-2 text-right">{num(Math.round(r.latencia_p95_ms))} ms</td>
                </tr>
              ))}
              <tr className="border-t-2 border-ink/20 font-semibold">
                <td className="py-2">total</td>
                <td className="py-2 text-right">500</td>
                <td className="py-2 text-right">{coma(coste.coste_por_doc_eur, 4)}</td>
                <td className="py-2 text-right" colSpan={2}>{coma(coste.docs_por_segundo, 0)} docs/s</td>
              </tr>
            </tbody>
          </table>
        </Card>

        {/* proyección */}
        <Card titulo="Proyección de coste mensual">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted">
                <th className="pb-2">facturas/mes</th><th className="pb-2 text-right">coste</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {coste.proyeccion.map((p) => (
                <tr key={p.docs_mes} className="border-t border-line">
                  <td className="py-2">{num(p.docs_mes)}</td>
                  <td className="py-2 text-right">{eur(Math.round(p.coste_eur * 100))}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-muted">{coste.punto_cruce_ocr}</p>
        </Card>
      </div>

      {/* diff de normas */}
      <Card titulo={`Qué cambia al pasar de ${normaA} a ${normaB} · ${diff.length} decisiones — el resto se conserva, nada se borra`}>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-muted">
              <th className="pb-2">factura</th><th className="pb-2">{normaA}</th><th className="pb-2">{normaB}</th><th className="pb-2">por qué</th>
            </tr>
          </thead>
          <tbody>
            {diff.map((c) => (
              <tr key={c.file_id} className="border-t border-line">
                <td className="py-2">
                  <Link href={`/expediente/${encodeURIComponent(c.file_id)}?norma=${normaB}`} className="font-mono text-accent underline underline-offset-2">
                    {c.file_id}
                  </Link>
                </td>
                <td className="py-2"><ResultBadge result={c.de} /></td>
                <td className="py-2"><ResultBadge result={c.a} /></td>
                <td className="py-2 text-muted">{c.motivo}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-xs text-muted">
          La clave primaria de una decisión es (doc_id, norma, snapshot ERP, snapshot maestro):
          reprocesar añade, nunca sobreescribe. Esta tabla es una consulta, no una migración.
        </p>
      </Card>

      {/* parte de trabajo */}
      <Card titulo="Parte de trabajo · una fila por pasada">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-muted">
              <th className="pb-2">pasada</th><th className="pb-2">cuándo</th><th className="pb-2 text-right">docs</th>
              <th className="pb-2 text-right">duración</th><th className="pb-2 text-right">coste</th>
              <th className="pb-2 text-right">pagar</th><th className="pb-2 text-right">escalar</th>
              <th className="pb-2 text-right">no pagar</th><th className="pb-2">norma</th>
            </tr>
          </thead>
          <tbody className="font-mono">
            {partes.map((p) => (
              <tr key={p.pasada} className="border-t border-line">
                <td className="py-2">{p.pasada}</td>
                <td className="py-2 text-muted">{p.inicio.slice(0, 10)} {hora(p.inicio)}</td>
                <td className="py-2 text-right">{p.docs}</td>
                <td className="py-2 text-right">{coma(p.duracion_s, 1)} s</td>
                <td className="py-2 text-right">{coma(p.coste_eur)} €</td>
                <td className="py-2 text-right text-ok">{p.pagar}</td>
                <td className="py-2 text-right text-warn">{p.escalar}</td>
                <td className="py-2 text-right text-bad">{p.no_pagar}</td>
                <td className="py-2">{p.norma}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
