"use client";

/**
 * Subir una factura. La identidad es el sha256 del contenido, no el nombre.
 *
 * El hash se calcula AQUÍ, en el navegador, y se pregunta antes de enviar un
 * solo byte: un duplicado no llega a viajar. El servidor lo vuelve a
 * comprobar de todas formas — esto es comodidad, no la barandilla.
 */
import { useState, useTransition } from "react";
import Link from "next/link";
import type {
  EstadoDocumento, ResultadoReproceso, ResultadoSubida,
} from "@/lib/types";
import { fecha, hora } from "@/lib/format";
import { Card } from "@/components/Card";
import { ResultBadge } from "@/components/Badge";
import { comprobar, reprocesar, subir } from "./actions";

async function sha256Hex(f: File): Promise<string | null> {
  // crypto.subtle no existe fuera de un contexto seguro (http en una IP).
  // Sin él se sube directo: el servidor deduplica igual.
  if (!globalThis.crypto?.subtle) return null;
  const buf = await crypto.subtle.digest("SHA-256", await f.arrayBuffer());
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function Errores({ errores }: { errores: string[] }) {
  return (
    <Card titulo="No se ha subido">
      <ul className="space-y-2 text-sm">
        {errores.map((e) => (
          <li key={e} className="rounded-md border border-bad/25 bg-bad/5 px-3 py-2 text-bad">{e}</li>
        ))}
      </ul>
    </Card>
  );
}

function Historial({ decisiones }: { decisiones: EstadoDocumento["historial"] }) {
  if (decisiones.length === 0) return null;
  return (
    <details className="mt-4">
      <summary className="cursor-pointer text-xs text-muted hover:text-ink">
        {decisiones.length === 1 ? "1 decisión registrada" : `${decisiones.length} decisiones registradas`}
      </summary>
      <table className="mt-2 w-full text-xs">
        <thead>
          <tr className="text-left uppercase tracking-wide text-muted">
            <th className="py-1.5">cuándo</th>
            <th className="py-1.5">norma</th>
            <th className="py-1.5">decisión</th>
            <th className="py-1.5">motivo</th>
          </tr>
        </thead>
        <tbody>
          {decisiones.map((d) => (
            <tr key={`${d.norma_version}-${d.snapshot_erp}-${d.creado_at}`} className="border-t border-line">
              <td className="py-1.5 font-mono">{fecha(d.creado_at)} {hora(d.creado_at)}</td>
              <td className="py-1.5 font-mono">{d.norma_version}</td>
              <td className="py-1.5"><ResultBadge result={d.result} /></td>
              <td className="py-1.5 text-muted">{d.motivo ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

/** La tarjeta que da la cara: este fichero ya estaba, y esto es lo que pasó. */
function Duplicado({
  estado, nombreSubido, onReprocesar, reprocesando, reproceso,
}: {
  estado: EstadoDocumento;
  nombreSubido?: string;
  onReprocesar: () => void;
  reprocesando: boolean;
  reproceso: ResultadoReproceso | null;
}) {
  const doc = estado.documento;
  const otroNombre = nombreSubido && nombreSubido !== doc.file_id;

  return (
    <Card titulo="Este fichero ya está en la plataforma">
      <p className="text-sm">
        Se subió como <b className="font-mono text-ink">{doc.file_id}</b> (lote{" "}
        <span className="font-mono">{doc.lote}</span>, el {fecha(doc.creado_at)}) y ya fue
        procesado. Es el <b>mismo contenido</b>, byte a byte
        {otroNombre && <> — lo has elegido como <span className="font-mono">{nombreSubido}</span></>}.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        {estado.ultimo_result ? (
          <ResultBadge result={estado.ultimo_result} grande />
        ) : (
          <span className="text-sm text-warn">registrado, pero todavía sin decidir</span>
        )}
        {estado.decision?.motivo && (
          <span className="text-sm text-muted">{estado.decision.motivo}</span>
        )}
      </div>

      {estado.resolucion && (
        <p className="mt-3 rounded-md border border-line bg-soft px-3 py-2 text-sm">
          Resuelta a mano como <b className="font-mono">{estado.resolucion.result}</b> por{" "}
          <b>{estado.resolucion.resuelto_por ?? "alguien"}</b>: {estado.resolucion.motivo}
        </p>
      )}

      {estado.aviso && <p className="mt-3 text-xs text-warn">{estado.aviso}</p>}
      <Historial decisiones={estado.historial} />

      {/* Las reglas cambiaron: el motor puede dar otro resultado, así que
          reprocesar vale la pena aunque la factura ya estuviera decidida. */}
      {estado.norma_cambiada && (
        <p className="mt-3 rounded-md border border-warn/25 bg-warn/5 px-3 py-2 text-sm text-warn">
          Las reglas han cambiado desde que se decidió:{" "}
          <span className="font-mono">{estado.norma_decision}</span> →{" "}
          <span className="font-mono">{estado.norma_actual}</span>. Volver a procesarla
          puede dar otro resultado.
        </p>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-line pt-4">
        {estado.reprocesable ? (
          <>
            <button
              onClick={onReprocesar}
              disabled={reprocesando}
              className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-paper hover:bg-ink/85 disabled:opacity-50"
            >
              {reprocesando ? "reprocesando…" : "Volver a procesarla"}
            </button>
            <span className="text-xs text-muted">
              {estado.ultimo_result === "PAGAR"
                ? "ya se pagó: reprocesar no deshace el pago, recalcula qué diría el motor hoy"
                : "se vuelve a decidir con la norma activa y los últimos datos del ERP; no se vuelve a leer el PDF"}
            </span>
          </>
        ) : (
          <p className="text-sm text-muted">
            No se puede volver a procesar: <b className="text-ink">{estado.motivo_bloqueo}</b>.
          </p>
        )}
        {estado.decision && (
          <Link
            href={`/expediente/${encodeURIComponent(doc.file_id)}`}
            className="ml-auto text-sm text-accent underline underline-offset-2"
          >
            ver su expediente →
          </Link>
        )}
      </div>

      {doc.tiene_texto === false && estado.reprocesable && (
        <p className="mt-3 text-xs text-warn">
          Este PDF no tiene capa de texto: reprocesar no lo leerá mejor, hace falta la vía de
          visión (<code className="rounded bg-soft px-1 font-mono">alberto vision</code>).
        </p>
      )}

      {reproceso && (
        <div className="mt-4 rounded-lg border border-line bg-soft px-4 py-3 text-sm">
          {reproceso.ok ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                {reproceso.anterior && <ResultBadge result={reproceso.anterior.result} />}
                <span className="text-muted">→</span>
                {reproceso.nueva && <ResultBadge result={reproceso.nueva.result} />}
                <span className="text-muted">{reproceso.nueva?.motivo}</span>
              </div>
              {reproceso.misma_clave && (
                <p className="mt-2 text-xs text-muted">
                  Misma norma y mismos snapshots que la decisión anterior: se ha vuelto a
                  evaluar, pero no había nada que pudiera cambiarla.
                </p>
              )}
            </>
          ) : (
            <p className="text-bad">{reproceso.motivo ?? reproceso.errores?.join(", ")}</p>
          )}
        </div>
      )}
    </Card>
  );
}

function Subida({ r }: { r: ResultadoSubida }) {
  return (
    <Card titulo="Subida y procesada">
      <p className="text-sm">
        Guardada como <b className="font-mono text-ink">{r.file_id}</b>.
      </p>
      {r.renombrado_de && (
        <p className="mt-2 text-sm text-warn">
          Ya había un <span className="font-mono">{r.renombrado_de}</span> con otro contenido, así
          que esta se ha guardado con otro nombre. Son dos facturas distintas.
        </p>
      )}
      <div className="mt-4 flex flex-wrap items-center gap-3">
        {r.decision ? (
          <>
            <ResultBadge result={r.decision.result} grande />
            <span className="text-sm text-muted">{r.decision.motivo}</span>
          </>
        ) : (
          <span className="text-sm text-warn">{r.sin_decidir}</span>
        )}
        {r.decision && (
          <Link
            href={`/expediente/${encodeURIComponent(r.file_id!)}`}
            className="ml-auto text-sm text-accent underline underline-offset-2"
          >
            ver su expediente →
          </Link>
        )}
      </div>
      {r.tiene_texto === false && (
        <p className="mt-3 text-xs text-warn">
          Sin capa de texto: se ha escalado porque no se pudieron leer los campos.
        </p>
      )}
      {r.aviso && <p className="mt-3 text-xs text-warn">{r.aviso}</p>}
    </Card>
  );
}

export function SubirFactura({ hayBackend }: { hayBackend: boolean }) {
  const [fichero, setFichero] = useState<File | null>(null);
  const [duplicado, setDuplicado] = useState<EstadoDocumento | null>(null);
  const [nombreSubido, setNombreSubido] = useState<string | undefined>();
  const [hecho, setHecho] = useState<ResultadoSubida | null>(null);
  const [errores, setErrores] = useState<string[]>([]);
  const [reproceso, setReproceso] = useState<ResultadoReproceso | null>(null);
  const [trabajando, startTrabajo] = useTransition();
  const [reprocesando, startReproceso] = useTransition();

  const limpiar = () => {
    setDuplicado(null); setHecho(null); setErrores([]); setReproceso(null);
    setNombreSubido(undefined);
  };

  const elegir = (f: File | null) => {
    limpiar();
    setFichero(f);
    if (!f) return;
    startTrabajo(async () => {
      const sha = await sha256Hex(f);
      if (!sha) return;
      const ya = await comprobar(sha);
      if (ya) { setDuplicado(ya); setNombreSubido(f.name); }
    });
  };

  const enviar = () => {
    if (!fichero) return;
    startTrabajo(async () => {
      const form = new FormData();
      form.set("fichero", fichero);
      const r = await subir(form);
      if (r.ok) { setHecho(r); setDuplicado(null); }
      else if (r.duplicado) { setDuplicado(r.duplicado); setNombreSubido(r.nombre_subido); }
      else setErrores(r.errores ?? ["no se ha podido subir"]);
    });
  };

  const volverAProcesar = () => {
    const doc_id = duplicado?.documento.doc_id;
    if (!doc_id) return;
    startReproceso(async () => {
      const r = await reprocesar(doc_id);
      setReproceso(r);
      if (r.ok) {
        const fresco = await comprobar(doc_id);
        if (fresco) setDuplicado(fresco);
      }
    });
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Subir una factura</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          La identidad de una factura es el <b className="text-ink">sha256 de su contenido</b>, no
          su nombre. Si ya está en la plataforma no se vuelve a subir ni a cobrar el procesado:
          se enseña qué se decidió y por qué.
        </p>
      </div>

      {!hayBackend && (
        <Card titulo="Solo lectura">
          <p className="text-sm text-muted">
            Esta pantalla necesita <code className="rounded bg-soft px-1 font-mono">alberto web</code>{" "}
            levantado (<span className="font-mono">NEXT_PUBLIC_API_BASE</span>). Sin él, la web
            enseña datos pero no acepta ficheros.
          </p>
        </Card>
      )}

      <Card>
        <div className="flex flex-wrap items-center gap-4">
          <input
            type="file"
            accept="application/pdf,.pdf"
            disabled={!hayBackend || trabajando}
            onChange={(e) => elegir(e.target.files?.[0] ?? null)}
            className="text-sm file:mr-3 file:rounded-md file:border-0 file:bg-soft file:px-4 file:py-2 file:text-sm file:font-semibold file:text-ink hover:file:bg-line disabled:opacity-50"
          />
          <button
            onClick={enviar}
            disabled={!hayBackend || !fichero || trabajando || duplicado !== null}
            className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-paper hover:bg-ink/85 disabled:opacity-40"
          >
            {trabajando ? "procesando…" : "Subir y procesar"}
          </button>
          {fichero && (
            <span className="text-xs text-muted">
              {fichero.name} · {Math.round(fichero.size / 1024)} kB
            </span>
          )}
        </div>
      </Card>

      {errores.length > 0 && <Errores errores={errores} />}
      {duplicado && (
        <Duplicado
          estado={duplicado}
          nombreSubido={nombreSubido}
          onReprocesar={volverAProcesar}
          reprocesando={reprocesando}
          reproceso={reproceso}
        />
      )}
      {hecho && <Subida r={hecho} />}
    </div>
  );
}
