/**
 * El almacén de normas: la parte del mock que hace de `alberto decide --norma`.
 * - Una norma publicada es INMUTABLE; editar es publicar una versión nueva.
 * - Publicar reprocesa (simulado) y AÑADE decisiones; nunca borra las anteriores.
 * - Siempre hay una versión ACTIVA (por defecto, la última publicada), que es
 *   la que usan el muro y la bandeja si no se pide otra.
 * En producción esto es una tabla `normas` + `alberto decide`; el estado vive
 * aquí en memoria porque es una demo con mock.
 */
import { load } from "js-yaml";
import type { CambioNorma, Decision, ReglaVeredicto, Resultado } from "./types";
import * as mock from "./mock/dataset";

// ── el contrato del YAML ────────────────────────────────────────────

export interface ReglaDef {
  id: string;
  descripcion: string;
  tipo: string;
  requiere: string[];
  params: Record<string, string | number>;
}

export interface NormaVersion {
  version: string;
  yaml: string;
  reglas: ReglaDef[];
  autor: string;
  motivo: string;
  creado_at: string;
  publicada: boolean;
}

/** Los tipos de comprobación que el motor sabe ejecutar. Un tipo nuevo = tocar código. */
export const TIPOS_MOTOR: Record<string, string> = {
  nif_iban: "cruza NIF e IBAN de la factura contra el maestro de proveedores",
  pedido_importe: "cruza el importe contra el pedido en el ERP",
  iva: "recalcula el IVA y comprueba que total = base + IVA",
  fecha: "valida la fecha (existe, formato, no futura)",
  estado_erp: "comprueba el estado del pedido en el ERP",
  vencimiento: "cruza la fecha con las condiciones de pago del proveedor",
};

/** Política vigente (politica.yaml): a qué resultado va cada tipo cuando FALLA. */
export const POLITICA_FALLA: Record<string, Resultado> = {
  nif_iban: "NO_PAGAR",
  estado_erp: "NO_PAGAR",
  pedido_importe: "ESCALAR",
  iva: "ESCALAR",
  fecha: "ESCALAR",
  vencimiento: "ESCALAR",
};

// ── parseo y validación ─────────────────────────────────────────────

export function parsearNorma(yaml: string): { version: string; reglas: ReglaDef[]; errores: string[] } {
  const errores: string[] = [];
  let raw: unknown;
  try {
    raw = load(yaml);
  } catch (e) {
    return { version: "", reglas: [], errores: [`YAML inválido: ${(e as Error).message.split("\n")[0]}`] };
  }
  const doc = (raw ?? {}) as { version?: unknown; reglas?: unknown };
  const version = typeof doc.version === "string" ? doc.version : "";
  if (!version) errores.push("Falta el campo `version` (p. ej. v5).");
  if (!Array.isArray(doc.reglas) || doc.reglas.length === 0) {
    errores.push("Falta la lista `reglas` o está vacía.");
    return { version, reglas: [], errores };
  }
  const reglas: ReglaDef[] = [];
  const vistos = new Set<string>();
  for (const [i, r] of (doc.reglas as Record<string, unknown>[]).entries()) {
    const id = typeof r.id === "string" ? r.id : "";
    const tipo = typeof r.tipo === "string" ? r.tipo : "";
    const descripcion = typeof r.descripcion === "string" ? r.descripcion : "";
    if (!id) errores.push(`Regla ${i + 1}: falta \`id\`.`);
    else if (vistos.has(id)) errores.push(`Regla \`${id}\`: id duplicado.`);
    vistos.add(id);
    if (!descripcion) errores.push(`Regla \`${id || i + 1}\`: falta \`descripcion\` — el manual lo lee un humano.`);
    if (!tipo) errores.push(`Regla \`${id || i + 1}\`: falta \`tipo\`.`);
    else if (!TIPOS_MOTOR[tipo])
      errores.push(
        `Regla \`${id}\`: el motor no sabe comprobar \`${tipo}\`. Tipos disponibles: ${Object.keys(TIPOS_MOTOR).join(", ")}. Un tipo nuevo requiere tocar código (y un ADR).`,
      );
    const requiere = Array.isArray(r.requiere) ? (r.requiere as string[]).map(String) : [];
    const params: Record<string, string | number> = {};
    for (const [k, v] of Object.entries(r)) {
      if (["id", "descripcion", "tipo", "requiere"].includes(k)) continue;
      if (typeof v === "string" || typeof v === "number") params[k] = v;
    }
    if (typeof params.tolerancia === "number")
      errores.push(`Regla \`${id}\`: \`tolerancia\` debe ir entre comillas ("0.01") — los importes son Decimal, nunca float.`);
    reglas.push({ id, descripcion, tipo, requiere, params });
  }
  return { version, reglas, errores };
}

// ── simulador: qué decidiría el motor con estas reglas ──────────────
// Reproduce la política sobre los casos medidos del dataset. En producción
// esto es `alberto decide --norma vX` de verdad.

function simular(reglas: ReglaDef[], version: string): Map<string, Decision> {
  const tienen = new Set(reglas.map((r) => r.tipo));
  const out = new Map<string, Decision>();

  for (const doc of mock.documentos) {
    const caso = mock.casoDe.get(doc.doc_id)!;
    const base = mock.decisionesV3.get(doc.doc_id)!;

    let result: Resultado = "PAGAR";
    let motivo: string | null = null;
    let tipoFalla: string | null = null;

    if (caso === "sin_texto") {
      result = "ESCALAR";
      motivo = "documento sin capa de texto: ningún extractor cerró la aritmética";
    } else if (caso === "erp_pagada" && tienen.has("estado_erp")) {
      result = "NO_PAGAR"; motivo = "el ERP ya da el pedido por PAGADA: nunca pagar dos veces"; tipoFalla = "estado_erp";
    } else if ((caso === "iban_mal" || caso === "nif_inexistente") && tienen.has("nif_iban")) {
      result = "NO_PAGAR"; tipoFalla = "nif_iban";
      motivo = caso === "iban_mal" ? "el IBAN de la factura no coincide con el del maestro" : "NIF inexistente en el maestro y pedido que no consta";
    } else if (caso === "importe_desvio" && tienen.has("pedido_importe")) {
      result = "ESCALAR"; motivo = "el importe no cuadra con el pedido del ERP"; tipoFalla = "pedido_importe";
    } else if (caso === "falta_fecha" && tienen.has("fecha")) {
      result = "ESCALAR"; motivo = "falta la fecha: no se puede validar"; tipoFalla = "fecha";
    } else if (caso === "falta_iban" && tienen.has("nif_iban")) {
      result = "ESCALAR"; motivo = "falta el IBAN: no se puede validar"; tipoFalla = "nif_iban";
    } else if (caso === "revisar") {
      result = "ESCALAR"; motivo = "pedido marcado pendiente_revisar en el Excel";
    } else if (tienen.has("vencimiento") && mock.venceDocs.has(doc.doc_id)) {
      result = "ESCALAR"; motivo = `supera las condiciones de pago del proveedor (norma ${version})`;
      tipoFalla = "vencimiento";
    }

    // veredicto por regla: evidencia heredada de la v3/v4 cuando el id coincide
    const evidenciaDe = (id: string) =>
      base.reglas.find((r) => r.regla === id)?.evidencia ??
      mock.decisionesV4.get(doc.doc_id)!.reglas.find((r) => r.regla === id)?.evidencia ?? {};
    const veredictos: ReglaVeredicto[] = reglas.map((r) => ({
      regla: r.id,
      descripcion: r.descripcion,
      veredicto: caso === "sin_texto" ? "SIN_DATOS"
        : (caso === "falta_fecha" && r.tipo === "fecha") || (caso === "falta_iban" && r.tipo === "nif_iban") ? "SIN_DATOS"
        : r.tipo === tipoFalla ? "FALLA"
        : "CUMPLE",
      evidencia: evidenciaDe(r.id),
    }));

    out.set(doc.doc_id, {
      ...base, norma_version: version, result, motivo, reglas: veredictos,
      creado_at: new Date().toISOString().slice(0, 19),
    });
  }
  return out;
}

// ── el almacén ──────────────────────────────────────────────────────

const YAML_V3 = `# Norma de Pagos a Proveedores v3 (la vigente el viernes).
version: v3
reglas:
  - id: R1_nif_iban
    descripcion: El NIF está en el maestro y el IBAN de la factura coincide con el del maestro
    requiere: [nif_emisor, iban]
    tipo: nif_iban

  - id: R2_pedido
    descripcion: El pedido existe, pertenece al proveedor y el importe coincide con el del pedido
    requiere: [pedido, total]
    tipo: pedido_importe
    tolerancia: "0.01"
    campo_importe: total      # medido sobre la Caja: el ERP casa con el TOTAL, nunca con la base
    fuente: erp               # el Excel dice ABIERTO en los 516: no sirve como estado

  - id: R3_iva
    descripcion: El IVA está bien calculado y el total es base más IVA
    requiere: [base, iva_importe, total]
    tipo: iva
    tolerancia: "0.01"
    iva_estandar: "21"

  - id: R4_fecha
    descripcion: La fecha es válida y no futura
    requiere: [fecha]
    tipo: fecha

  - id: R5_estado_erp
    descripcion: El pedido está PENDIENTE en el ERP (nunca pagar dos veces)
    requiere: [pedido]
    tipo: estado_erp
    estado_pagable: PENDIENTE
`;

const YAML_V4 = YAML_V3.replace("# Norma de Pagos a Proveedores v3 (la vigente el viernes).", "# Norma de Pagos a Proveedores v4 (la del sábado a las 18:00).")
  .replace("version: v3", "version: v4") + `
  - id: R6_vencimiento
    descripcion: La factura no supera las condiciones de pago del proveedor
    requiere: [fecha]
    tipo: vencimiento
    fuente_condiciones: maestro   # condiciones_dias del proveedor (30/45/60)
`;

interface Almacen {
  normas: Map<string, NormaVersion>;
  decisiones: Map<string, Map<string, Decision>>;
  activa: string;
}

// sobrevive al hot-reload del dev server
const g = globalThis as unknown as { __normas?: Almacen };
function almacen(): Almacen {
  if (!g.__normas) {
    const normas = new Map<string, NormaVersion>();
    normas.set("v3", { version: "v3", yaml: YAML_V3, reglas: parsearNorma(YAML_V3).reglas, autor: "organización", motivo: "norma vigente el viernes", creado_at: "2026-09-18T19:00:00", publicada: true });
    normas.set("v4", { version: "v4", yaml: YAML_V4, reglas: parsearNorma(YAML_V4).reglas, autor: "alberto", motivo: "regla de vencimiento del lote 2 (sábado 18:00)", creado_at: "2026-09-19T18:24:00", publicada: true });
    const decisiones = new Map<string, Map<string, Decision>>();
    decisiones.set("v3", mock.decisionesV3);
    decisiones.set("v4", mock.decisionesV4);
    g.__normas = { normas, decisiones, activa: "v4" };
  }
  return g.__normas;
}

// orden de publicación = orden de inserción en el Map
export const listaNormas = (): NormaVersion[] => [...almacen().normas.values()];
export const getNorma = (v: string): NormaVersion | null => almacen().normas.get(v) ?? null;
export const normaActiva = (): string => almacen().activa;
export const versiones = (): string[] => listaNormas().map((n) => n.version);

export function activarNorma(v: string) {
  if (almacen().normas.has(v)) almacen().activa = v;
}

export const decisionesDe = (v: string): Map<string, Decision> =>
  almacen().decisiones.get(v) ?? almacen().decisiones.get("v3")!;

export function diff(de: string, a: string): CambioNorma[] {
  const dDe = decisionesDe(de);
  const dA = decisionesDe(a);
  const out: CambioNorma[] = [];
  for (const doc of mock.documentos) {
    const x = dDe.get(doc.doc_id)!;
    const y = dA.get(doc.doc_id)!;
    if (x.result !== y.result)
      out.push({ file_id: doc.file_id, de: x.result, a: y.result, motivo: y.motivo ?? "" });
  }
  return out;
}

export interface Ensayo {
  errores: string[];
  version: string;
  cambios: CambioNorma[];
  reparto: Record<Resultado, number>;
  base: string; // contra qué versión se compara
}

/** El ensayo en seco: qué pasaría con las 500 si esta norma se publicara. */
export function ensayarNorma(yaml: string): Ensayo {
  const { version, reglas, errores } = parsearNorma(yaml);
  const a = almacen();
  if (version && a.normas.has(version))
    errores.push(`La versión \`${version}\` ya está publicada y es inmutable: sube el número (siguiente libre: ${siguienteVersion()}).`);
  if (errores.length) return { errores, version, cambios: [], reparto: { PAGAR: 0, ESCALAR: 0, NO_PAGAR: 0 }, base: a.activa };

  const sim = simular(reglas, version);
  const baseDec = decisionesDe(a.activa);
  const cambios: CambioNorma[] = [];
  const reparto: Record<Resultado, number> = { PAGAR: 0, ESCALAR: 0, NO_PAGAR: 0 };
  for (const doc of mock.documentos) {
    const y = sim.get(doc.doc_id)!;
    reparto[y.result]++;
    const x = baseDec.get(doc.doc_id)!;
    if (x.result !== y.result)
      cambios.push({ file_id: doc.file_id, de: x.result, a: y.result, motivo: y.motivo ?? "sin motivo: pasa a PAGAR" });
  }
  return { errores: [], version, cambios, reparto, base: a.activa };
}

export function publicarNorma(yaml: string, autor: string, motivo: string): { ok: boolean; errores: string[]; version: string } {
  const ensayo = ensayarNorma(yaml);
  if (ensayo.errores.length) return { ok: false, errores: ensayo.errores, version: ensayo.version };
  if (!autor.trim()) return { ok: false, errores: ["Falta el autor: toda versión queda firmada."], version: ensayo.version };
  if (!motivo.trim()) return { ok: false, errores: ["Falta el motivo del cambio: es la primera pregunta del tribunal."], version: ensayo.version };
  const { version, reglas } = parsearNorma(yaml);
  const a = almacen();
  a.normas.set(version, {
    version, yaml, reglas, autor: autor.trim(), motivo: motivo.trim(),
    creado_at: new Date().toISOString().slice(0, 19), publicada: true,
  });
  a.decisiones.set(version, simular(reglas, version));
  a.activa = version; // la última publicada pasa a ser la activa
  return { ok: true, errores: [], version };
}

export function siguienteVersion(): string {
  const n = Math.max(...versiones().map((v) => parseInt(v.replace(/\D/g, ""), 10) || 0));
  return `v${n + 1}`;
}

/** Borrador inicial: copia de la activa con la versión subida. */
export function borradorInicial(): string {
  const activa = getNorma(normaActiva())!;
  const nueva = siguienteVersion();
  return activa.yaml
    .replace(/^#.*\n/, `# Norma de Pagos a Proveedores ${nueva} — borrador. Edita, ensaya y publica.\n`)
    .replace(/version: \S+/, `version: ${nueva}`);
}
