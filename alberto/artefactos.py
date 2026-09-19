"""La capa `raw`: lo que el sistema VIO, antes de interpretarlo.

Tres capas de procedencia, y esta es la de en medio:

    original    el PDF            -> tabla `documentos` (doc_id = sha256)
    raw         lo que se leyo    -> aqui
    extraction  los campos        -> tabla `extracciones`

Direccionada por CONTENIDO: la clave es el sha256 de los bytes, no del
documento. Re-renderizar el mismo PNG al reanudar no duplica ni una fila ni
un byte, y dos documentos identicos comparten artefacto.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from alberto.db import ahora

DIR_RAW = Path("raw")
INLINE = {"texto_pdf", "llamada", "lectura", "interpretacion"}


def sha256_de(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def guardar(con: sqlite3.Connection, *, tipo: str, datos: bytes,
            mime: str = "application/octet-stream",
            dir_raw: Path | None = None, en_disco: bool | None = None) -> str:
    """Guarda un artefacto y devuelve su sha256.

    Los PNG van a disco (0,5-1 MB cada uno); el texto y el JSON caben
    inline. `INSERT OR IGNORE`: guardar dos veces lo mismo es gratis, que es
    lo que hace la reanudacion idempotente.
    """
    sha = sha256_de(datos)
    if con.execute("SELECT 1 FROM artefactos WHERE sha256=?", (sha,)).fetchone():
        return sha

    a_disco = (tipo not in INLINE) if en_disco is None else en_disco
    contenido: bytes | None = None
    ruta: str | None = None
    if a_disco:
        base = Path(dir_raw or DIR_RAW)
        destino = base / sha[:2] / f"{sha}{_extension(mime)}"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(datos)
        ruta = str(destino)
    else:
        contenido = datos

    con.execute(
        "INSERT OR IGNORE INTO artefactos (sha256, tipo, mime, bytes, contenido,"
        " ruta, creado_at) VALUES (?,?,?,?,?,?,?)",
        (sha, tipo, mime, len(datos), contenido, ruta, ahora()))
    return sha


def _extension(mime: str) -> str:
    return {"image/png": ".png", "text/plain": ".txt",
            "application/json": ".json"}.get(mime, ".bin")


def guardar_json(con: sqlite3.Connection, *, tipo: str, objeto: Any, **kw) -> str:
    datos = json.dumps(objeto, ensure_ascii=False, default=str).encode("utf-8")
    return guardar(con, tipo=tipo, datos=datos, mime="application/json", **kw)


def guardar_texto(con: sqlite3.Connection, *, tipo: str, texto: str, **kw) -> str:
    return guardar(con, tipo=tipo, datos=texto.encode("utf-8"),
                   mime="text/plain", **kw)


def enlazar(con: sqlite3.Connection, doc_id: str, intento: int, rol: str,
            sha256: str, *, orden: int = 0, modelo: str | None = None) -> None:
    con.execute(
        "INSERT OR REPLACE INTO extraccion_artefactos (doc_id, intento, rol,"
        " orden, sha256, modelo, creado_at) VALUES (?,?,?,?,?,?,?)",
        (doc_id, intento, rol, orden, sha256, modelo, ahora()))


def leer(con: sqlite3.Connection, sha256: str) -> bytes:
    fila = con.execute(
        "SELECT contenido, ruta FROM artefactos WHERE sha256=?", (sha256,)).fetchone()
    if fila is None:
        raise KeyError(f"no hay artefacto {sha256}")
    if fila["contenido"] is not None:
        return bytes(fila["contenido"])
    return Path(fila["ruta"]).read_bytes()


def del_documento(con: sqlite3.Connection, doc_id: str,
                  intento: int | None = None) -> list[sqlite3.Row]:
    sql = ("SELECT ea.rol, ea.orden, ea.modelo, a.tipo, a.mime, a.bytes, a.sha256,"
           " a.ruta FROM extraccion_artefactos ea JOIN artefactos a USING(sha256)"
           " WHERE ea.doc_id=?")
    args: list[Any] = [doc_id]
    if intento is not None:
        sql += " AND ea.intento=?"
        args.append(intento)
    return con.execute(sql + " ORDER BY ea.intento, ea.rol, ea.orden", args).fetchall()
