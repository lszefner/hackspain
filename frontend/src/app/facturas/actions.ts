"use server";

import { revalidatePath } from "next/cache";
import { ingerir, type ResultadoIngesta } from "@/lib/ingesta";

/**
 * Ingesta desde la web: una factura o un lote, mismo camino.
 * En producción: se escribe el PDF en la caja y se lanza `alberto ingesta`
 * (idempotente por sha256). El mock registra nombre y tamaño.
 */
export async function subirFacturas(formData: FormData): Promise<ResultadoIngesta> {
  const ficheros = formData
    .getAll("ficheros")
    .filter((f): f is File => f instanceof File && f.size > 0)
    .map((f) => ({ name: f.name, size: f.size }));
  const r = ingerir(ficheros);
  if (r.añadidas.length) {
    revalidatePath("/facturas");
    revalidatePath("/logs");
  }
  return r;
}
