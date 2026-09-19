"""Persiste una foto del ERP y la versiona. Toda decision registra con que
snapshot se tomo: sin eso el reproceso no es reproducible."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from alberto.contratos import Asiento
from alberto.db import ahora, log


def nuevo_id(origen: str) -> str:
    return f"erp-{origen}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"


def guardar(con: sqlite3.Connection, asientos: list[Asiento], informe: dict,
            *, origen: str = "bridge") -> str:
    sid = nuevo_id(origen)
    con.execute(
        "INSERT INTO snapshots_erp (snapshot_id, origen, n_asientos,"
        " total_declarado, completo, creado_at) VALUES (?,?,?,?,?,?)",
        (sid, origen, len(asientos), informe.get("total_declarado"),
         int(bool(informe.get("completo"))), ahora()),
    )
    con.executemany(
        "INSERT OR REPLACE INTO asientos (snapshot_id, asiento_id, pedido, nif,"
        " proveedor_id, importe_esperado, estado, fecha_registro)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [(sid, a.asiento_id, a.pedido, a.nif, a.proveedor_id,
          str(a.importe_esperado), a.estado, a.fecha_registro) for a in asientos],
    )
    log(con, "erp", f"snapshot {sid}", nivel="info" if informe.get("completo") else "warn",
        **{k: v for k, v in informe.items() if k != "metricas"})
    return sid


def cargar(con: sqlite3.Connection, snapshot_id: str) -> dict[str, Asiento]:
    from decimal import Decimal
    filas = con.execute(
        "SELECT * FROM asientos WHERE snapshot_id=?", (snapshot_id,)
    ).fetchall()
    return {
        f["pedido"]: Asiento(
            asiento_id=f["asiento_id"], pedido=f["pedido"], nif=f["nif"],
            proveedor_id=f["proveedor_id"],
            importe_esperado=Decimal(f["importe_esperado"]),
            estado=f["estado"], fecha_registro=f["fecha_registro"],
        )
        for f in filas
    }


def ultimo(con: sqlite3.Connection) -> str | None:
    f = con.execute(
        "SELECT snapshot_id FROM snapshots_erp ORDER BY creado_at DESC LIMIT 1"
    ).fetchone()
    return f["snapshot_id"] if f else None
