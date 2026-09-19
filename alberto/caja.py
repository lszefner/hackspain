"""Donde vive La Caja. UN solo sitio.

La Caja ya no esta suelta en la raiz del repo. Hay dos cosas distintas y
conviene no confundirlas:

  caja_de_alberto/vN/   las instantaneas de lo que publica la organizacion.
                        Versionadas, con manifiesto, y NO se escriben nunca.
  caja/                 la copia viva: la que lee el pipeline y en la que la
                        subida web escribe los PDF nuevos. Esta en .gitignore
                        y se siembra con `make caja`.

La separacion existe porque `alberto web` guarda los ficheros subidos dentro
de la carpeta de facturas. Con La Caja y la instantanea en el mismo sitio, la
primera subida invalidaba el MANIFIESTO.sha256 y la captura dejaba de servir
como prueba de con que datos se genero una entrega.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
INSTANTANEAS = RAIZ / "caja_de_alberto"
VIVA = RAIZ / "caja"

_VERSION = re.compile(r"^v(\d+)$")


def instantaneas() -> list[Path]:
    """Las capturas que hay, de la mas vieja a la mas nueva.

    Se ordena por el numero, no por el nombre: alfabeticamente v10 iria antes
    que v2 y `mas_reciente()` devolveria la equivocada en cuanto pasemos de
    nueve capturas.
    """
    if not INSTANTANEAS.is_dir():
        return []
    encontradas = []
    for hijo in INSTANTANEAS.iterdir():
        m = _VERSION.match(hijo.name)
        if m and (hijo / "facturas").is_dir():
            encontradas.append((int(m.group(1)), hijo))
    return [ruta for _, ruta in sorted(encontradas)]


def mas_reciente() -> Path | None:
    """La ultima captura, o None si no hay ninguna."""
    todas = instantaneas()
    return todas[-1] if todas else None


def _corta(ruta: Path) -> Path:
    """Relativa al directorio actual si cuelga de el; si no, absoluta.

    Importa porque esta ruta acaba en `documentos.ruta`, en la BD. Guardar
    '/Users/quien-sea/...' ata la base a una maquina, que es justo lo que se
    quito de los tests. Ejecutando desde la raiz del repo, que es como se
    ejecuta, esto da 'caja/facturas/x.pdf'.
    """
    try:
        return ruta.relative_to(Path.cwd())
    except ValueError:
        return ruta


def resolver() -> Path:
    """Donde buscar La Caja, en orden de precedencia.

      1. ALBERTO_CAJA, si esta puesta. Manda siempre: es como se apunta a un
         lote concreto sin tocar nada.
      2. caja/, la copia viva. Es la normal.
      3. la instantanea mas reciente, para que un clon recien hecho pueda
         ejecutar los comandos de solo lectura sin sembrar nada antes.

    Lo que devuelve puede no existir: quien lo use decide si eso es un error.
    `alberto` avisa con la ruta y el comando para arreglarlo.
    """
    entorno = os.environ.get("ALBERTO_CAJA")
    if entorno:
        return Path(entorno)
    if (VIVA / "facturas").is_dir():
        return _corta(VIVA)
    ultima = mas_reciente()
    return _corta(ultima) if ultima is not None else _corta(VIVA)


def facturas() -> Path:
    """La carpeta de PDF de La Caja resuelta."""
    return resolver() / "facturas"


def excel() -> Path | None:
    """El libro de proveedores y normas, si esta."""
    return next(iter(sorted(resolver().glob("*.xlsx"))), None)
