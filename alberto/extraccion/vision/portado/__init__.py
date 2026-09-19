"""Codigo PORTADO desde la rama main, tal cual. No se refactoriza aqui.

  origen:  github.com/lszefner/hackspain, rama main, commit cb24cb2
  ficheros: ingestion/providers/vision.py
            ingestion/providers/deepseek.py
            ingestion/pdf.py
  portado: 2026-09-19

El unico cambio sobre el original es el import de `digest` de esta linea:
alli venia de ingestion.contracts, que arrastra jsonschema y un directorio
de esquemas. Aqui son tres lineas de hashlib y nada mas.

Si hay que corregir algo del lector, se corrige AQUI y se anota en el
CHANGELOG: este directorio es la frontera con el codigo de otro equipo.
"""
from __future__ import annotations

import hashlib


def digest(data: bytes | bytearray | memoryview) -> str:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("digest espera bytes")
    return hashlib.sha256(bytes(data)).hexdigest()
