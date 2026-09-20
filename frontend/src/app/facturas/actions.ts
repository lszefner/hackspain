"use server";
import type { ResultadoIngesta } from "@/lib/ingesta";
export async function subirFacturas(_formData: FormData): Promise<ResultadoIngesta> {
  void _formData;
  throw new Error("Upload processing is not connected to the canonical backend. No invoice was saved or evaluated.");
}
