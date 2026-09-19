"""Fase final: ¿esta el extractor listo para que las reglas corran encima?

No hay verdad de referencia -- la organizacion no publica los resultados
esperados -- asi que esto NO mide exactitud. Mide tres cosas que si son
comprobables:

  1. COBERTURA   cuantos documentos tienen cada campo que exigen las reglas
  2. NO REGRESION ningun documento pierde un campo que antes tenia
  3. COHERENCIA  la identidad contable base + IVA == total

La consulta de `contrato()` es la frontera con la capa de reglas: una fila
por documento, con `via` y la procedencia campo a campo.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml

from alberto.extraccion.cascada import CAMPOS_EXIGIDOS
from alberto.reglas.motor import RAIZ as RAIZ_REGLAS

RUTA_ORO = Path("tests/oro_cobertura.json")

# --- la consulta que lee la capa de reglas. Es EL contrato. ----------------
CONTRATO = """
SELECT d.file_id, d.doc_id, d.lote,
       v.campos_json, v.via, v.campos_faltantes, v.cuadra_interna,
       v.coste_eur, v.intento
  FROM extraccion_vigente v JOIN documentos d USING(doc_id)
 WHERE d.lote = ?
 ORDER BY d.file_id
"""


def contrato(con: sqlite3.Connection, *, lote: str = "lote1") -> list[sqlite3.Row]:
    """Una fila por documento. Lo que consume la capa de reglas."""
    return con.execute(CONTRATO, (lote,)).fetchall()


def campos_por_regla(norma: str = "v3") -> dict[str, list[str]]:
    """Que regla exige cada campo, leido de la propia norma."""
    datos = yaml.safe_load((RAIZ_REGLAS / f"norma_{norma}.yaml").read_text("utf-8"))
    salida: dict[str, list[str]] = {}
    for regla in datos["reglas"]:
        for campo in regla.get("requiere", []):
            salida.setdefault(campo, []).append(regla["id"])
    return salida


def _presentes(fila: sqlite3.Row) -> dict[str, bool]:
    campos = json.loads(fila["campos_json"])
    return {c: campos.get(c) not in (None, "None", "")
            for c in sorted(set(CAMPOS_EXIGIDOS) | {"base", "iva_importe", "iva_pct"})}


def medir(con: sqlite3.Connection, *, lote: str = "lote1",
          norma: str = "v3") -> dict:
    filas = contrato(con, lote=lote)
    exigidos = sorted({c for c in campos_por_regla(norma)})
    cobertura = {c: 0 for c in exigidos}
    vias: dict[str, int] = {}
    coherencia = {"cuadra": 0, "no_cuadra": 0, "sin_datos": 0}
    listos = 0
    incompletos: list[tuple[str, str]] = []

    for f in filas:
        pres = _presentes(f)
        for c in exigidos:
            cobertura[c] += int(pres.get(c, False))
        vias[f["via"] or "?"] = vias.get(f["via"] or "?", 0) + 1
        cuadra = f["cuadra_interna"]
        coherencia["cuadra" if cuadra == 1 else
                   "no_cuadra" if cuadra == 0 else "sin_datos"] += 1
        faltan = [c for c in exigidos if not pres.get(c, False)]
        if faltan:
            incompletos.append((f["file_id"], ",".join(faltan)))
        else:
            listos += 1

    return {"total": len(filas), "listos": listos, "cobertura": cobertura,
            "vias": vias, "coherencia": coherencia, "incompletos": incompletos,
            "reglas_por_campo": campos_por_regla(norma)}


# --- no regresion ---------------------------------------------------------
def instantanea(con: sqlite3.Connection, *, lote: str = "lote1") -> dict[str, dict]:
    """{file_id: {campos, valores}}. Es el fichero de oro.

    `valores` es un sha256 corto del contenido de los campos, no solo de sus
    nombres. Sin el, un cambio que convirtiera todos los totales en 1,00
    dejaba el oro en verde: `total` seguia "presente".
    """
    import hashlib
    salida: dict[str, dict] = {}
    for f in contrato(con, lote=lote):
        campos = json.loads(f["campos_json"])
        presentes = sorted(c for c, ok in _presentes(f).items() if ok)
        crudo = "|".join(f"{c}={campos.get(c)}" for c in presentes)
        salida[f["file_id"]] = {
            "campos": presentes,
            "valores": hashlib.sha256(crudo.encode("utf-8")).hexdigest()[:12],
        }
    return salida


def congelar(con: sqlite3.Connection, ruta: Path = RUTA_ORO, *,
             lote: str = "lote1") -> int:
    datos = instantanea(con, lote=lote)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=1, sort_keys=True),
                    encoding="utf-8")
    return len(datos)


def regresiones(con: sqlite3.Connection, ruta: Path = RUTA_ORO, *,
                lote: str = "lote1") -> list[str]:
    """Documentos que PERDIERON un campo respecto al oro. Ganar campos no es
    regresion: es justo lo que la fase 2 viene a hacer."""
    if not ruta.exists():
        return []
    oro = json.loads(ruta.read_text("utf-8"))
    ahora_ = instantanea(con, lote=lote)
    fallos = []
    for file_id, antes in oro.items():
        actual = ahora_.get(file_id) or {"campos": [], "valores": ""}
        # formato antiguo: una lista de campos, sin hash de valores
        campos_antes = antes["campos"] if isinstance(antes, dict) else antes
        perdidos = sorted(set(campos_antes) - set(actual["campos"]))
        if perdidos:
            fallos.append(f"{file_id}: perdio {', '.join(perdidos)}")
        elif isinstance(antes, dict) and antes.get("valores") \
                and antes["valores"] != actual["valores"] \
                and set(campos_antes) == set(actual["campos"]):
            fallos.append(f"{file_id}: mismos campos, VALORES distintos")
    return fallos


# --- salida legible -------------------------------------------------------
V, R, A, G, F = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"


def informe(con: sqlite3.Connection, *, lote: str = "lote1",
            norma: str = "v3") -> int:
    m = medir(con, lote=lote, norma=norma)
    n = m["total"]
    print(f"\n  CONTRATO DE CAMPOS{G}  ·  norma {norma} · lote {lote}{F}\n")
    print(f"  {'campo':<14}{'presente':>9}{'ausente':>9}{'%':>8}   reglas")
    for campo, tiene in m["cobertura"].items():
        falta = n - tiene
        color = V if falta == 0 else (A if falta <= n * 0.02 else R)
        reglas = " ".join(m["reglas_por_campo"].get(campo, []))
        print(f"  {campo:<14}{color}{tiene:>9}{F}{falta:>9}{100*tiene/n:>7.1f}%   {G}{reglas}{F}")

    print(f"\n  PROCEDENCIA   " + " · ".join(
        f"{k} {v}" for k, v in sorted(m["vias"].items())))
    c = m["coherencia"]
    print(f"  COHERENCIA    cuadra {c['cuadra']} · "
          f"no cuadra {c['no_cuadra']} {G}(hallazgos, no errores){F} · "
          f"sin datos {c['sin_datos']}")

    fallos = regresiones(con, lote=lote)
    if not RUTA_ORO.exists():
        print(f"  NO REGRESION  {G}sin fichero de oro; congelar con "
              f"`alberto valida --congelar`{F}")
    elif fallos:
        print(f"  NO REGRESION  {R}{len(fallos)} documentos perdieron campos{F}")
        for linea in fallos[:10]:
            print(f"                {R}{linea}{F}")
    else:
        print(f"  NO REGRESION  {V}ningun documento perdio un campo{F}")

    listo = m["listos"] == n and not fallos
    color = V if listo else A
    print(f"\n  VEREDICTO     {color}{m['listos']}/{n} documentos listos "
          f"para reglas{F}")
    if m["incompletos"]:
        print(f"  {G}sin campos completos:{F}")
        for file_id, faltan in m["incompletos"][:10]:
            print(f"     {A}{file_id:<34}{F} {G}falta {faltan}{F}")
        if len(m["incompletos"]) > 10:
            print(f"     {G}… y {len(m['incompletos']) - 10} mas{F}")
    print()
    return 0 if listo else 1


# --- coste: una cifra, sin contarla dos veces ------------------------------
def coste(con: sqlite3.Connection, *, lote: str = "lote1") -> dict:
    """Coste y latencia POR VIA, desde la BD.

    Se suma sobre `extracciones`, nunca sumando tambien `decisiones`:
    decidir no llama a nadie y su coste es cero por construccion. Antes
    `decisiones.coste_eur` era una copia del de extraccion y cualquier
    agregacion ingenua facturaba dos veces.

    Se suma sobre TODOS los intentos, incluidos los rechazados: un intento
    que no sirvio costo dinero igual. Son dos cifras distintas y las dos
    son ciertas.
    """
    filas = con.execute(
        "SELECT x.via, x.aceptada, count(*) n, sum(CAST(x.coste_eur AS REAL)) eur,"
        "       sum(x.latencia_ms) ms, sum(x.tokens_entrada) t_ent,"
        "       sum(x.tokens_salida) t_sal, sum(x.n_llamadas) llamadas"
        "  FROM extracciones x JOIN documentos d USING(doc_id)"
        " WHERE d.lote=? GROUP BY x.via, x.aceptada", (lote,)).fetchall()
    por_via = [dict(f) for f in filas]
    total_eur = sum(f["eur"] or 0 for f in filas)
    facturado = sum(f["eur"] or 0 for f in filas if not f["aceptada"])
    n_docs = con.execute("SELECT count(*) FROM documentos WHERE lote=?",
                         (lote,)).fetchone()[0] or 1
    return {"por_via": por_via, "total_eur": total_eur,
            "tirado_en_rechazos_eur": facturado,
            "eur_por_documento": total_eur / n_docs,
            "documentos": n_docs}


def informe_coste(con: sqlite3.Connection, *, lote: str = "lote1") -> int:
    c = coste(con, lote=lote)
    print(f"\n  COSTE Y CAPACIDAD{G}   ·   lote {lote}{F}\n")
    print(f"  {'via':<14}{'acept':>6}{'docs':>7}{'EUR':>12}{'ms':>10}{'llamadas':>10}")
    for f in c["por_via"]:
        print(f"  {f['via'] or '?':<14}{'si' if f['aceptada'] else 'NO':>6}"
              f"{f['n']:>7}{(f['eur'] or 0):>12.6f}{int(f['ms'] or 0):>10}"
              f"{int(f['llamadas'] or 0):>10}")
    print(f"\n  total                 {c['total_eur']:>12.6f} EUR")
    print(f"  por documento         {c['eur_por_documento']:>12.6f} EUR")
    if c["tirado_en_rechazos_eur"]:
        print(f"  {A}en intentos rechazados{c['tirado_en_rechazos_eur']:>12.6f} EUR{F}"
              f"  {G}costaron dinero y no se usaron{F}")
    print(f"\n  {G}tarifas declaradas en alberto/precios.yaml; tokens medidos{F}\n")
    return 0
