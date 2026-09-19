"""Orquestacion: ingesta -> snapshot -> extraccion -> decision -> JSONL."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from alberto.contratos import FacturaExtraida, nfc
from alberto.db import ahora, log
from alberto.erp import ClienteERP
from alberto.erp import snapshot as snap
from alberto.extraccion import extraer_campos, texto_de_pdf
from alberto.maestros import cargar_excel, cargar_proveedores
from alberto.maestros import guardar as guardar_maestro
from alberto.reglas import Motor, cargar_norma, cargar_politica


def sincronizar_erp(con: sqlite3.Connection, url: str, *, origen: str = "bridge") -> str:
    with ClienteERP(url) as cli:
        asientos, informe = cli.descargar_todo()
    if not informe["completo"]:
        log(con, "erp", "snapshot INCOMPLETO", nivel="warn", **{
            k: v for k, v in informe.items() if k != "metricas"})
    return snap.guardar(con, asientos, informe, origen=origen)


def cargar_maestro(con: sqlite3.Connection, xlsx: Path) -> str:
    return guardar_maestro(con, cargar_excel(xlsx), origen=xlsx.name)


def extraer(con: sqlite3.Connection, *, lote: str = "lote1", forzar: bool = False) -> dict:
    pendientes = con.execute(
        "SELECT * FROM documentos WHERE lote=?" + ("" if forzar else
        " AND doc_id NOT IN (SELECT doc_id FROM extracciones)"), (lote,)).fetchall()
    hechos = sin_texto = 0
    for d in pendientes:
        t0 = time.monotonic()
        if d["tiene_texto"]:
            campos = extraer_campos(texto_de_pdf(Path(d["ruta"])))
            via, plantilla = "determinista", campos.pop("_plantilla", "")
        else:
            # N3 (vision) va aqui. De momento se marca y la politica la escala.
            campos, via, plantilla = {}, "sin_texto", "imagen"
            sin_texto += 1
        f = FacturaExtraida(
            doc_id=d["doc_id"], file_id=d["file_id"], plantilla=plantilla, via=via,
            latencia_ms=int((time.monotonic() - t0) * 1000),
            **{k: v for k, v in campos.items() if not k.startswith("_")})
        faltan = tuple(k for k in ("pedido", "nif_emisor", "iban", "fecha", "total")
                       if getattr(f, k) is None)
        con.execute(
            "INSERT OR REPLACE INTO extracciones (doc_id, intento, plantilla, via,"
            " campos_json, campos_faltantes, cuadra_interna, coste_eur, latencia_ms, creado_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f.doc_id, 1, plantilla, via,
             json.dumps({k: str(v) if v is not None else None
                         for k, v in asdict(f).items()}, ensure_ascii=False),
             ",".join(faltan), None if f.cuadra_interna() is None else int(f.cuadra_interna()),
             str(f.coste_eur), f.latencia_ms, ahora()))
        con.execute("UPDATE documentos SET estado='extraido' WHERE doc_id=?", (d["doc_id"],))
        hechos += 1
    log(con, "extraccion", f"{hechos} documentos", lote=lote, sin_texto=sin_texto)
    return {"extraidos": hechos, "sin_texto": sin_texto}


def _factura_de_fila(fila: sqlite3.Row) -> FacturaExtraida:
    from datetime import date
    c = json.loads(fila["campos_json"])
    def d(k):
        return Decimal(c[k]) if c.get(k) else None
    f = c.get("fecha")
    return FacturaExtraida(
        doc_id=c["doc_id"], file_id=c["file_id"], plantilla=c.get("plantilla") or "",
        via=c.get("via") or "determinista", pedido=c.get("pedido"),
        nif_emisor=c.get("nif_emisor"), iban=c.get("iban"),
        fecha=date.fromisoformat(f) if f else None,
        base=d("base"), iva_pct=d("iva_pct"), iva_importe=d("iva_importe"), total=d("total"),
        coste_eur=Decimal(c.get("coste_eur") or "0"), latencia_ms=int(c.get("latencia_ms") or 0))


def decidir(con: sqlite3.Connection, *, snapshot_erp: str, snapshot_maestro: str,
            norma: str = "v3", lote: str = "lote1") -> dict:
    asientos = snap.cargar(con, snapshot_erp)
    proveedores = cargar_proveedores(con, snapshot_maestro)
    revisar = frozenset(
        r["clave"] for r in con.execute(
            "SELECT clave FROM notas WHERE ambito='pedido' AND clave IS NOT NULL"))
    motor = Motor(cargar_norma(norma), cargar_politica(), proveedores, asientos,
                  revisar=revisar)

    filas = con.execute(
        "SELECT x.* FROM extracciones x JOIN documentos d USING(doc_id)"
        " WHERE d.lote=?", (lote,)).fetchall()
    conteo: dict[str, int] = {}
    for fila in filas:
        f = _factura_de_fila(fila)
        dec = motor.decidir(f, motor.evaluar(f),
                            snapshot_erp=snapshot_erp, snapshot_maestro=snapshot_maestro)
        con.execute(
            "INSERT OR REPLACE INTO decisiones (doc_id, norma_version, snapshot_erp,"
            " snapshot_maestro, result, motivo, reglas_json, coste_eur, latencia_ms, creado_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (dec.doc_id, dec.norma_version, dec.snapshot_erp, dec.snapshot_maestro,
             dec.result, dec.motivo,
             json.dumps([asdict(v) for v in dec.reglas], ensure_ascii=False, default=str),
             str(dec.coste_eur), dec.latencia_ms, ahora()))
        con.execute("UPDATE documentos SET estado=? WHERE doc_id=?",
                    ("escalado" if dec.result == "ESCALAR" else "decidido", dec.doc_id))
        conteo[dec.result] = conteo.get(dec.result, 0) + 1
    log(con, "decision", f"{len(filas)} decisiones", norma=norma, **conteo)
    return conteo
