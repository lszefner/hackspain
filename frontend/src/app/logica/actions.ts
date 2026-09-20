"use server";
import type { Ensayo } from "@/lib/normas";
export async function activar(_version: string): Promise<void> {
  void _version;
  throw new Error("Rule activation is not connected. No rules were changed.");
}
export async function ensayar(_yaml: string): Promise<Ensayo> {
  void _yaml;
  throw new Error("Rule simulation is not connected. No evaluation was performed.");
}
export async function publicar(_yaml: string, _autor: string, _motivo: string): Promise<string[]> {
  void [_yaml, _autor, _motivo];
  return ["Rule publication is not connected. No rules were changed."];
}
