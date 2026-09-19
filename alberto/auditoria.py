"""Volver a tomar cada decision desde lo registrado, y comprobar que sale igual.

Esto no es un test: es la diferencia entre decir "tenemos trazabilidad" y
demostrarlo. Para cada decision se reconstruye el motor EXACTO que la tomo
--el contenido de la norma y de la politica archivados como artefactos, el
snapshot del ERP, la version del maestro, las notas de ESA version y el
`hoy` de aquella pasada-- y se compara el resultado.

Si una entrada no se puede recuperar, se dice. Una decision que no se puede
volver a derivar no es auditable, y callarlo seria peor que no tenerla.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

import yaml

from alberto import artefactos as arte
from alberto.erp import snapshot as snap
from alberto.maestros import cargar_proveedores
from alberto.pipeline import _factura_de_fila
from alberto.reglas import Motor


@dataclass(frozen=True, slots=True)
class Resultado:
    doc_id: str
    file_id: str
    norma_version: str
    almacenado: str
    rederivado: str | None
    problema: str = ""

    @property
    def reproducible(self) -> bool:
        return self.problema == "" and self.almacenado == self.rederivado


SQL = """
SELECT d.doc_id, d.norma_version, d.snapshot_erp, d.snapshot_maestro,
       d.result, doc.file_id,
       c.hoy, c.norma_sha, c.politica_sha, c.intento_extraccion, c.pasada_id
  FROM decisiones d
  JOIN documentos doc USING(doc_id)
  LEFT JOIN decision_contexto c
         ON c.doc_id = d.doc_id AND c.norma_version = d.norma_version
        AND c.snapshot_erp = d.snapshot_erp
        AND c.snapshot_maestro = d.snapshot_maestro
 WHERE (:norma IS NULL OR d.norma_version = :norma OR d.norma_version LIKE :fam)
 ORDER BY doc.file_id
"""


def _yaml_de_artefacto(con: sqlite3.Connection, sha: str | None) -> dict | None:
    if not sha:
        return None
    try:
        return yaml.safe_load(arte.leer(con, sha).decode("utf-8"))
    except (KeyError, OSError, yaml.YAMLError):
        return None


def auditar(con: sqlite3.Connection, *, norma: str | None = None,
            limite: int | None = None) -> list[Resultado]:
    fam = f"{norma}@%" if norma and "@" not in norma else "\x00"
    filas = con.execute(SQL, {"norma": norma, "fam": fam}).fetchall()
    if limite:
        filas = filas[:limite]

    cache: dict[tuple, Motor] = {}
    salida: list[Resultado] = []

    for f in filas:
        base = dict(doc_id=f["doc_id"], file_id=f["file_id"],
                    norma_version=f["norma_version"], almacenado=f["result"])

        if f["hoy"] is None:
            salida.append(Resultado(**base, rederivado=None,
                                    problema="sin contexto registrado"))
            continue

        norma_d = _yaml_de_artefacto(con, f["norma_sha"])
        politica_d = _yaml_de_artefacto(con, f["politica_sha"])
        if norma_d is None or politica_d is None:
            salida.append(Resultado(**base, rederivado=None,
                                    problema="no se recupera la configuracion"))
            continue

        clave = (f["norma_version"], f["snapshot_erp"], f["snapshot_maestro"],
                 f["hoy"])
        motor = cache.get(clave)
        if motor is None:
            # La etiqueta se fija a la almacenada: el motor reconstruido debe
            # firmar con la misma identidad, no recalcularla desde el disco.
            norma_d = dict(norma_d, _etiqueta=f["norma_version"])
            revisar = frozenset(
                r["clave"] for r in con.execute(
                    "SELECT clave FROM notas WHERE version_id=? AND ambito='pedido'"
                    " AND clave IS NOT NULL", (f["snapshot_maestro"],)))
            motor = Motor(norma_d, politica_d,
                          cargar_proveedores(con, f["snapshot_maestro"]),
                          snap.cargar(con, f["snapshot_erp"]),
                          revisar=revisar, hoy=date.fromisoformat(f["hoy"]))
            cache[clave] = motor

        intento = f["intento_extraccion"]
        ext = con.execute(
            "SELECT * FROM extracciones WHERE doc_id=? AND intento=?",
            (f["doc_id"], intento)).fetchone() if intento else None
        if ext is None:
            salida.append(Resultado(**base, rederivado=None,
                                    problema=f"no se recupera la extraccion {intento}"))
            continue

        factura = _factura_de_fila(ext)
        dec = motor.decidir(factura, motor.evaluar(factura),
                            snapshot_erp=f["snapshot_erp"],
                            snapshot_maestro=f["snapshot_maestro"])
        salida.append(Resultado(**base, rederivado=dec.result))

    return salida


V, R, A, G, F = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"


def informe(con: sqlite3.Connection, *, norma: str | None = None,
            limite: int | None = None) -> int:
    res = auditar(con, norma=norma, limite=limite)
    if not res:
        print(f"\n  {A}no hay decisiones que auditar{F}\n")
        return 1

    ok = [r for r in res if r.reproducible]
    divergentes = [r for r in res if not r.reproducible and not r.problema]
    rotos = [r for r in res if r.problema]
    normas = sorted({r.norma_version for r in res})

    print(f"\n  AUDITORIA DE REPRODUCIBILIDAD{G}   ·   "
          f"{', '.join(normas)}{F}\n")
    print(f"  decisiones                 {len(res):>4}")
    print(f"  reproducidas               {V if not divergentes and not rotos else ''}"
          f"{len(ok):>4}{F}  {G}vueltas a derivar desde lo registrado{F}")
    print(f"  divergentes                {R if divergentes else ''}{len(divergentes):>4}{F}")
    print(f"  entradas no recuperables   {A if rotos else ''}{len(rotos):>4}{F}")

    for r in divergentes[:10]:
        print(f"     {R}{r.file_id:<34}{F} guardado {r.almacenado} "
              f"· rederivado {r.rederivado}")
    motivos: dict[str, int] = {}
    for r in rotos:
        motivos[r.problema] = motivos.get(r.problema, 0) + 1
    for motivo, n in sorted(motivos.items(), key=lambda x: -x[1]):
        print(f"     {A}{n:>4}{F} {G}{motivo}{F}")

    entero = not divergentes and not rotos
    color = V if entero else (A if not divergentes else R)
    print(f"\n  VEREDICTO   {color}"
          f"{len(ok)}/{len(res)} decisiones se pueden volver a derivar{F}\n")
    return 0 if entero else 1
