"use client";

import { useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import type { ResultadoIngesta } from "@/lib/ingesta";
import { subirFacturas } from "./actions";

/** Añadir facturas: una sola o en bulk (selección múltiple o arrastrar la carpeta encima). */
export function SubirFacturas() {
  const input = useRef<HTMLInputElement>(null);
  const [arrastrando, setArrastrando] = useState(false);
  const [resultado, setResultado] = useState<ResultadoIngesta | null>(null);
  const [pendiente, start] = useTransition();
  const router = useRouter();

  const enviar = (files: FileList | File[]) => {
    const fd = new FormData();
    for (const f of Array.from(files)) fd.append("ficheros", f);
    start(async () => {
      setResultado(await subirFacturas(fd));
      router.refresh();
    });
  };

  return (
    <div className="space-y-2">
      <div
        onDragOver={(e) => { e.preventDefault(); setArrastrando(true); }}
        onDragLeave={() => setArrastrando(false)}
        onDrop={(e) => { e.preventDefault(); setArrastrando(false); enviar(e.dataTransfer.files); }}
        className={`flex flex-wrap items-center gap-3 rounded-xl border-2 border-dashed px-4 py-3 transition-colors ${
          arrastrando ? "border-accent bg-accent/5" : "border-line bg-card"
        }`}
      >
        <button
          onClick={() => input.current?.click()}
          disabled={pendiente}
          className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-paper hover:bg-ink/85 disabled:opacity-50"
        >
          {pendiente ? "ingiriendo…" : "Añadir facturas"}
        </button>
        <span className="text-sm text-muted">
          una o varias en PDF — o arrástralas aquí. La ingesta es idempotente:
          repetir un fichero no crea dos documentos.
        </span>
        <input
          ref={input}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          hidden
          onChange={(e) => { if (e.target.files?.length) enviar(e.target.files); e.target.value = ""; }}
        />
      </div>

      {resultado && (
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
          {resultado.añadidas.length > 0 && (
            <span className="text-ok">
              {resultado.añadidas.length} ingerida{resultado.añadidas.length !== 1 && "s"} — pendiente
              {resultado.añadidas.length !== 1 && "s"} de la próxima pasada
            </span>
          )}
          {resultado.duplicadas.length > 0 && (
            <span className="text-warn">
              {resultado.duplicadas.length} ya registrada{resultado.duplicadas.length !== 1 && "s"} (ignoradas por idempotencia)
            </span>
          )}
          {resultado.rechazadas.length > 0 && (
            <span className="text-bad">
              {resultado.rechazadas.length} rechazada{resultado.rechazadas.length !== 1 && "s"}: solo se aceptan PDF
            </span>
          )}
        </div>
      )}
    </div>
  );
}
