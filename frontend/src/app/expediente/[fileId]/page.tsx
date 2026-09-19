import Link from "next/link";
import { notFound } from "next/navigation";
import { data } from "@/lib/data";
import { eur, fecha, hora, iban, num, pct } from "@/lib/format";
import { Card } from "@/components/Card";
import { ResultBadge, VeredictoIcono } from "@/components/Badge";

const DINERO = new Set(["importe_factura", "importe_erp", "desvio", "base", "iva", "total", "iva_esperado"]);
const ETIQ: Record<string, string> = {
  nif: "NIF", nif_maestro: "NIF en el maestro", iban_factura: "IBAN de la factura",
  iban_maestro: "IBAN en el maestro", iban: "IBAN", importe_factura: "importe factura",
  importe_erp: "importe pedido (ERP)", desvio: "desvío", iva_pct: "IVA",
  estado: "estado en el ERP", faltan: "faltan", fecha_pago_erp: "fecha de pago en el ERP",
  condiciones_dias: "condiciones (días)", dias_transcurridos: "días transcurridos",
};

function Evidencia({ k, v }: { k: string; v: string | number | null }) {
  let txt = v === null ? "—" : String(v);
  if (typeof v === "number" && DINERO.has(k)) txt = eur(v);
  if (k.includes("iban") && typeof v === "string" && v.length > 8) txt = iban(v);
  if (k === "iva_pct" && typeof v === "number") txt = pct(v);
  return (
    <span className="mr-4 inline-block">
      <span className="text-muted">{ETIQ[k] ?? k.replace(/_/g, " ")}:</span>{" "}
      <span className="font-mono">{txt}</span>
    </span>
  );
}

export default async function ExpedientePage({
  params, searchParams,
}: {
  params: Promise<{ fileId: string }>;
  searchParams: Promise<{ norma?: string }>;
}) {
  const { fileId } = await params;
  const spNorma = (await searchParams).norma;
  const norma = spNorma && data.normas().includes(spNorma) ? spNorma : data.normaActiva();
  const e = await data.expediente(decodeURIComponent(fileId), norma);
  if (!e) notFound();

  const c = e.extraccion?.campos;

  return (
    <div className="space-y-6">
      {/* cabecera */}
      <div className="flex flex-wrap items-center gap-4">
        <Link href={`/facturas?norma=${norma}`} className="text-sm text-muted hover:text-ink">← facturas</Link>
        <h1 className="font-mono text-xl font-bold">{e.documento.file_id}</h1>
        <ResultBadge result={e.decision.result} grande />
        <nav className="ml-auto flex gap-2 font-mono text-sm">
          {e.prev && <Link className="rounded-md border border-line bg-card px-3 py-1 hover:bg-soft" href={`/expediente/${encodeURIComponent(e.prev)}?norma=${norma}`}>← ant</Link>}
          {e.next && <Link className="rounded-md border border-line bg-card px-3 py-1 hover:bg-soft" href={`/expediente/${encodeURIComponent(e.next)}?norma=${norma}`}>sig →</Link>}
        </nav>
      </div>

      {e.decision.motivo && (
        <div className={`rounded-lg border px-4 py-3 text-sm font-medium ${e.decision.result === "NO_PAGAR" ? "border-bad/30 bg-bad/5 text-bad" : "border-warn/30 bg-warn/5 text-warn"}`}>
          {e.decision.motivo}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_360px]">
        <div className="space-y-6">
          {/* reglas */}
          <Card titulo={`El veredicto de cada regla · norma ${e.decision.norma_version}`}>
            <ul className="divide-y divide-line">
              {e.decision.reglas.map((r) => (
                <li key={r.regla} className="flex gap-3 py-3">
                  <VeredictoIcono veredicto={r.veredicto} />
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="font-mono text-sm font-semibold">{r.regla}</span>
                      <span className="text-sm text-muted">{r.descripcion}</span>
                    </div>
                    <div className="mt-1 text-xs">
                      {Object.entries(r.evidencia).map(([k, v]) => <Evidencia key={k} k={k} v={v} />)}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </Card>

          {/* extracción */}
          <Card titulo="Lo que se leyó del PDF">
            {e.extraccion?.via ? (
              <>
                <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted">
                  <span>vía <b className="font-mono text-ink">{e.extraccion.via}</b></span>
                  <span>plantilla <b className="font-mono text-ink">{e.extraccion.plantilla}</b></span>
                  <span>latencia <b className="font-mono text-ink">{e.extraccion.latencia_ms} ms</b></span>
                  <span>coste <b className="font-mono text-ink">{e.extraccion.coste_eur.toFixed(2).replace(".", ",")} €</b></span>
                  <span>aritmética {e.extraccion.cuadra_interna ? <b className="text-ok">cuadra ✓</b> : <b className="text-bad">no cuadra ✗</b>}</span>
                </div>
                <dl className="grid grid-cols-2 gap-x-8 gap-y-1.5 text-sm sm:grid-cols-3">
                  {([
                    ["nº factura", c?.numero],
                    ["fecha", fecha(c?.fecha)],
                    ["NIF emisor", c?.nif_emisor],
                    ["proveedor", c?.proveedor],
                    ["pedido", c?.pedido],
                    ["IBAN", iban(c?.iban)],
                    ["base", eur(c?.base_cent ?? null)],
                    ["IVA", `${eur(c?.iva_cent ?? null)}${c?.iva_pct ? ` (${pct(c.iva_pct)})` : ""}`],
                    ["total", eur(c?.total_cent ?? null)],
                  ] as [string, string | null | undefined][]).map(([k, v]) => (
                    <div key={k}>
                      <dt className="text-xs text-muted">{k}</dt>
                      <dd className="truncate font-mono">{v ?? "—"}</dd>
                    </div>
                  ))}
                </dl>
              </>
            ) : (
              <p className="text-sm text-warn">
                Sin capa de texto: ningún extractor cerró la aritmética. Albertito no inventa
                cifras — este documento espera a un humano (o a la vía de visión).
              </p>
            )}
          </Card>

          {/* notas que aplican */}
          {e.notas.length > 0 && (
            <Card titulo="Notas de Alberto que aplican a este expediente">
              <ul className="space-y-2 text-sm">
                {e.notas.map((n) => (
                  <li key={n.id} className="rounded-md border border-warn/25 bg-warn/5 px-3 py-2">
                    <span className="mr-2 font-mono text-xs text-warn">{n.clave ?? n.ambito}</span>
                    {n.texto}
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>

        {/* columna derecha: custodia, versiones, timeline */}
        <div className="space-y-6">
          <Card titulo="Cadena de custodia">
            <dl className="space-y-1.5 text-sm">
              {([
                ["fichero", e.documento.ruta],
                ["sha256 (doc_id)", e.documento.doc_id.slice(0, 16) + "…"],
                ["tamaño", `${num(e.documento.bytes)} bytes`],
                ["capa de texto", e.documento.tiene_texto ? "sí" : "no (imagen)"],
                ["lote", e.documento.lote],
                ["intentos", String(e.documento.intentos)],
              ] as [string, string][]).map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3">
                  <dt className="text-muted">{k}</dt>
                  <dd className="truncate font-mono text-xs leading-5">{v}</dd>
                </div>
              ))}
            </dl>
          </Card>

          <Card titulo="Con qué versiones se decidió">
            <dl className="space-y-1.5 text-sm">
              <div className="flex justify-between"><dt className="text-muted">norma</dt><dd><Link href={`/manual?version=${e.decision.norma_version}`} className="font-mono text-accent underline underline-offset-2">{e.decision.norma_version}</Link></dd></div>
              <div className="flex justify-between"><dt className="text-muted">snapshot ERP</dt><dd className="font-mono">{e.decision.snapshot_erp}</dd></div>
              <div className="flex justify-between"><dt className="text-muted">maestro</dt><dd className="font-mono">{e.decision.snapshot_maestro}</dd></div>
            </dl>
            {e.otrasDecisiones.map((otra) => {
              const cambia = otra.result !== e.decision.result;
              return (
                <div key={otra.norma_version} className={`mt-3 rounded-md border px-3 py-2 text-xs ${cambia ? "border-warn/30 bg-warn/5" : "border-line bg-soft/50"}`}>
                  {cambia ? (
                    <>Con la norma <b className="font-mono">{otra.norma_version}</b> cambia: <ResultBadge result={otra.result} /> — {otra.motivo}{" "}</>
                  ) : (
                    <>La norma <b className="font-mono">{otra.norma_version}</b> decide lo mismo. </>
                  )}
                  <Link href={`/expediente/${encodeURIComponent(e.documento.file_id)}?norma=${otra.norma_version}`} className="text-accent underline underline-offset-2">ver con {otra.norma_version}</Link>
                </div>
              );
            })}
          </Card>

          {e.asiento && (
            <Card titulo="El pedido en el ERP">
              <dl className="space-y-1.5 text-sm">
                <div className="flex justify-between"><dt className="text-muted">pedido</dt><dd className="font-mono">{e.asiento.pedido}</dd></div>
                <div className="flex justify-between"><dt className="text-muted">importe esperado</dt><dd className="font-mono">{eur(e.asiento.importe_esperado_cent)}</dd></div>
                <div className="flex justify-between"><dt className="text-muted">estado</dt><dd className={`font-mono font-semibold ${e.asiento.estado === "PAGADA" ? "text-bad" : "text-ok"}`}>{e.asiento.estado}</dd></div>
              </dl>
            </Card>
          )}

          <Card titulo="Qué pasó y cuándo">
            <ol className="space-y-2 border-l-2 border-line pl-4 text-sm">
              {e.eventos.map((ev) => (
                <li key={ev.id} className="relative">
                  <span className={`absolute -left-[21px] top-1.5 h-2 w-2 rounded-full ${ev.nivel === "warn" ? "bg-warn" : ev.nivel === "error" ? "bg-bad" : "bg-ok"}`} />
                  <span className="mr-2 font-mono text-xs text-muted">{hora(ev.at)}</span>
                  <span className="mr-2 font-mono text-xs font-semibold">{ev.etapa}</span>
                  <span className="text-muted">{ev.mensaje}</span>
                </li>
              ))}
            </ol>
          </Card>
        </div>
      </div>
    </div>
  );
}
