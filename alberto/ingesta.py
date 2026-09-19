"""Ingesta idempotente: sha256 como clave, NFC en el nombre, estado en SQLite."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from alberto.contratos import Documento, nfc
from alberto.db import ahora, log
from alberto.extraccion import tiene_capa_texto


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as fh:
        for bloque in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def registrar_uno(con: sqlite3.Connection, ruta: Path, *,
                  lote: str = "lote1") -> Documento:
    """UN fichero. Existe para que la subida web registre exactamente el que
    acaba de escribir, sin barrer la carpeta entera ni volver a hashear 500."""
    doc = Documento(
        doc_id=sha256(ruta), file_id=nfc(ruta.name), ruta=ruta,
        bytes=ruta.stat().st_size, tiene_texto=tiene_capa_texto(ruta), lote=lote,
    )
    con.execute(
        "INSERT INTO documentos (doc_id, file_id, ruta, bytes, tiene_texto,"
        " lote, estado, creado_at) VALUES (?,?,?,?,?,?,'pendiente',?)"
        # La ruta SI se refresca; lo demas no. El doc_id es el hash del
        # CONTENIDO, asi que mover La Caja no cambia la identidad del
        # documento pero si donde esta: con DO NOTHING, una reingesta
        # despues de mover la carpeta dejaba las 500 filas apuntando a un
        # sitio que ya no existe, y como texto_de_pdf devolvia "" ante
        # cualquier fallo, salian 500 extracciones vacias sin un solo error.
        # El file_id no se toca: es la identidad de cara al entregable.
        " ON CONFLICT(doc_id) DO UPDATE SET ruta = excluded.ruta",
        (doc.doc_id, doc.file_id, str(ruta), doc.bytes,
         int(doc.tiene_texto), lote, ahora()),
    )
    return doc


def registrar(con: sqlite3.Connection, carpeta: Path, *, lote: str = "lote1") -> list[Documento]:
    docs = [registrar_uno(con, ruta, lote=lote)
            for ruta in sorted(carpeta.glob("*.pdf"))]
    sin_texto = sum(1 for d in docs if not d.tiene_texto)
    log(con, "ingesta", f"{len(docs)} documentos en {lote}",
        con_texto=len(docs) - sin_texto, sin_texto=sin_texto)
    return docs
