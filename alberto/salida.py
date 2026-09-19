"""Genera el JSONL de entrega. Un objeto por fichero, ni uno mas ni uno menos."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from alberto.contratos import RESULTADOS_VALIDOS, nfc
from alberto.resolucion import Ambigua, decisiones_de_lote


def escribir_jsonl(con: sqlite3.Connection, destino: Path, *, lote: str = "lote1",
                   norma: str = "v3", con_traza: bool = False) -> dict:
    # Una fila por documento, garantizado. Antes esto emparejaba solo por
    # norma_version y con dos snapshots devolvia N filas; ganaba la primera
    # que sacara SQLite y el JSONL podia llevar una decision caducada.
    filas = decisiones_de_lote(con, lote=lote, norma=norma)

    sin_decision = [f["file_id"] for f in filas if f["result"] is None]
    vistos: set[str] = set()
    with destino.open("w", encoding="utf-8") as fh:
        for f in filas:
            fid = nfc(f["file_id"])
            if fid in vistos:
                continue                     # invariante: un outcome por fichero
            vistos.add(fid)
            obj = {"file_id": fid, "result": f["result"] or "ESCALAR"}
            if con_traza and f["motivo"]:
                obj["motivo"] = f["motivo"]
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")

    return {"escritos": len(vistos), "sin_decision": sin_decision, "ruta": str(destino)}


def verificar(ruta: Path, esperados: int | None = None) -> dict:
    """Comprobacion previa a la entrega: JSON valido, NFC, resultados del vocabulario."""
    problemas: list[str] = []
    ids: list[str] = []
    for n, linea in enumerate(ruta.read_text("utf-8").splitlines(), 1):
        if not linea.strip():
            continue
        try:
            obj = json.loads(linea)
        except json.JSONDecodeError as exc:
            problemas.append(f"linea {n}: JSON invalido ({exc})")
            continue
        fid = obj.get("file_id", "")
        if obj.get("result") not in RESULTADOS_VALIDOS:
            problemas.append(f"linea {n}: result {obj.get('result')!r} no es del vocabulario")
        if fid != nfc(fid):
            problemas.append(f"linea {n}: file_id no esta en NFC")
        ids.append(fid)
    duplicados = {x for x in ids if ids.count(x) > 1}
    if duplicados:
        problemas.append(f"file_id duplicados: {sorted(duplicados)[:5]}")
    if esperados is not None and len(ids) != esperados:
        problemas.append(f"hay {len(ids)} lineas y se esperaban {esperados}")
    return {"lineas": len(ids), "ok": not problemas, "problemas": problemas}
