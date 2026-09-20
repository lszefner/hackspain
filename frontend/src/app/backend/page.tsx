"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { Resumen } from "@/lib/engine/types";

type Result = { path: string; data?: unknown; error?: string };
async function read(path: string, signal: AbortSignal): Promise<Result> {
  try {
    const response = await fetch(`/api/engine/${path}`, { cache: "no-store", signal });
    const data = await response.json();
    if (!response.ok) throw new Error(`${response.status}: ${data.error ?? "Error del backend"}`);
    return { path, data };
  } catch (error) {
    return { path, error: error instanceof Error ? error.message : "Error del backend" };
  }
}
function Json({ value }: { value: unknown }) {
  return <pre className="max-h-[36rem] overflow-auto rounded-md border border-line bg-card p-4 text-xs leading-relaxed">{JSON.stringify(value, null, 2)}</pre>;
}
function ResponseView({ result }: { result: Result }) {
  return <section className="min-w-0 space-y-3">
    <h3 className="break-all font-mono text-sm">GET /api/{result.path}</h3>
    {result.error ? <p role="alert" className="text-sm text-bad">{result.error}</p> : <>
      {result.data && typeof result.data === "object" && !Array.isArray(result.data) ? Object.entries(result.data).map(([key, value]) => <details key={key} className="rounded-md border border-line bg-card p-3">
        <summary className="cursor-pointer break-words text-sm font-semibold">{key}{value === null ? " · sin datos registrados" : Array.isArray(value) ? ` · ${value.length} elementos` : ""}</summary>
        <div className="mt-3"><Json value={value} /></div>
      </details>) : <Json value={result.data} />}
      <details><summary className="cursor-pointer text-sm text-accent">Respuesta JSON completa</summary><div className="mt-3"><Json value={result.data} /></div></details>
    </>}
  </section>;
}

export default function BackendPage() {
  const [overview, setOverview] = useState<Result[]>([]);
  const [detail, setDetail] = useState<Result[]>([]);
  const [selected, setSelected] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setOverview([]);
    void Promise.all(["resumen", "salud", "estado"].map((path) => read(path, controller.signal))).then((results) => {
      if (!controller.signal.aborted) { setOverview(results); setLoading(false); }
    });
    return () => controller.abort();
  }, [revision]);

  useEffect(() => {
    const controller = new AbortController();
    setDetail([]);
    if (!selected) { setDetailLoading(false); return () => controller.abort(); }
    setDetailLoading(true);
    const path = `factura/${encodeURIComponent(selected)}`;
    void Promise.all([read(path, controller.signal), read(`${path}/flujo`, controller.signal)]).then((results) => {
      if (!controller.signal.aborted) { setDetail(results); setDetailLoading(false); }
    });
    return () => controller.abort();
  }, [selected, revision]);

  const summary = overview.find((result) => result.path === "resumen")?.data as Resumen | undefined;
  const term = query.trim().toLocaleLowerCase("es");
  const rows = (summary?.facturas ?? []).filter((row) => (!status || row.estado === status) && row.file_id.toLocaleLowerCase("es").includes(term));

  return <main className="mx-auto max-w-[1500px] space-y-6 p-4 sm:p-8">
    <Link href="/" className="text-sm text-accent underline underline-offset-4">Volver a la mesa</Link>
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><p className="text-sm text-muted">API de revisión de facturas</p><h1 className="mt-1 text-3xl font-semibold tracking-tight">Datos del backend</h1><p className="mt-2 max-w-3xl text-sm text-muted">Explora las facturas y abre sus datos guardados: extracción, evaluación, revisión y etapas del flujo. Los campos sin registro se muestran tal como los devuelve la API.</p></div>
      <button disabled={loading || detailLoading} onClick={() => setRevision((value) => value + 1)} className="rounded-md border border-line bg-card px-4 py-2 text-sm font-semibold disabled:opacity-50">{loading ? "Cargando…" : "Actualizar datos"}</button>
    </header>
    {loading ? <p role="status">Consultando el backend…</p> : null}
    {overview.filter((result) => result.error).map((result) => <p key={result.path} role="alert" className="text-sm text-bad">/api/{result.path}: {result.error}. Comprueba el backend en el puerto 8010 y vuelve a actualizar.</p>)}
    {summary ? <>
      <div className="flex flex-wrap gap-5 border-y border-line py-4 text-sm"><strong>{summary.total} facturas</strong>{Object.entries(summary.conteo).map(([key, value]) => <span key={key}>{key}: {value}</span>)}</div>
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(280px,1fr)_minmax(0,2fr)]">
        <section className="min-w-0 space-y-3" aria-label="Facturas del backend">
          <label className="flex flex-col gap-1 text-sm">Buscar factura<input value={query} onChange={(event) => setQuery(event.target.value)} className="rounded-md border border-line bg-card px-3 py-2" placeholder="Nombre del PDF…" /></label>
          <label className="flex flex-col gap-1 text-sm">Estado<select value={status} onChange={(event) => setStatus(event.target.value)} className="rounded-md border border-line bg-card px-3 py-2"><option value="">Todos</option>{Object.keys(summary.conteo).map((value) => <option key={value}>{value}</option>)}</select></label>
          <p aria-live="polite" className="text-xs text-muted">{rows.length} de {summary.total} facturas</p>
          <ul className="max-h-[65vh] overflow-auto rounded-md border border-line bg-card">{rows.map((row) => <li key={row.file_id} className="border-b border-line last:border-0"><button aria-pressed={selected === row.file_id} onClick={() => setSelected(row.file_id)} className={`w-full p-3 text-left hover:bg-soft focus-visible:outline-2 focus-visible:outline-accent ${selected === row.file_id ? "bg-soft" : ""}`}><span className="block break-all text-sm font-semibold">{row.file_id}</span><span className="mt-1 block text-xs text-muted">{row.estado} · Decisión del evaluador: {row.decision ?? "sin evaluación"}</span></button></li>)}</ul>
          {!rows.length ? <p className="text-sm text-muted">No hay facturas que coincidan con estos filtros.</p> : null}
        </section>
        <section className="min-w-0 space-y-6" aria-label="Detalle de la factura">
          <h2 className="break-all text-xl font-semibold">{selected || "Selecciona una factura"}</h2>
          {!selected ? <p className="text-sm text-muted">Abre una factura para consultar todos sus campos y su flujo guardado.</p> : null}
          {detailLoading ? <p role="status">Cargando factura y flujo…</p> : detail.map((result) => <ResponseView key={result.path} result={result} />)}
        </section>
      </div>
    </> : null}
    <section className="space-y-3 border-t border-line pt-6"><h2 className="text-lg font-semibold">Respuestas generales de la API</h2>{overview.map((result) => <details key={result.path}><summary className="cursor-pointer py-2 font-mono text-sm">GET /api/{result.path}</summary><ResponseView result={result} /></details>)}</section>
  </main>;
}
