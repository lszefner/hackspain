"use client";

/**
 * El escritorio de Alberto: los escalados que esperan a un humano.
 * Barandillas (no negociables, ver TODO.md T27):
 *  - las cifras del email van por plantilla, el LLM solo escribiría la prosa
 *  - nunca se envía solo: aprobar / editar / descartar, con autor registrado
 *  - resolver aquí escribe en `resoluciones`; JAMÁS toca outcomes.jsonl
 * Mock: el estado vive en memoria; el botón de enviar está sustituido.
 */
import { useMemo, useState } from "react";
import Link from "next/link";
import type { Escalado } from "@/lib/types";
import { eur } from "@/lib/format";
import { Card } from "@/components/Card";

type Cat = Escalado["categoria"];
const CATS: { id: Cat; label: string }[] = [
  { id: "importe", label: "Importe no cuadra" },
  { id: "campo_faltante", label: "Campo faltante" },
  { id: "revisar", label: "Marcadas revisar" },
  { id: "ilegible", label: "Ilegibles (imagen)" },
];

function borradorEmail(e: Escalado): string {
  // Los datos vienen inyectados de `decisiones` y `extracciones`, nunca generados.
  const prov = e.proveedor?.razon_social ?? "proveedor";
  if (e.categoria === "importe")
    return `Estimados señores de ${prov}:

Hemos recibido su factura ${e.file_id.replace(".pdf", "")} por ${eur(e.total_cent)}.
Nuestro pedido ${e.pedido} consta por ${eur(e.importe_erp_cent)}, una diferencia
de ${eur((e.total_cent ?? 0) - (e.importe_erp_cent ?? 0))}.

¿Pueden confirmar el importe correcto o remitir factura rectificativa?

Un saludo,
Alberto — Administración`;
  return `Estimados señores de ${prov}:

Su factura ${e.file_id.replace(".pdf", "")} ha llegado incompleta
(${e.motivo}). ¿Pueden reenviarla con todos los datos?

Un saludo,
Alberto — Administración`;
}

type Estado = { resuelto?: "PAGAR" | "NO_PAGAR"; esperandoProveedor?: boolean };

export function Bandeja({ escalados }: { escalados: Escalado[] }) {
  const [cat, setCat] = useState<Cat>("importe");
  const [sel, setSel] = useState<string | null>(null);
  const [estado, setEstado] = useState<Record<string, Estado>>({});
  const [borrador, setBorrador] = useState<string | null>(null);

  const porCat = useMemo(() => {
    const m = new Map<Cat, Escalado[]>();
    for (const e of escalados) m.set(e.categoria, [...(m.get(e.categoria) ?? []), e]);
    return m;
  }, [escalados]);

  const lista = porCat.get(cat) ?? [];
  const activo = lista.find((e) => e.file_id === sel) ?? lista[0] ?? null;
  const est = activo ? estado[activo.file_id] ?? {} : {};

  const marcar = (fid: string, e: Estado) => {
    setEstado((s) => ({ ...s, [fid]: { ...s[fid], ...e } }));
    setBorrador(null);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Bandeja de escalados</h1>
        <p className="mt-1 text-sm text-muted">
          Lo que Albertito no se atrevió a decidir solo. Resolver aquí queda en{" "}
          <code className="rounded bg-soft px-1 font-mono text-xs">resoluciones</code> con autor y
          motivo — <b className="text-ink">nunca toca outcomes.jsonl</b>.
        </p>
      </div>

      {/* pestañas por motivo */}
      <div className="flex flex-wrap gap-2">
        {CATS.map((c) => (
          <button
            key={c.id}
            onClick={() => { setCat(c.id); setSel(null); setBorrador(null); }}
            className={`rounded-full border px-4 py-1.5 text-sm font-medium transition-colors ${
              cat === c.id ? "border-ink bg-ink text-paper" : "border-line bg-card text-muted hover:text-ink"
            }`}
          >
            {c.label} <span className="font-mono">({porCat.get(c.id)?.length ?? 0})</span>
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[380px_1fr]">
        {/* lista */}
        <Card className="max-h-[560px] overflow-y-auto !p-2">
          <ul className="divide-y divide-line">
            {lista.map((e) => {
              const st = estado[e.file_id] ?? {};
              return (
                <li key={e.file_id}>
                  <button
                    onClick={() => { setSel(e.file_id); setBorrador(null); }}
                    className={`w-full rounded-md px-3 py-2.5 text-left transition-colors ${activo?.file_id === e.file_id ? "bg-soft" : "hover:bg-soft/60"}`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="truncate font-mono text-sm">{e.file_id}</span>
                      {st.resuelto && <span className={`ml-auto shrink-0 font-mono text-[10px] font-bold ${st.resuelto === "PAGAR" ? "text-ok" : "text-bad"}`}>{st.resuelto.replace("_", " ")}</span>}
                      {st.esperandoProveedor && <span className="ml-auto shrink-0 font-mono text-[10px] font-bold text-accent">esperando proveedor</span>}
                    </div>
                    <div className="mt-0.5 truncate text-xs text-muted">{e.motivo}</div>
                  </button>
                </li>
              );
            })}
          </ul>
        </Card>

        {/* detalle */}
        {activo ? (
          <div className="space-y-4">
            <Card>
              <div className="flex flex-wrap items-baseline gap-3">
                <Link href={`/expediente/${encodeURIComponent(activo.file_id)}`} className="font-mono text-lg font-bold text-accent underline underline-offset-2">
                  {activo.file_id}
                </Link>
                <span className="text-sm text-warn">{activo.motivo}</span>
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-x-8 gap-y-1.5 text-sm sm:grid-cols-4">
                <div><dt className="text-xs text-muted">proveedor</dt><dd className="truncate">{activo.proveedor?.razon_social ?? "desconocido"}</dd></div>
                <div><dt className="text-xs text-muted">pedido</dt><dd className="font-mono">{activo.pedido ?? "—"}</dd></div>
                <div><dt className="text-xs text-muted">factura</dt><dd className="font-mono">{eur(activo.total_cent)}</dd></div>
                <div><dt className="text-xs text-muted">pedido (ERP)</dt><dd className="font-mono">{eur(activo.importe_erp_cent)}</dd></div>
              </dl>
            </Card>

            <Card titulo="Resolver como Alberto · queda registrado con autor y motivo">
              <div className="flex flex-wrap gap-2">
                <button onClick={() => marcar(activo.file_id, { resuelto: "PAGAR", esperandoProveedor: false })}
                  className="rounded-md border border-ok/40 bg-ok/10 px-4 py-1.5 text-sm font-semibold text-ok hover:bg-ok/20">
                  Aprobar el pago
                </button>
                <button onClick={() => marcar(activo.file_id, { resuelto: "NO_PAGAR", esperandoProveedor: false })}
                  className="rounded-md border border-bad/40 bg-bad/10 px-4 py-1.5 text-sm font-semibold text-bad hover:bg-bad/20">
                  Rechazar
                </button>
                {activo.email_posible && (
                  <button onClick={() => setBorrador(borradorEmail(activo))}
                    className="rounded-md border border-accent/40 bg-accent/10 px-4 py-1.5 text-sm font-semibold text-accent hover:bg-accent/20">
                    ¿Escribimos al proveedor?
                  </button>
                )}
                {activo.categoria === "ilegible" && (
                  <span className="self-center text-xs text-muted">
                    Sin NIF no hay destinatario: primero la vía de visión (T02).
                  </span>
                )}
              </div>
              {est.resuelto && (
                <p className="mt-3 text-xs text-muted">
                  Resuelto como <b className="font-mono">{est.resuelto}</b> por{" "}
                  <b>alberto</b> — escrito en <code className="rounded bg-soft px-1 font-mono">resoluciones</code>.
                  El outcome del JSONL sigue siendo <b className="font-mono">ESCALAR</b>.
                </p>
              )}
            </Card>

            {borrador !== null && (
              <Card titulo="Borrador · las cifras vienen de la base de datos, no del modelo">
                <textarea
                  value={borrador}
                  onChange={(ev) => setBorrador(ev.target.value)}
                  rows={12}
                  className="w-full rounded-md border border-line bg-paper p-3 font-mono text-sm outline-none focus:border-accent"
                />
                <div className="mt-3 flex items-center gap-2">
                  <button
                    onClick={() => marcar(activo.file_id, { esperandoProveedor: true })}
                    className="rounded-md bg-accent px-4 py-1.5 text-sm font-semibold text-paper hover:bg-accent/85"
                  >
                    Aprobar y enviar
                  </button>
                  <button onClick={() => setBorrador(null)} className="rounded-md border border-line px-4 py-1.5 text-sm text-muted hover:text-ink">
                    Descartar
                  </button>
                  <span className="ml-auto text-xs text-warn">
                    demo: el destinatario está sustituido por el buzón del equipo
                  </span>
                </div>
              </Card>
            )}

            {est.esperandoProveedor && (
              <div className="rounded-lg border border-accent/30 bg-accent/5 px-4 py-3 text-sm text-accent">
                Email aprobado por <b>alberto</b> y en camino. La factura pasa a{" "}
                <b>esperando proveedor</b> — su resultado en outcomes.jsonl sigue siendo ESCALAR.
              </div>
            )}
          </div>
        ) : (
          <Card><p className="text-sm text-muted">Nada en esta categoría.</p></Card>
        )}
      </div>
    </div>
  );
}
