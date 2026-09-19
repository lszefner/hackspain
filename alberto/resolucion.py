"""Que decision vale para un documento. UN solo sitio.

Existe porque `emite` y `explica` resolvian el mismo empate de dos formas
distintas: el entregable se quedaba con la primera fila que devolviera
SQLite y la traza con la mas reciente. Podian ensenar cosas distintas de la
misma factura, y nadie se habria enterado hasta la defensa.

La clave de `decisiones` es (doc_id, norma_version, snapshot_erp,
snapshot_maestro). Emparejar solo por norma_version, como hacia `salida`,
devuelve N filas en cuanto hay un segundo snapshot -- que es exactamente lo
que pasa el domingo cuando Alberto cambia un dato de la Caja.
"""
from __future__ import annotations

import sqlite3

# `norma` admite dos formas:
#   'v3'         -> la FAMILIA v3: cualquier huella, la mas reciente gana
#   'v3@7f3a1c'  -> esa norma exacta, para fijar una auditoria
#
# Una fila por documento. El empate se rompe por
# (creado_at, snapshot_erp, snapshot_maestro), nunca por el plan de consulta.
FILTRO = "(x.norma_version = :norma OR x.norma_version LIKE :familia)"
FILTRO_Y = "(y.norma_version = :norma OR y.norma_version LIKE :familia)"

VIGENTE = f"""
SELECT x.* FROM decisiones x
 WHERE {FILTRO}
   AND x.creado_at || x.snapshot_erp || x.snapshot_maestro = (
       SELECT MAX(y.creado_at || y.snapshot_erp || y.snapshot_maestro)
         FROM decisiones y
        WHERE y.doc_id = x.doc_id AND {FILTRO_Y})
"""


def _args(norma: str) -> dict:
    """Si piden la familia, el LIKE la encuentra; si piden una huella
    concreta, el LIKE no casa con nada y manda la igualdad."""
    familia = f"{norma}@%" if "@" not in norma else "\x00"
    return {"norma": norma, "familia": familia}


class Ambigua(RuntimeError):
    """Un documento resuelve a mas de una decision. No se adivina."""


def decision_de(con: sqlite3.Connection, doc_id: str, norma: str) -> sqlite3.Row | None:
    filas = con.execute(
        f"SELECT * FROM ({VIGENTE}) WHERE doc_id = :doc",
        _args(norma) | {"doc": doc_id}).fetchall()
    if len(filas) > 1:
        raise Ambigua(f"{doc_id} resuelve a {len(filas)} decisiones con norma {norma}")
    return filas[0] if filas else None


def decisiones_de_lote(con: sqlite3.Connection, *, lote: str,
                       norma: str) -> list[sqlite3.Row]:
    """Una fila por documento del lote, con la decision que le corresponde.

    Los documentos sin decision salen con `result` a NULL: que falten es
    informacion, no un motivo para omitirlos.
    """
    filas = con.execute(
        f"SELECT d.file_id, d.doc_id, v.result, v.motivo, v.norma_version,"
        f" v.snapshot_erp, v.snapshot_maestro"
        f"  FROM documentos d LEFT JOIN ({VIGENTE}) v USING(doc_id)"
        f" WHERE d.lote = :lote ORDER BY d.file_id",
        _args(norma) | {"lote": lote}).fetchall()

    vistos: dict[str, int] = {}
    for f in filas:
        vistos[f["file_id"]] = vistos.get(f["file_id"], 0) + 1
    duplicados = sorted(k for k, n in vistos.items() if n > 1)
    if duplicados:
        raise Ambigua(
            f"{len(duplicados)} documentos resuelven a mas de una decision con "
            f"norma {norma}: {duplicados[:5]}. "
            f"Desambigua antes de entregar.")
    return filas
