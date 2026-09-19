"use server";

/**
 * El puente con `alberto web`. Los bytes del PDF viajan en crudo con el
 * nombre en `X-File-Name`: así el servidor de la API sigue siendo stdlib,
 * sin parser multipart ni dependencia nueva.
 */
import { revalidatePath } from "next/cache";
import type { EstadoDocumento, ResultadoReproceso, ResultadoSubida } from "@/lib/types";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";
const SIN_BACKEND = "la subida necesita `alberto web` levantado (falta NEXT_PUBLIC_API_BASE)";

function fallo(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** ¿Está ya este contenido en la plataforma? El sha256 se calcula en el navegador. */
export async function comprobar(sha256: string): Promise<EstadoDocumento | null> {
  if (!BASE) return null; // sin backend no hay nada que comprobar; `subir` ya lo dirá
  try {
    const res = await fetch(`${BASE}/api/documento/${encodeURIComponent(sha256)}`, {
      cache: "no-store",
    });
    if (res.status === 404) return null;
    if (!res.ok) return null;
    return (await res.json()) as EstadoDocumento;
  } catch {
    return null;
  }
}

export async function subir(form: FormData): Promise<ResultadoSubida> {
  if (!BASE) return { ok: false, errores: [SIN_BACKEND] };
  const f = form.get("fichero");
  if (!(f instanceof File) || f.size === 0) {
    return { ok: false, errores: ["elige un PDF"] };
  }
  try {
    const res = await fetch(`${BASE}/api/subir`, {
      method: "POST",
      headers: {
        "Content-Type": "application/pdf",
        // NFC antes de codificar: el file_id acaba en el JSONL y el
        // verificador compara cadenas.
        "X-File-Name": encodeURIComponent(f.name.normalize("NFC")),
      },
      body: Buffer.from(await f.arrayBuffer()),
    });
    // 409 (duplicado) y 422 (no es un PDF) ya vienen con esta forma.
    const r = (await res.json()) as ResultadoSubida;
    if (r.ok) revalidatePath("/", "layout"); // cambian muro, KPIs y facturas
    return r;
  } catch (e) {
    return { ok: false, errores: [`no se pudo hablar con la API: ${fallo(e)}`] };
  }
}

export async function reprocesar(doc_id: string): Promise<ResultadoReproceso> {
  if (!BASE) return { ok: false, errores: [SIN_BACKEND] };
  try {
    const res = await fetch(`${BASE}/api/reprocesar`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc_id }),
    });
    const r = (await res.json()) as ResultadoReproceso;
    if (r.ok) revalidatePath("/", "layout");
    return r;
  } catch (e) {
    return { ok: false, errores: [`no se pudo hablar con la API: ${fallo(e)}`] };
  }
}
