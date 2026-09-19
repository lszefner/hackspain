"""La implementacion real de la interfaz `DataSource` del frontend.

`frontend/src/lib/data.ts` declara la costura y la implementaba un mock.
Esto la implementa contra `alberto.db`, que es lo que de verdad se entrega.

Aqui NO se decide nada: se lee `extraccion_vigente`, `decisiones` y el
contexto que las acompana. Si la pantalla y el JSONL dijeran cosas
distintas, seria el mismo fallo que ya tuvimos entre `emite` y `explica`,
pero delante del jurado.

Dos traducciones al vocabulario del frontend, y solo dos:
  · los importes viajan en CENTIMOS enteros, no en Decimal
  · los veredictos son CUMPLE / FALLA / SIN_DATOS, no PASA / FALLA / NA
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from alberto.resolucion import decision_de, decisiones_de_lote

VEREDICTO = {"PASA": "CUMPLE", "FALLA": "FALLA", "NA": "SIN_DATOS"}
ETAPA = {"pendiente": "ingerida", "extraido": "extraida",
         "extraido_parcial": "extraida", "decidido": "decidida",
         "escalado": "decidida"}


# ------------------------------------------------------------- utilidades
def cent(valor: Any) -> int | None:
    """Decimal o cadena -> centimos enteros. El frontend no usa Decimal."""
    if valor in (None, "", "None"):
        return None
    try:
        return int((Decimal(str(valor)) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def _eur(valor: Any) -> float:
    try:
        return float(Decimal(str(valor or 0)))
    except (InvalidOperation, ValueError):
        return 0.0


def _campos(fila: sqlite3.Row | None) -> dict:
    return json.loads(fila["campos_json"]) if fila and fila["campos_json"] else {}


def _campos_factura(c: dict) -> dict:
    return {"numero": c.get("num_factura"), "fecha": c.get("fecha"),
            "nif_emisor": c.get("nif_emisor"), "proveedor": c.get("_proveedor"),
            "pedido": c.get("pedido"), "iban": c.get("iban"),
            "base_cent": cent(c.get("base")),
            "iva_pct": float(c["iva_pct"]) if c.get("iva_pct") else None,
            "iva_cent": cent(c.get("iva_importe")),
            "total_cent": cent(c.get("total"))}


def _percentil(valores: list[int], p: float) -> int:
    if not valores:
        return 0
    orden = sorted(valores)
    return orden[min(int(len(orden) * p), len(orden) - 1)]


# ------------------------------------------------------------------ normas
def normas(con: sqlite3.Connection) -> list[str]:
    return [f["norma_version"] for f in con.execute(
        "SELECT norma_version, MIN(creado_at) o FROM decisiones"
        " GROUP BY norma_version ORDER BY o")]


def norma_activa(con: sqlite3.Connection) -> str:
    fila = con.execute("SELECT norma_version FROM decisiones"
                       " ORDER BY creado_at DESC LIMIT 1").fetchone()
    return fila["norma_version"] if fila else "v3"


def _norma(con: sqlite3.Connection, norma: str | None) -> str:
    return norma or norma_activa(con)


# -------------------------------------------------------------------- kpis
def kpis(con: sqlite3.Connection, norma: str | None = None,
         *, lote: str = "lote1") -> dict:
    n = _norma(con, norma)
    filas = con.execute(
        "SELECT d.result, x.campos_json FROM decisiones d"
        " JOIN documentos doc USING(doc_id)"
        " LEFT JOIN extraccion_vigente x ON x.doc_id = d.doc_id"
        " WHERE d.norma_version=? AND doc.lote=?", (n, lote)).fetchall()

    por: dict[str, dict] = {}
    for f in filas:
        e = por.setdefault(f["result"], {"result": f["result"], "n": 0,
                                         "total_cent": 0, "sin_importe": 0,
                                         "_alguno": False})
        e["n"] += 1
        c = cent(json.loads(f["campos_json"] or "{}").get("total"))
        if c is None:
            e["sin_importe"] += 1
        else:
            e["total_cent"] += c
            e["_alguno"] = True
    for e in por.values():
        if not e.pop("_alguno"):
            e["total_cent"] = None

    coste = con.execute(
        "SELECT sum(CAST(coste_eur AS REAL)) s FROM extracciones x"
        " JOIN documentos d USING(doc_id) WHERE d.lote=?", (lote,)).fetchone()
    pasada = con.execute(
        "SELECT inicio, fin FROM pasadas WHERE verbo='decide' AND fin IS NOT NULL"
        " ORDER BY inicio DESC LIMIT 1").fetchone()
    return {"norma": n, "nDocs": len(filas),
            "porResultado": sorted(por.values(), key=lambda e: -e["n"]),
            "costeTotal_eur": round(coste["s"] or 0.0, 6),
            "duracionPasada_s": _duracion(pasada)}


def _duracion(pasada: sqlite3.Row | None) -> float:
    if not pasada or not pasada["fin"]:
        return 0.0
    try:
        a = datetime.fromisoformat(pasada["inicio"])
        b = datetime.fromisoformat(pasada["fin"])
        return round((b - a).total_seconds(), 2)
    except ValueError:
        return 0.0


# --------------------------------------------------------------- baldosas
def _filtrar(sql: str, f: dict) -> tuple[str, dict]:
    args = {"norma": f.get("norma"), "lote": f.get("lote", "lote1")}
    if f.get("result"):
        sql += " AND d.result = :result"
        args["result"] = f["result"]
    if f.get("q"):
        sql += " AND (doc.file_id LIKE :q OR d.motivo LIKE :q)"
        args["q"] = f"%{f['q']}%"
    return sql, args


def baldosas(con: sqlite3.Connection, f: dict | None = None) -> list[dict]:
    f = dict(f or {})
    f["norma"] = _norma(con, f.get("norma"))
    sql, args = _filtrar(
        "SELECT doc.file_id, d.result, d.motivo FROM decisiones d"
        " JOIN documentos doc USING(doc_id)"
        " WHERE d.norma_version = :norma AND doc.lote = :lote", f)
    return [dict(r) for r in con.execute(sql + " ORDER BY doc.file_id", args)]


# --------------------------------------------------------------- facturas
def facturas(con: sqlite3.Connection, f: dict | None = None) -> list[dict]:
    f = dict(f or {})
    f["norma"] = _norma(con, f.get("norma"))
    sql, args = _filtrar(
        "SELECT doc.file_id, doc.doc_id, doc.lote, doc.estado, doc.tiene_texto,"
        "       doc.intentos, d.result, d.motivo, d.creado_at,"
        "       x.via, x.campos_json, x.coste_eur, x.latencia_ms"
        "  FROM decisiones d JOIN documentos doc USING(doc_id)"
        "  LEFT JOIN extraccion_vigente x ON x.doc_id = d.doc_id"
        " WHERE d.norma_version = :norma AND doc.lote = :lote", f)
    resueltos = {r["doc_id"] for r in con.execute("SELECT doc_id FROM resoluciones")}
    prov = _proveedores(con)
    salida = []
    for r in con.execute(sql + " ORDER BY doc.file_id", args):
        c = json.loads(r["campos_json"] or "{}")
        p = prov.get(c.get("nif_emisor") or "")
        salida.append({
            "file_id": r["file_id"], "doc_id": r["doc_id"], "lote": r["lote"],
            "etapa": "resuelta" if r["doc_id"] in resueltos
                     else ETAPA.get(r["estado"], "ingerida"),
            "tiene_texto": bool(r["tiene_texto"]), "via": r["via"],
            "proveedor": p["razon_social"] if p else None,
            "nif": c.get("nif_emisor"), "pedido": c.get("pedido"),
            "total_cent": cent(c.get("total")), "result": r["result"],
            "motivo": r["motivo"], "latencia_ms": r["latencia_ms"] or 0,
            "coste_eur": _eur(r["coste_eur"]), "intentos": r["intentos"] or 0,
            "decidida_at": r["creado_at"]})
    return salida


def _proveedores(con: sqlite3.Connection) -> dict[str, dict]:
    fila = con.execute("SELECT version_id FROM maestro_versiones"
                       " ORDER BY creado_at DESC LIMIT 1").fetchone()
    if not fila:
        return {}
    return {r["nif"]: dict(r) for r in con.execute(
        "SELECT nif, proveedor_id, razon_social, iban, condiciones_dias"
        " FROM proveedores WHERE version_id=?", (fila["version_id"],))}


# ---------------------------------------------------------------- eventos
def eventos(con: sqlite3.Connection, f: dict | None = None) -> dict:
    f = f or {}
    sql = ("SELECT e.id, e.doc_id, e.etapa, e.nivel, e.mensaje, e.at,"
           "       doc.file_id FROM eventos e"
           "  LEFT JOIN documentos doc USING(doc_id) WHERE 1=1")
    args: dict[str, Any] = {}
    for campo in ("etapa", "nivel"):
        if f.get(campo):
            sql += f" AND e.{campo} = :{campo}"
            args[campo] = f[campo]
    if f.get("q"):
        sql += " AND (e.mensaje LIKE :q OR doc.file_id LIKE :q)"
        args["q"] = f"%{f['q']}%"
    total = con.execute(f"SELECT count(*) n FROM ({sql})", args).fetchone()["n"]
    sql += " ORDER BY e.id DESC LIMIT :limit"
    args["limit"] = int(f.get("limit") or 200)
    return {"filas": [dict(r) for r in con.execute(sql, args)], "total": total}


def motivos(con: sqlite3.Connection, norma: str | None = None,
            *, lote: str = "lote1") -> list[dict]:
    n = _norma(con, norma)
    return [dict(r) for r in con.execute(
        "SELECT d.motivo, d.result, count(*) n FROM decisiones d"
        " JOIN documentos doc USING(doc_id)"
        " WHERE d.norma_version=? AND doc.lote=? AND d.result <> 'PAGAR'"
        " GROUP BY d.motivo, d.result ORDER BY n DESC", (n, lote))]


# ------------------------------------------------------------- expediente
def _documento(r: sqlite3.Row) -> dict:
    return {"doc_id": r["doc_id"], "file_id": r["file_id"], "ruta": r["ruta"],
            "bytes": r["bytes"], "tiene_texto": bool(r["tiene_texto"]),
            "lote": r["lote"], "estado": r["estado"],
            "intentos": r["intentos"] or 0, "ultimo_error": r["ultimo_error"],
            "creado_at": r["creado_at"]}


def _extraccion(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    c = _campos(r)
    faltan = r["campos_faltantes"] or ""
    return {"doc_id": r["doc_id"], "intento": r["intento"],
            "plantilla": r["plantilla"], "via": r["via"],
            "campos": _campos_factura(c),
            "campos_faltantes": [x for x in faltan.split(",") if x],
            "cuadra_interna": None if r["cuadra_interna"] is None
                              else bool(r["cuadra_interna"]),
            "coste_eur": _eur(r["coste_eur"]), "latencia_ms": r["latencia_ms"] or 0,
            "creado_at": r["creado_at"]}


def _decision(r: sqlite3.Row, descripciones: dict[str, str]) -> dict:
    reglas = []
    for v in json.loads(r["reglas_json"] or "[]"):
        ev = {k: x for k, x in (v.get("evidencia") or {}).items()}
        reglas.append({"regla": v.get("id"),
                       "descripcion": descripciones.get(v.get("id"), ""),
                       "veredicto": VEREDICTO.get(v.get("veredicto"), "SIN_DATOS"),
                       "evidencia": {k: (str(x) if x is not None else None)
                                     for k, x in ev.items()}})
    return {"doc_id": r["doc_id"], "norma_version": r["norma_version"],
            "snapshot_erp": r["snapshot_erp"],
            "snapshot_maestro": r["snapshot_maestro"], "result": r["result"],
            "motivo": r["motivo"], "reglas": reglas,
            "coste_eur": _eur(r["coste_eur"]),
            "latencia_ms": r["latencia_ms"] or 0, "creado_at": r["creado_at"]}


def _descripciones(norma: str) -> dict[str, str]:
    from alberto.procedencia import version_base
    from alberto.reglas.motor import cargar_norma
    try:
        datos = cargar_norma(version_base(norma))
    except FileNotFoundError:
        return {}
    return {r["id"]: r.get("descripcion", "") for r in datos.get("reglas", [])}


def expediente(con: sqlite3.Connection, file_id: str,
               norma: str | None = None) -> dict | None:
    n = _norma(con, norma)
    doc = con.execute("SELECT * FROM documentos WHERE file_id=?",
                      (file_id,)).fetchone()
    if doc is None:
        return None
    dec = decision_de(con, doc["doc_id"], n)
    if dec is None:
        return None

    desc = _descripciones(n)
    ext = con.execute("SELECT * FROM extraccion_vigente WHERE doc_id=?",
                      (doc["doc_id"],)).fetchone()
    c = _campos(ext)
    otras = [_decision(r, desc) for r in con.execute(
        "SELECT * FROM decisiones WHERE doc_id=? AND NOT (norma_version=? AND"
        " snapshot_erp=? AND snapshot_maestro=?) ORDER BY creado_at DESC",
        (doc["doc_id"], dec["norma_version"], dec["snapshot_erp"],
         dec["snapshot_maestro"]))]

    prov = _proveedores(con).get(c.get("nif_emisor") or "")
    asi = con.execute(
        "SELECT * FROM asientos WHERE snapshot_id=? AND pedido=?",
        (dec["snapshot_erp"], c.get("pedido") or "")).fetchone()
    notas = [dict(r) | {"id": r["rowid"]} for r in con.execute(
        "SELECT rowid, ambito, clave, texto, origen, creado_at FROM notas"
        " WHERE version_id=? AND ((ambito='pedido' AND clave=?)"
        "   OR (ambito='proveedor' AND clave=?) OR ambito='regla')",
        (dec["snapshot_maestro"], c.get("pedido") or "",
         (prov or {}).get("proveedor_id") or ""))]
    res = con.execute("SELECT * FROM resoluciones WHERE doc_id=?",
                      (doc["doc_id"],)).fetchone()
    vecinos = _vecinos(con, file_id, doc["lote"])

    return {
        "documento": _documento(doc), "extraccion": _extraccion(ext),
        "decision": _decision(dec, desc), "otrasDecisiones": otras,
        "eventos": [dict(r) for r in con.execute(
            "SELECT id, doc_id, etapa, nivel, mensaje, at FROM eventos"
            " WHERE doc_id=? ORDER BY id DESC LIMIT 50", (doc["doc_id"],))],
        "notas": notas,
        "proveedor": prov,
        "asiento": {"asiento_id": asi["asiento_id"], "pedido": asi["pedido"],
                    "nif": asi["nif"], "proveedor_id": asi["proveedor_id"],
                    "importe_esperado_cent": cent(asi["importe_esperado"]),
                    "estado": asi["estado"],
                    "fecha_registro": asi["fecha_registro"]} if asi else None,
        "resolucion": dict(res) if res else None,
        "prev": vecinos[0], "next": vecinos[1]}


def _vecinos(con: sqlite3.Connection, file_id: str,
             lote: str) -> tuple[str | None, str | None]:
    prev = con.execute("SELECT file_id FROM documentos WHERE lote=? AND file_id<?"
                       " ORDER BY file_id DESC LIMIT 1", (lote, file_id)).fetchone()
    sig = con.execute("SELECT file_id FROM documentos WHERE lote=? AND file_id>?"
                      " ORDER BY file_id LIMIT 1", (lote, file_id)).fetchone()
    return (prev["file_id"] if prev else None, sig["file_id"] if sig else None)


# ---------------------------------------------------------------- bandeja
def _categoria(motivo: str, faltan: str) -> str:
    """Por que escalo, para agrupar la bandeja.

    Manda el MOTIVO, que es lo que el motor dijo de verdad; la columna
    `campos_faltantes` es solo el respaldo. El orden importa: un documento
    ilegible tambien tiene campos faltantes, y lo util es saber que no se
    pudo leer, no que falte el IBAN.
    """
    m = (motivo or "").lower()
    if "no se pudieron extraer" in m:
        return "ilegible"
    if "marcado para revision" in m:
        return "revisar"
    if "faltan datos" in m or faltan:
        return "campo_faltante"
    return "importe"


def bandeja(con: sqlite3.Connection, norma: str | None = None,
            *, lote: str = "lote1") -> list[dict]:
    n = _norma(con, norma)
    prov = _proveedores(con)
    salida = []
    for r in con.execute(
            "SELECT doc.file_id, doc.doc_id, d.motivo, d.snapshot_erp,"
            "       x.campos_json, x.campos_faltantes"
            "  FROM decisiones d JOIN documentos doc USING(doc_id)"
            "  LEFT JOIN extraccion_vigente x ON x.doc_id = d.doc_id"
            " WHERE d.norma_version=? AND doc.lote=? AND d.result='ESCALAR'"
            " ORDER BY doc.file_id", (n, lote)):
        c = json.loads(r["campos_json"] or "{}")
        p = prov.get(c.get("nif_emisor") or "")
        asi = con.execute(
            "SELECT importe_esperado FROM asientos WHERE snapshot_id=? AND pedido=?",
            (r["snapshot_erp"], c.get("pedido") or "")).fetchone()
        res = con.execute("SELECT * FROM resoluciones WHERE doc_id=?",
                          (r["doc_id"],)).fetchone()
        salida.append({
            "file_id": r["file_id"], "doc_id": r["doc_id"],
            "motivo": r["motivo"] or "",
            "categoria": _categoria(r["motivo"], r["campos_faltantes"] or ""),
            "proveedor": p, "pedido": c.get("pedido"),
            "total_cent": cent(c.get("total")),
            "importe_erp_cent": cent(asi["importe_esperado"]) if asi else None,
            # Hay a quien escribir solo si sabemos quien es el proveedor.
            "email_posible": bool(p),
            "resolucion": dict(res) if res else None})
    return salida


# ------------------------------------------------------------------ coste
def coste(con: sqlite3.Connection, *, lote: str = "lote1") -> dict:
    rutas = []
    total_docs = total_eur = 0.0
    for via in ("determinista", "vision"):
        filas = con.execute(
            "SELECT x.coste_eur, x.latencia_ms FROM extracciones x"
            "  JOIN documentos d USING(doc_id)"
            " WHERE d.lote=? AND x.via=? AND x.aceptada=1", (lote, via)).fetchall()
        if not filas:
            continue
        lat = [f["latencia_ms"] or 0 for f in filas]
        eur = sum(_eur(f["coste_eur"]) for f in filas)
        rutas.append({"via": via, "docs": len(filas),
                      "coste_total_eur": round(eur, 6),
                      "coste_por_doc_eur": round(eur / len(filas), 6),
                      "latencia_p50_ms": _percentil(lat, 0.50),
                      "latencia_p95_ms": _percentil(lat, 0.95)})
        total_docs += len(filas)
        total_eur += eur

    ms = con.execute(
        "SELECT sum(x.latencia_ms) s FROM extracciones x JOIN documentos d"
        " USING(doc_id) WHERE d.lote=? AND x.aceptada=1", (lote,)).fetchone()["s"]
    por_doc = total_eur / total_docs if total_docs else 0.0
    return {
        "rutas": rutas,
        "coste_por_doc_eur": round(por_doc, 6),
        "docs_por_segundo": round(total_docs / (ms / 1000), 1) if ms else 0.0,
        "proyeccion": [{"docs_mes": m, "coste_eur": round(por_doc * m, 2)}
                       for m in (10_000, 100_000)],
        "punto_cruce_ocr": _punto_cruce(rutas),
    }


def _punto_cruce(rutas: list[dict]) -> str:
    vis = next((r for r in rutas if r["via"] == "vision"), None)
    if not vis or not vis["coste_por_doc_eur"]:
        return ("La via determinista cuesta 0 EUR, asi que no hay punto de "
                "cruce que calcular todavia: solo lo habra cuando la vision "
                "haya corrido de verdad contra el proveedor.")
    return (f"Cada documento por vision cuesta {vis['coste_por_doc_eur']:.4f} EUR. "
            f"Un OCR local se amortiza cuando el volumen mensual por esa via "
            f"supera lo que cuesta mantenerlo.")


# ------------------------------------------------------------ diff normas
def diff_normas(con: sqlite3.Connection, de: str, a: str) -> list[dict]:
    """Que decisiones cambian entre dos normas, y por que.

    Esto solo es posible porque `norma_version` lleva la huella del
    contenido: antes, editar la politica sobrescribia la decision anterior
    y no habia nada con lo que comparar.
    """
    return [dict(r) for r in con.execute(
        "SELECT doc.file_id, x.result AS de, y.result AS a, y.motivo"
        "  FROM decisiones x JOIN decisiones y USING(doc_id)"
        "  JOIN documentos doc USING(doc_id)"
        " WHERE x.norma_version=? AND y.norma_version=? AND x.result<>y.result"
        " ORDER BY doc.file_id", (de, a))]


# ----------------------------------------------------------------- partes
def partes(con: sqlite3.Connection) -> list[dict]:
    """El parte de trabajo: una fila por pasada. Sale de `pasadas`."""
    salida = []
    for p in con.execute("SELECT * FROM pasadas WHERE verbo='decide'"
                         " ORDER BY inicio DESC LIMIT 50"):
        r = json.loads(p["resultado_json"] or "{}")
        args = json.loads(p["argumentos_json"] or "{}")
        salida.append({
            "pasada": p["pasada_id"], "inicio": p["inicio"],
            "duracion_s": _duracion(p),
            "docs": sum(r.get(k, 0) for k in ("PAGAR", "ESCALAR", "NO_PAGAR")),
            "coste_eur": 0.0,
            "pagar": r.get("PAGAR", 0), "escalar": r.get("ESCALAR", 0),
            "no_pagar": r.get("NO_PAGAR", 0),
            "norma": r.get("norma") or args.get("norma") or "",
            "lote": args.get("lote", "lote1")})
    return salida


# ------------------------------------------------------------------ salud
def salud(con: sqlite3.Connection, *, lote: str = "lote1") -> dict:
    erp = con.execute("SELECT * FROM snapshots_erp ORDER BY creado_at DESC"
                      " LIMIT 1").fetchone()
    pendientes = con.execute(
        "SELECT count(*) n FROM documentos d"
        " WHERE d.lote=? AND NOT EXISTS (SELECT 1 FROM decisiones x"
        "   WHERE x.doc_id=d.doc_id)", (lote,)).fetchone()["n"]
    reintentos = con.execute(
        "SELECT coalesce(sum(intentos),0) n FROM documentos WHERE lote=?",
        (lote,)).fetchone()["n"]
    desde = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    errores = con.execute(
        "SELECT count(*) n FROM eventos WHERE nivel IN ('warn','error') AND at>=?",
        (desde,)).fetchone()["n"]
    # Hay LLM configurado? No se llama a nadie para averiguarlo.
    import os
    llm_ok = bool(os.environ.get("HELMCODE_BASE_URL")
                  and os.environ.get("HELMCODE_API_KEY"))
    return {
        "erp": {"ok": bool(erp and erp["completo"]),
                "snapshot": erp["snapshot_id"] if erp else "",
                "asientos": erp["n_asientos"] if erp else 0},
        "llm": {"ok": llm_ok, "modo": "normal" if llm_ok else "degradado"},
        "pendientes": pendientes, "reintentos": reintentos,
        "errores24h": errores,
    }


def instantanea(con: sqlite3.Connection, *, lote: str = "lote1") -> dict:
    """Todo lo que la web necesita, en un JSON. Es lo que se despliega en
    Vercel, donde no hay backend vivo: la foto de la ultima pasada real."""
    ns = normas(con)
    activa = norma_activa(con)
    return {
        "normas": ns, "normaActiva": activa,
        "kpis": {n: kpis(con, n, lote=lote) for n in ns},
        "baldosas": {n: baldosas(con, {"norma": n, "lote": lote}) for n in ns},
        "facturas": {n: facturas(con, {"norma": n, "lote": lote}) for n in ns},
        "motivos": {n: motivos(con, n, lote=lote) for n in ns},
        "eventos": eventos(con, {"limit": 500}),
        "bandeja": bandeja(con, activa, lote=lote),
        "coste": coste(con, lote=lote),
        "partes": partes(con),
        "salud": salud(con, lote=lote),
        "expedientes": {b["file_id"]: expediente(con, b["file_id"], activa)
                        for b in baldosas(con, {"norma": activa, "lote": lote})},
    }
