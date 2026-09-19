"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { activarNorma, ensayarNorma, publicarNorma, type Ensayo } from "@/lib/normas";

export async function activar(version: string) {
  activarNorma(version);
  revalidatePath("/", "layout"); // la norma activa afecta a todas las vistas
}

export async function ensayar(yaml: string): Promise<Ensayo> {
  return ensayarNorma(yaml);
}

export async function publicar(yaml: string, autor: string, motivo: string): Promise<string[]> {
  const r = publicarNorma(yaml, autor, motivo);
  if (!r.ok) return r.errores;
  revalidatePath("/", "layout");
  redirect(`/manual?version=${r.version}&publicada=1`);
}
