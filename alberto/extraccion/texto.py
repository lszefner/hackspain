"""Capa de texto del PDF. Se enruta por CONTENIDO, nunca por el nombre:
en la Caja hay ficheros llamados scan_* que si llevan texto."""
from __future__ import annotations

from pathlib import Path

import pdfplumber


def texto_de_pdf(ruta: Path) -> str:
    """El texto del PDF, o "" si no tiene capa de texto.

    Devolver "" ante un PDF que no se deja parsear es DELIBERADO: asi es como
    un escaneo acaba en la fase 2. Pero un fichero que NO EXISTE no es un PDF
    sin texto, es que alguien movio La Caja, y eso tiene que doler: cuando se
    tragaba tambien el FileNotFoundError, mover la carpeta producia 500
    extracciones vacias y ni un solo error en el log.
    """
    if not Path(ruta).is_file():
        raise FileNotFoundError(
            f"no encuentro el PDF en {ruta}. Si has movido La Caja, "
            f"`alberto ingesta` refresca las rutas.")
    try:
        with pdfplumber.open(ruta) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        return ""


def tiene_capa_texto(ruta: Path, minimo: int = 20) -> bool:
    return len(texto_de_pdf(ruta).strip()) >= minimo
