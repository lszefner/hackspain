"""Capa de texto del PDF. Se enruta por CONTENIDO, nunca por el nombre:
en la Caja hay ficheros llamados scan_* que si llevan texto."""
from __future__ import annotations

from pathlib import Path

import pdfplumber


def texto_de_pdf(ruta: Path) -> str:
    try:
        with pdfplumber.open(ruta) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        return ""


def tiene_capa_texto(ruta: Path, minimo: int = 20) -> bool:
    return len(texto_de_pdf(ruta).strip()) >= minimo
