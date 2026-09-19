"""Carga el Excel de Alberto. 14 hojas, y las hojas basura tambien importan.

Tres trampas confirmadas en los datos:
  · P007 aparece DOS veces en Proveedores -> deduplicar
  · 'Ofimatica Cieza S.L.   ' trae cola de espacios -> strip en todo
  · Importe_Total llega como 9299.620000000001 -> Decimal, cuantizado a 2
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from alberto.contratos import CENTIMO, Proveedor
from alberto.db import ahora, log

HOJA_PROVEEDORES, HOJA_PEDIDOS = "Proveedores", "Pedidos_2026"
HOJA_NOTAS, HOJA_PENDIENTES = "notas_alberto", "pendiente_revisar"


def _s(valor) -> str:
    return "" if valor is None else str(valor).strip()


def _dec(valor) -> Decimal:
    return Decimal(str(valor)).quantize(CENTIMO)


def _dias(condiciones: str) -> int | None:
    trozos = [t for t in _s(condiciones).split() if t.isdigit()]
    return int(trozos[0]) if trozos else None


def cargar_excel(ruta: Path) -> dict:
    wb = load_workbook(ruta, data_only=True, read_only=True)

    proveedores: dict[str, Proveedor] = {}
    duplicados: list[str] = []
    for fila in wb[HOJA_PROVEEDORES].iter_rows(min_row=2, values_only=True):
        if not fila or not _s(fila[0]):
            continue
        pid, razon, nif, iban, _ciudad, cond = (list(fila) + [None] * 6)[:6]
        nif = _s(nif)
        if not nif:
            continue
        if nif in proveedores:
            duplicados.append(nif)            # P007 esta dos veces
            continue
        proveedores[nif] = Proveedor(
            proveedor_id=_s(pid), nif=nif, razon_social=_s(razon),
            iban=_s(iban).replace(" ", ""), condiciones_dias=_dias(cond),
        )

    pedidos: dict[str, dict] = {}
    for fila in wb[HOJA_PEDIDOS].iter_rows(min_row=2, values_only=True):
        if not fila or not _s(fila[0]):
            continue
        ped, pid, nif, importe, estado, fecha = (list(fila) + [None] * 6)[:6]
        pedidos[_s(ped)] = {
            "proveedor_id": _s(pid), "nif": _s(nif),
            "importe": _dec(importe) if importe is not None else None,
            "estado": _s(estado), "fecha": _s(fecha),
        }

    notas: list[tuple[str, str | None, str]] = []
    for fila in wb[HOJA_NOTAS].iter_rows(values_only=True):
        texto = _s(fila[0] if fila else None)
        if texto:
            notas.append(("general", None, texto))
    # La hoja 'pendiente_revisar' es una regla operativa olvidada en un Excel.
    for fila in wb[HOJA_PENDIENTES].iter_rows(values_only=True):
        valor = _s(fila[0] if fila else None)
        if valor.startswith("PO-"):
            notas.append(("pedido", valor, "Marcado para revision en pendiente_revisar"))

    wb.close()
    return {"proveedores": proveedores, "pedidos": pedidos,
            "notas": notas, "duplicados": duplicados}


def guardar(con: sqlite3.Connection, datos: dict, *, origen: str,
            autor: str = "sistema", motivo: str = "carga inicial") -> str:
    vid = f"maestro-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}"
    con.execute(
        "INSERT INTO maestro_versiones (version_id, origen, autor, motivo, creado_at)"
        " VALUES (?,?,?,?,?)", (vid, origen, autor, motivo, ahora()))
    con.executemany(
        "INSERT OR REPLACE INTO proveedores (version_id, nif, proveedor_id,"
        " razon_social, iban, condiciones_dias) VALUES (?,?,?,?,?,?)",
        [(vid, p.nif, p.proveedor_id, p.razon_social, p.iban, p.condiciones_dias)
         for p in datos["proveedores"].values()])
    con.executemany(
        "INSERT OR REPLACE INTO pedidos_excel (version_id, pedido, proveedor_id,"
        " nif, importe, estado, fecha) VALUES (?,?,?,?,?,?,?)",
        [(vid, ped, d["proveedor_id"], d["nif"],
          str(d["importe"]) if d["importe"] is not None else "0",
          d["estado"], d["fecha"]) for ped, d in datos["pedidos"].items()])
    # INSERT OR REPLACE con version en la clave: recargar el maestro ya no
    # duplica las notas. Antes era un INSERT plano y cada `alberto maestro`
    # anadia otras seis.
    con.executemany(
        "INSERT OR REPLACE INTO notas (version_id, ambito, clave, texto,"
        " origen, creado_at) VALUES (?,?,?,?,?,?)",
        [(vid, a, c, t, "excel", ahora()) for a, c, t in datos["notas"]])
    log(con, "maestros", f"version {vid}",
        proveedores=len(datos["proveedores"]), pedidos=len(datos["pedidos"]),
        notas=len(datos["notas"]), nifs_duplicados=datos["duplicados"])
    return vid


def cargar_proveedores(con: sqlite3.Connection, version_id: str) -> dict[str, Proveedor]:
    return {
        f["nif"]: Proveedor(proveedor_id=f["proveedor_id"], nif=f["nif"],
                            razon_social=f["razon_social"], iban=f["iban"],
                            condiciones_dias=f["condiciones_dias"])
        for f in con.execute("SELECT * FROM proveedores WHERE version_id=?", (version_id,))
    }


def cargar_notas(con: sqlite3.Connection, version_id: str, *,
                 ambito: str | None = None) -> list[sqlite3.Row]:
    """Las notas DE ESA VERSION del maestro. Nunca la tabla entera."""
    sql = "SELECT * FROM notas WHERE version_id=?"
    args: list = [version_id]
    if ambito:
        sql += " AND ambito=?"
        args.append(ambito)
    return con.execute(sql + " ORDER BY ambito, clave", args).fetchall()
