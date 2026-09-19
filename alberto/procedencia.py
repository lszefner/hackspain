"""La identidad de una configuracion es lo que DICE, no como se llama.

Tres agujeros que esto cierra de golpe:

  · `politica.yaml` no estaba en ninguna parte, y es literalmente el fichero
    que convierte un FALLA de R1 en NO_PAGAR en vez de ESCALAR. La mitad del
    "por que" no se guardaba.
  · `norma_version` era la cadena `version:` escrita a mano DENTRO del YAML.
    Editar norma_v3.yaml dejando `version: v3` producia la misma clave con
    reglas distintas. Y editarlo es exactamente lo que se hace el sabado.
  · Como la clave de `decisiones` no distinguia esos dos casos,
    `INSERT OR REPLACE` borraba la decision anterior en silencio.

Con la huella dentro de la etiqueta -- `v3@7f3a1c` -- los tres desaparecen
sin tocar el esquema de `decisiones`.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from alberto.db import ahora

RAIZ_REGLAS = Path(__file__).parent / "reglas"
LONGITUD = 6


def huella(*rutas: Path) -> str:
    """sha256 del contenido concatenado, en el orden dado."""
    h = hashlib.sha256()
    for ruta in rutas:
        h.update(Path(ruta).read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:LONGITUD]


def rutas_norma(version: str = "v3") -> tuple[Path, Path]:
    """Los dos ficheros que deciden. Los DOS forman la identidad: la norma
    dice que se comprueba y la politica que se hace con el veredicto."""
    return RAIZ_REGLAS / f"norma_{version}.yaml", RAIZ_REGLAS / "politica.yaml"


def etiqueta_norma(version: str = "v3") -> str:
    """'v3@7f3a1c'. La etiqueta legible sigue delante para que una consulta
    por `LIKE 'v3@%'` siga encontrando toda la familia."""
    return f"{version}@{huella(*rutas_norma(version))}"


def version_base(etiqueta: str) -> str:
    """'v3@7f3a1c' -> 'v3'. Tolera etiquetas viejas sin huella."""
    return etiqueta.split("@", 1)[0]


def version_codigo() -> str:
    """El commit corto, con marca si el arbol estaba sucio al ejecutar.

    Un resultado producido con cambios sin commitear NO es reproducible, y
    hay que poder decirlo.
    """
    try:
        raiz = Path(__file__).resolve().parents[1]
        commit = subprocess.run(
            ["git", "-C", str(raiz), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5)
        if commit.returncode != 0:
            return "sin-git"
        sucio = subprocess.run(
            ["git", "-C", str(raiz), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5)
        marca = "-sucio" if sucio.stdout.strip() else ""
        return commit.stdout.strip() + marca
    except (OSError, subprocess.SubprocessError):
        return "sin-git"


def archivar_config(con, version: str = "v3") -> dict[str, str]:
    """Guarda el contenido de los dos YAML como artefactos.

    Reutiliza `artefactos`, que ya es un almacen direccionado por contenido:
    no hay que inventar sitio donde guardarlos. Devuelve sus sha256, que es
    lo que permite a `alberto audita` volver a construir el motor exacto.
    """
    from alberto import artefactos as arte
    norma, politica = rutas_norma(version)
    return {
        "norma_sha": arte.guardar(con, tipo="config", datos=norma.read_bytes(),
                                  mime="text/plain", en_disco=False),
        "politica_sha": arte.guardar(con, tipo="config", datos=politica.read_bytes(),
                                     mime="text/plain", en_disco=False),
    }


# --------------------------------------------------------------- la pasada
@dataclass
class Pasada:
    """Una ejecucion: quien, cuando, con que argumentos y que codigo.

    Sin esta entidad ninguna otra senal se puede fechar ni atribuir. `hoy`
    se resuelve UNA vez aqui y se pasa explicito al motor, para que R4 y la
    futura regla de vencimiento dejen de depender del reloj de pared sin
    que quede constancia.
    """
    pasada_id: str
    verbo: str
    hoy: date
    codigo: str
    con: Any = None

    def registrar(self, resultado: dict | None = None, ok: bool = True) -> None:
        if self.con is None:
            return
        self.con.execute(
            "UPDATE pasadas SET fin=?, resultado_json=?, ok=? WHERE pasada_id=?",
            (ahora(), json.dumps(resultado or {}, ensure_ascii=False, default=str),
             int(ok), self.pasada_id))


def abrir_pasada(con, verbo: str, argumentos: dict | None = None, *,
                 hoy: date | None = None) -> Pasada:
    pid = f"r-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}"
    dia = hoy or date.today()
    codigo = version_codigo()
    con.execute(
        "INSERT INTO pasadas (pasada_id, verbo, argumentos_json, codigo, hoy,"
        " inicio) VALUES (?,?,?,?,?,?)",
        (pid, verbo, json.dumps(argumentos or {}, ensure_ascii=False, default=str),
         codigo, dia.isoformat(), ahora()))
    return Pasada(pasada_id=pid, verbo=verbo, hoy=dia, codigo=codigo, con=con)


def guardar_contexto(con, *, doc_id: str, norma_version: str, snapshot_erp: str,
                     snapshot_maestro: str, pasada: Pasada, shas: dict,
                     intento: int | None) -> None:
    """Todo lo que hace falta para volver a tomar esta decision y que salga
    igual, en una tabla APARTE de `decisiones`."""
    con.execute(
        "INSERT OR REPLACE INTO decision_contexto (doc_id, norma_version,"
        " snapshot_erp, snapshot_maestro, pasada_id, hoy, norma_sha,"
        " politica_sha, codigo, intento_extraccion, creado_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (doc_id, norma_version, snapshot_erp, snapshot_maestro, pasada.pasada_id,
         pasada.hoy.isoformat(), shas.get("norma_sha"), shas.get("politica_sha"),
         pasada.codigo, intento, ahora()))
