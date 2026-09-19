/**
 * Dataset mock, determinista (semilla fija) y fiel al estado real medido:
 *   431 PAGAR · 49 ESCALAR · 20 NO_PAGAR
 *   2.407.583,91 € aprobados · 60.034,52 € bloqueados · 92.068,14 € en espera
 *   471 por vía determinista (5,4 ms, 0 €) · 29 imágenes sin capa de texto
 * Cuando se enchufe alberto.db, este fichero desaparece: las páginas solo
 * conocen la interfaz de src/lib/data.ts.
 */
import type {
  Asiento, CamposFactura, Decision, Documento, Evento, Extraccion, Nota,
  Proveedor, ReglaVeredicto, Resultado, Veredicto,
} from "../types";

// PRNG con semilla fija: el mock es idéntico en cada build.
function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rnd = mulberry32(500);
const pick = <T,>(xs: T[]) => xs[Math.floor(rnd() * xs.length)];
const entre = (a: number, b: number) => a + Math.floor(rnd() * (b - a + 1));

// ── proveedores ─────────────────────────────────────────────────────
const RAZONES = [
  "Suministros Iberia SL", "Papelera del Duero SA", "Logística Manzanares SL",
  "Catering Hermanos Ruiz SL", "Ferretería Industrial Vega SA", "Textiles Alcores SL",
  "Química Tajuña SA", "Electricidad Montiel SL", "Vidrios y Cristales Sur SA",
  "Maderas Carballo SL", "Transportes Peláez SL", "Envases del Henares SA",
  "Limpiezas Buendía SL", "Seguridad Ondarreta SL", "Informática Bidasoa SA",
  "Rotulación Levante SL", "Climatización Arlanza SA", "Áridos y Hormigones Genil SL",
  "Etiquetas Turia SL", "Mensajería Urgente Sil SA",
];
export const proveedores: Proveedor[] = RAZONES.map((razon, i) => {
  const n = i + 1;
  return {
    proveedor_id: `P${String(n).padStart(3, "0")}`,
    razon_social: razon,
    nif: `B${String(28000000 + n * 41761).padStart(8, "0")}`,
    iban: `ES${String(10 + n)}2100${String(400000000 + n * 7013577).padStart(10, "0")}`,
    condiciones_dias: pick([30, 30, 45, 60]),
  };
});

// ── reparto de los 500 ──────────────────────────────────────────────
type Caso =
  | "ok"                 // PAGAR
  | "erp_pagada"         // NO_PAGAR · el ERP ya la daba por pagada
  | "iban_mal"           // NO_PAGAR · IBAN no coincide con el maestro
  | "nif_inexistente"    // NO_PAGAR · NIF no está y el pedido no consta
  | "importe_desvio"     // ESCALAR  · importe no cuadra con el pedido
  | "falta_fecha"        // ESCALAR  · campo faltante
  | "falta_iban"         // ESCALAR  · campo faltante
  | "revisar"            // ESCALAR  · pendiente_revisar en el Excel
  | "sin_texto";         // ESCALAR  · imagen, sin capa de texto

const CASOS: [Caso, number][] = [
  ["ok", 431], ["erp_pagada", 9], ["iban_mal", 6], ["nif_inexistente", 5],
  ["importe_desvio", 14], ["falta_fecha", 3], ["falta_iban", 1],
  ["revisar", 2], ["sin_texto", 29],
];

/** Reparte `total_cent` entre n importes plausibles, cuadrando al céntimo. */
function repartir(total_cent: number, n: number): number[] {
  const pesos = Array.from({ length: n }, () => 0.35 + rnd());
  const suma = pesos.reduce((a, b) => a + b, 0);
  const xs = pesos.map((p) => Math.round((total_cent * p) / suma));
  xs[n - 1] += total_cent - xs.reduce((a, b) => a + b, 0);
  return xs;
}

const IMPORTES: Record<string, number[]> = {
  ok: repartir(240758391, 431),
  erp_pagada: repartir(1615992, 9),
  iban_mal: repartir(2482920, 6),
  nif_inexistente: repartir(1904540, 5),
  // los 20 escalados con importe conocido suman 92.068,14 €
  ...(() => {
    const xs = repartir(9206814, 20);
    return {
      importe_desvio: xs.slice(0, 14),
      falta_fecha: xs.slice(14, 17),
      falta_iban: xs.slice(17, 18),
      revisar: xs.slice(18, 20),
    };
  })(),
};

// casos barajados para que el muro salga moteado y no en bloques
const casos: Caso[] = CASOS.flatMap(([c, n]) => Array(n).fill(c) as Caso[]);
for (let i = casos.length - 1; i > 0; i--) {
  const j = Math.floor(rnd() * (i + 1));
  [casos[i], casos[j]] = [casos[j], casos[i]];
}

// ── generación por documento ────────────────────────────────────────
const sha = (s: string) => {
  let h = 2166136261;
  for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return (h >>> 0).toString(16).padStart(8, "0").repeat(8).slice(0, 64);
};
const fechaDoc = (i: number) => {
  const dia = Math.floor((i * 320) / 500) + 1; // ene–nov 2026
  const m = String(Math.floor(dia / 28) + 1).padStart(2, "0");
  const d = String((dia % 28) + 1).padStart(2, "0");
  return `2026-${m}-${d}`;
};

export const documentos: Documento[] = [];
export const extracciones = new Map<string, Extraccion>();
export const decisionesV3 = new Map<string, Decision>();
export const decisionesV4 = new Map<string, Decision>();
export const asientos = new Map<string, Asiento>(); // por pedido
export const eventos: Evento[] = [];
export const casoDe = new Map<string, Caso>();

const cumple = (regla: string, descripcion: string, evidencia: Record<string, string | number | null> = {}): ReglaVeredicto =>
  ({ regla, descripcion, veredicto: "CUMPLE", evidencia });
const v = (regla: string, descripcion: string, veredicto: Veredicto, evidencia: Record<string, string | number | null>): ReglaVeredicto =>
  ({ regla, descripcion, veredicto, evidencia });

const D = {
  r1: "El NIF está en el maestro y el IBAN coincide con el del maestro",
  r2: "El pedido existe, pertenece al proveedor y el importe coincide (fuente: ERP)",
  r3: "El IVA está bien calculado y el total es base más IVA",
  r4: "La fecha es válida y no futura",
  r5: "El pedido está PENDIENTE en el ERP (nunca pagar dos veces)",
  r6: "La factura no supera las condiciones de pago del proveedor",
};

let contadorEvento = 1;
const counts = new Map<Caso, number>();

casos.forEach((caso, i) => {
  const idx = counts.get(caso) ?? 0;
  counts.set(caso, idx + 1);

  const prov = proveedores[i % proveedores.length];
  const f = fechaDoc(i);
  const file_id = `${f}_${prov.proveedor_id}${caso === "sin_texto" ? "-scan" : ""}.pdf`;
  const doc_id = sha(file_id);
  const pedido = `PO-2026-${String(i + 1).padStart(4, "0")}`;
  const total = IMPORTES[caso]?.[idx] ?? 0;
  const base = Math.round(total / 1.21);
  const iva = total - base;
  const esImagen = caso === "sin_texto";
  const ts = (h: number, m: number) => `2026-09-19T${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(entre(0, 59)).padStart(2, "0")}`;

  casoDe.set(doc_id, caso);
  documentos.push({
    doc_id, file_id, ruta: `caja/facturas/${file_id}`,
    bytes: entre(38000, 240000), tiene_texto: !esImagen, lote: "lote1",
    estado: "decidido", intentos: 1, ultimo_error: null, creado_at: ts(10, 1),
  });

  asientos.set(pedido, {
    asiento_id: `AS-${String(i + 1).padStart(4, "0")}`, pedido,
    nif: caso === "nif_inexistente" ? null : prov.nif,
    proveedor_id: caso === "nif_inexistente" ? null : prov.proveedor_id,
    importe_esperado_cent: caso === "importe_desvio" ? total - entre(9000, 60000) : total,
    estado: caso === "erp_pagada" ? "PAGADA" : "PENDIENTE",
    fecha_registro: f,
  });

  const campos: CamposFactura = {
    numero: `F-2026-${String(i + 101).padStart(4, "0")}`,
    fecha: caso === "falta_fecha" ? null : f,
    nif_emisor: caso === "nif_inexistente" ? `B${String(99000000 + i).padStart(8, "0")}` : prov.nif,
    proveedor: prov.razon_social,
    pedido,
    iban: caso === "falta_iban" ? null
      : caso === "iban_mal" ? `ES91BANCO${String(660000000 + i * 977).padStart(10, "0")}`
      : prov.iban,
    base_cent: base, iva_pct: 21, iva_cent: iva, total_cent: total,
  };

  if (!esImagen) {
    extracciones.set(doc_id, {
      doc_id, intento: 1, plantilla: pick(["factura_a", "factura_b", "factura_c"]),
      via: "determinista", campos,
      campos_faltantes: caso === "falta_fecha" ? ["fecha"] : caso === "falta_iban" ? ["iban"] : [],
      cuadra_interna: true, coste_eur: 0, latencia_ms: entre(3, 9), creado_at: ts(10, 2),
    });
  } else {
    extracciones.set(doc_id, {
      doc_id, intento: 1, plantilla: null, via: null,
      campos: { numero: null, fecha: null, nif_emisor: null, proveedor: null, pedido: null, iban: null, base_cent: null, iva_pct: null, iva_cent: null, total_cent: null },
      campos_faltantes: ["todos"], cuadra_interna: null, coste_eur: 0,
      latencia_ms: entre(2, 5), creado_at: ts(10, 2),
    });
  }

  // ── veredictos y decisión (política: R1/R5 fallan a NO_PAGAR, resto a ESCALAR) ──
  const asiento = asientos.get(pedido)!;
  let reglas: ReglaVeredicto[];
  let result: Resultado = "PAGAR";
  let motivo: string | null = null;

  if (esImagen) {
    reglas = [
      v("R1_nif_iban", D.r1, "SIN_DATOS", { faltan: "todos los campos" }),
      v("R2_pedido", D.r2, "SIN_DATOS", { faltan: "pedido, total" }),
      v("R3_iva", D.r3, "SIN_DATOS", { faltan: "base, iva, total" }),
      v("R4_fecha", D.r4, "SIN_DATOS", { faltan: "fecha" }),
      v("R5_estado_erp", D.r5, "SIN_DATOS", { faltan: "pedido" }),
    ];
    result = "ESCALAR";
    motivo = "documento sin capa de texto: ningún extractor cerró la aritmética";
  } else {
    const r1 = caso === "iban_mal"
      ? v("R1_nif_iban", D.r1, "FALLA", { nif: campos.nif_emisor, iban_factura: campos.iban, iban_maestro: prov.iban })
      : caso === "nif_inexistente"
      ? v("R1_nif_iban", D.r1, "FALLA", { nif: campos.nif_emisor, nif_maestro: "no consta", pedido: "no consta en el ERP" })
      : caso === "falta_iban"
      ? v("R1_nif_iban", D.r1, "SIN_DATOS", { faltan: "iban" })
      : cumple("R1_nif_iban", D.r1, { nif: campos.nif_emisor, iban: campos.iban });
    const r2 = caso === "importe_desvio"
      ? v("R2_pedido", D.r2, "FALLA", { pedido, importe_factura: total, importe_erp: asiento.importe_esperado_cent, desvio: total - asiento.importe_esperado_cent })
      : caso === "nif_inexistente"
      ? v("R2_pedido", D.r2, "FALLA", { pedido, importe_erp: "pedido no consta" })
      : cumple("R2_pedido", D.r2, { pedido, importe_factura: total, importe_erp: asiento.importe_esperado_cent });
    const r3 = cumple("R3_iva", D.r3, { base: base, iva_pct: 21, iva: iva, total: total });
    const r4 = caso === "falta_fecha"
      ? v("R4_fecha", D.r4, "SIN_DATOS", { faltan: "fecha" })
      : cumple("R4_fecha", D.r4, { fecha: f });
    const r5 = caso === "erp_pagada"
      ? v("R5_estado_erp", D.r5, "FALLA", { pedido, estado: "PAGADA", fecha_pago_erp: asiento.fecha_registro })
      : cumple("R5_estado_erp", D.r5, { pedido, estado: "PENDIENTE" });
    reglas = [r1, r2, r3, r4, r5];

    switch (caso) {
      case "erp_pagada": result = "NO_PAGAR"; motivo = "el ERP ya da el pedido por PAGADA: nunca pagar dos veces"; break;
      case "iban_mal": result = "NO_PAGAR"; motivo = "el IBAN de la factura no coincide con el del maestro"; break;
      case "nif_inexistente": result = "NO_PAGAR"; motivo = "NIF inexistente en el maestro y pedido que no consta"; break;
      case "importe_desvio": result = "ESCALAR"; motivo = "el importe no cuadra con el pedido del ERP"; break;
      case "falta_fecha": result = "ESCALAR"; motivo = "falta la fecha: no se puede validar R4"; break;
      case "falta_iban": result = "ESCALAR"; motivo = "falta el IBAN: no se puede validar R1"; break;
      case "revisar": result = "ESCALAR"; motivo = "pedido marcado pendiente_revisar en el Excel"; break;
      default: result = "PAGAR"; motivo = null;
    }
  }

  const dec: Decision = {
    doc_id, norma_version: "v3", snapshot_erp: "erp-0003", snapshot_maestro: "mv-0001",
    result, motivo, reglas, coste_eur: 0, latencia_ms: entre(4, 8), creado_at: ts(10, 3),
  };
  decisionesV3.set(doc_id, dec);

  // norma v4 (la del sábado): añade R6_vencimiento → 11 PAGAR pasan a ESCALAR
  const venceV4 = caso === "ok" && i % 39 === 0;
  const r6: ReglaVeredicto = venceV4
    ? v("R6_vencimiento", D.r6, "FALLA", { fecha: f, condiciones_dias: prov.condiciones_dias, dias_transcurridos: (prov.condiciones_dias ?? 30) + entre(4, 21) })
    : esImagen
    ? v("R6_vencimiento", D.r6, "SIN_DATOS", { faltan: "fecha" })
    : cumple("R6_vencimiento", D.r6, { condiciones_dias: prov.condiciones_dias });
  decisionesV4.set(doc_id, {
    ...dec, norma_version: "v4", reglas: [...reglas, r6],
    result: venceV4 ? "ESCALAR" : result,
    motivo: venceV4 ? "supera las condiciones de pago del proveedor (norma v4, R6)" : motivo,
    creado_at: ts(18, 24),
  });

  // eventos de la pasada
  const pasos: [string, string][] = [
    ["ingesta", `documento registrado (${esImagen ? "sin" : "con"} capa de texto)`],
    ["extrae", esImagen ? "sin capa de texto: se escala sin inventar cifras" : `extraído por vía determinista en ${extracciones.get(doc_id)!.latencia_ms} ms · 0,00 €`],
    ["decide", `norma v3 → ${result}${motivo ? ` · ${motivo}` : ""}`],
  ];
  pasos.forEach(([etapa, mensaje], j) => {
    eventos.push({
      id: contadorEvento++, doc_id, etapa,
      nivel: etapa === "decide" && result !== "PAGAR" ? "warn" : "info",
      mensaje, at: ts(10, 1 + j),
    });
  });
});

// ── notas de Alberto (del Excel) ────────────────────────────────────
export const notas: Nota[] = [
  { id: 1, ambito: "regla", clave: "R3_iva", texto: "Preguntar a Sonia lo del IVA reducido antes de tocar nada.", origen: "excel", creado_at: "2026-09-18T09:00:00" },
  { id: 2, ambito: "pedido", clave: "PO-2026-0007", texto: "pendiente_revisar: el proveedor mandó dos versiones de esta factura.", origen: "excel", creado_at: "2026-09-18T09:00:00" },
  { id: 3, ambito: "proveedor", clave: "P002", texto: "P002 aparece dos veces en el Excel: usar la fila con el IBAN que acaba en 77.", origen: "excel", creado_at: "2026-09-18T09:00:00" },
  { id: 4, ambito: "general", clave: null, texto: "El Excel dice ABIERTO en los 516 pedidos: el estado bueno es el del ERP.", origen: "excel", creado_at: "2026-09-18T09:00:00" },
];

export const proveedorPorNif = new Map(proveedores.map((p) => [p.nif, p]));

/** Docs que una regla de vencimiento haría escalar (los 11 del cambio v3→v4). */
export const venceDocs = new Set(
  [...decisionesV4.entries()]
    .filter(([id, d]) => d.result !== decisionesV3.get(id)!.result)
    .map(([id]) => id),
);

