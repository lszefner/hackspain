"""Los cuatro endpoints de la web, servidos desde `alberto.db`.

Este modulo existe para que la demo y el entregable no puedan discrepar.
La version de `main` recalculaba las decisiones con su propio motor
(`rules_ingestion.checks` sobre extracciones de `ingestion.pipeline`),
mientras el JSONL salia de aqui: la misma factura podia aparecer con dos
resultados distintos segun la mirases en pantalla o en el fichero.

Aqui no se decide nada. Se LEE lo ya decidido, por la vista
`extraccion_vigente` y por `decisiones`, que es exactamente lo que se
entrega. El frontend no se entera: mismos endpoints, mismas formas.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from alberto.resolucion import decision_de, decisiones_de_lote

# documentos.estado -> el vocabulario que entiende el frontend
ESTADO = {
    "pendiente": "pendiente",
    "extraido": "procesando",
    "extraido_parcial": "procesando",
    "decidido": "hecha",
    "escalado": "hecha",
}
# VeredictoRegla -> el vocabulario del frontend
VEREDICTO = {"PASA": "PASS", "FALLA": "FAIL", "NA": "NEEDS_REVIEW"}


def _estado(fila: sqlite3.Row) -> str:
    if fila["ultimo_error"]:
        return "error"
    return ESTADO.get(fila["estado"], "pendiente")


def resumen(con: sqlite3.Connection, *, lote: str = "lote1",
            norma: str = "v3") -> dict:
    decisiones = {f["file_id"]: f["result"]
                  for f in decisiones_de_lote(con, lote=lote, norma=norma)}
    docs = con.execute(
        "SELECT file_id, estado, ultimo_error FROM documentos WHERE lote=?"
        " ORDER BY file_id", (lote,)).fetchall()

    conteo = {"pendiente": 0, "procesando": 0, "hecha": 0, "error": 0}
    reparto = {"PAGAR": 0, "ESCALAR": 0, "NO_PAGAR": 0}
    facturas = []
    for d in docs:
        st = _estado(d)
        conteo[st] += 1
        dec = decisiones.get(d["file_id"])
        if dec in reparto:
            reparto[dec] += 1
        facturas.append({"file_id": d["file_id"], "estado": st, "decision": dec})

    return {"total": len(docs), "conteo": conteo, "decisiones": reparto,
            "facturas": facturas, "lote_tamano": len(docs),
            "procesando": False, "error": None}


def factura(con: sqlite3.Connection, file_id: str, *,
            norma: str = "v3") -> dict | None:
    doc = con.execute(
        "SELECT d.*, x.campos_json, x.via, x.campos_faltantes, x.cuadra_interna"
        "  FROM documentos d LEFT JOIN extraccion_vigente x USING(doc_id)"
        " WHERE d.file_id = ?", (file_id,)).fetchone()
    if doc is None:
        return None

    dec = decision_de(con, doc["doc_id"], norma)
    campos = json.loads(doc["campos_json"]) if doc["campos_json"] else {}
    politica = _politica(norma)

    checks: list[dict[str, Any]] = []
    if dec is not None:
        for v in json.loads(dec["reglas_json"] or "[]"):
            ev = v.get("evidencia") or {}
            checks.append({
                "rule_id": v.get("id"),
                "canonical": v.get("id", ""),
                "verdict": VEREDICTO.get(v.get("veredicto"), "NEEDS_REVIEW"),
                "reason": ev.get("motivo") or _evidencia_legible(ev),
                "on_fail": (politica.get("mapeo", {}).get(v.get("id"), {})
                            .get("FALLA")),
            })

    return {
        "file_id": doc["file_id"],
        "estado": _estado(doc),
        "decision": dec["result"] if dec else None,
        "checks": checks,
        "campos": campos,
        "error": doc["ultimo_error"],
        # Extras que el frontend ignora hoy y que estan ahi para la traza.
        "procedencia": _procedencia(con, doc, dec, norma),
    }


def _evidencia_legible(ev: dict) -> str:
    partes = [f"{k}={v}" for k, v in ev.items() if k != "motivo"]
    return " · ".join(partes[:4])


def _politica(norma: str) -> dict:
    from alberto.reglas.motor import cargar_politica
    try:
        return cargar_politica(version=norma)
    except FileNotFoundError:
        return {}


def _procedencia(con: sqlite3.Connection, doc: sqlite3.Row,
                 dec: sqlite3.Row | None, norma: str) -> dict:
    """De donde viene esta decision. Es lo que hace la pantalla auditable."""
    if dec is None:
        return {}
    ctx = con.execute(
        "SELECT * FROM decision_contexto WHERE doc_id=? AND norma_version=?"
        " AND snapshot_erp=? AND snapshot_maestro=?",
        (doc["doc_id"], dec["norma_version"], dec["snapshot_erp"],
         dec["snapshot_maestro"])).fetchone()
    salida = {
        "doc_id": doc["doc_id"],
        "norma": dec["norma_version"],
        "snapshot_erp": dec["snapshot_erp"],
        "snapshot_maestro": dec["snapshot_maestro"],
        "via": doc["via"],
        "motivo": dec["motivo"],
    }
    if ctx:
        salida |= {"hoy": ctx["hoy"], "codigo": ctx["codigo"],
                   "pasada": ctx["pasada_id"],
                   "intento_extraccion": ctx["intento_extraccion"]}
    return salida


def instantanea(con: sqlite3.Connection, *, lote: str = "lote1",
                norma: str = "v3") -> dict:
    """El JSON estatico que consume el frontend cuando no hay backend vivo.

    Es lo que se despliega en Vercel: la foto de la ultima pasada real, no
    una demo inventada.
    """
    res = resumen(con, lote=lote, norma=norma)
    return {"resumen": res,
            "facturas": {f["file_id"]: factura(con, f["file_id"], norma=norma)
                         for f in res["facturas"]}}
