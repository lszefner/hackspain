"""Sigue UNA decision de principio a fin.

Es la herramienta de depuracion y, a la vez, lo que se ensena en el minuto 4-8
de la defensa: del fichero de entrada a la decision, pasando por cada campo
extraido, cada regla evaluada y la evidencia que la disparo.
"""
from __future__ import annotations

import json
import sqlite3

from alberto.resolucion import decision_de

VERDE, ROJO, GRIS, AZUL, FIN = "\033[32m", "\033[31m", "\033[90m", "\033[36m", "\033[0m"
SIMBOLO = {"PASA": f"{VERDE}PASA{FIN}", "FALLA": f"{ROJO}FALLA{FIN}", "NA": f"{GRIS}N/A {FIN}"}
COLOR_RESULTADO = {"PAGAR": VERDE, "NO_PAGAR": ROJO, "ESCALAR": AZUL}


def _fila(con: sqlite3.Connection, patron: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT d.*, x.campos_json, x.plantilla, x.via, x.campos_faltantes,"
        "       x.cuadra_interna, x.latencia_ms AS ms_extraccion"
        " FROM documentos d LEFT JOIN extraccion_vigente x USING(doc_id)"
        " WHERE d.file_id = ? OR d.file_id LIKE ? OR d.doc_id LIKE ?"
        " ORDER BY length(d.file_id) LIMIT 1",
        (patron, f"%{patron}%", f"{patron}%")).fetchone()


def explicar(con: sqlite3.Connection, patron: str, *, norma: str = "v3") -> int:
    doc = _fila(con, patron)
    if doc is None:
        print(f"{ROJO}no encuentro ningun documento que case con {patron!r}{FIN}")
        return 1

    print(f"\n{AZUL}{'='*74}{FIN}")
    print(f"  {doc['file_id']}")
    print(f"{AZUL}{'='*74}{FIN}")
    print(f"  doc_id     {GRIS}{doc['doc_id'][:32]}…{FIN}")
    print(f"  entrada    {doc['bytes']:,} bytes · "
          f"{'con capa de texto' if doc['tiene_texto'] else ROJO+'IMAGEN (sin texto)'+FIN}")
    print(f"  ingerido   {GRIS}{doc['creado_at']}{FIN}  estado {doc['estado']}")
    if doc["intentos"]:
        print(f"  {ROJO}fallos     {doc['intentos']} intento(s) · "
              f"ultimo: {doc['ultimo_error']}{FIN}")

    print(f"\n{AZUL}1 · EXTRACCION{FIN}  via={doc['via']} "
          f"({doc['ms_extraccion']} ms)")
    if doc["campos_json"]:
        campos = json.loads(doc["campos_json"])
        for clave in ("pedido", "nif_emisor", "iban", "fecha", "base",
                      "iva_pct", "iva_importe", "total"):
            valor = campos.get(clave)
            marca = f"{ROJO}ausente{FIN}" if valor in (None, "None") else valor
            print(f"     {clave:<14} {marca}")
        cuadra = doc["cuadra_interna"]
        estado = (f"{VERDE}cuadra a la centima{FIN}" if cuadra == 1
                  else f"{ROJO}NO cuadra{FIN}" if cuadra == 0
                  else f"{GRIS}sin datos suficientes{FIN}")
        print(f"     {GRIS}aritmetica     {FIN}{estado}  "
              f"{GRIS}(base + IVA == total){FIN}")
        if doc["campos_faltantes"]:
            print(f"     {ROJO}faltan: {doc['campos_faltantes']}{FIN}")

    _vision(con, doc)

    # El MISMO resolvedor que usa `emite`. Antes cada uno rompia el empate a
    # su manera y podian ensenar decisiones distintas de la misma factura.
    dec = decision_de(con, doc["doc_id"], norma)
    if dec is None:
        print(f"\n{ROJO}sin decision para la norma {norma}{FIN}")
        return 1

    ctx = con.execute(
        "SELECT * FROM decision_contexto WHERE doc_id=? AND norma_version=?"
        " AND snapshot_erp=? AND snapshot_maestro=?",
        (doc["doc_id"], dec["norma_version"], dec["snapshot_erp"],
         dec["snapshot_maestro"])).fetchone()

    print(f"\n{AZUL}2 · REGLAS{FIN}  norma={dec['norma_version']} "
          f"{GRIS}erp={dec['snapshot_erp']} maestro={dec['snapshot_maestro']}{FIN}")
    if ctx:
        print(f"     {GRIS}hoy={ctx['hoy']} · codigo={ctx['codigo']} · "
              f"extraccion=intento {ctx['intento_extraccion']} · "
              f"pasada={ctx['pasada_id']}{FIN}")
        print(f"     {GRIS}norma sha {(ctx['norma_sha'] or '')[:12]}… · "
              f"politica sha {(ctx['politica_sha'] or '')[:12]}…{FIN}")
    for v in json.loads(dec["reglas_json"]):
        ev = {k: x for k, x in (v.get("evidencia") or {}).items() if k != "motivo"}
        print(f"     {SIMBOLO[v['veredicto']]}  {v['id']:<16} "
              f"{GRIS}{json.dumps(ev, ensure_ascii=False)[:90]}{FIN}")
        if (v.get("evidencia") or {}).get("motivo"):
            print(f"            {ROJO}↳ {v['evidencia']['motivo']}{FIN}")

    color = COLOR_RESULTADO.get(dec["result"], "")
    print(f"\n{AZUL}3 · DECISION{FIN}")
    print(f"     {color}{dec['result']}{FIN}")
    print(f"     {GRIS}motivo:{FIN} {dec['motivo']}")
    print(f"     {GRIS}coste {dec['coste_eur']} EUR · {dec['latencia_ms']} ms{FIN}")

    res = con.execute("SELECT * FROM resoluciones WHERE doc_id=?", (doc["doc_id"],)).fetchone()
    if res:
        print(f"\n{AZUL}4 · RESUELTO A MANO{FIN}  {res['result']} por "
              f"{res['resuelto_por']}: {res['motivo']}")

    # Solo las notas de la version de maestro que se uso de verdad. Antes
    # salian todas las generales en todas las facturas, de todas las cargas.
    notas = con.execute(
        "SELECT texto FROM notas WHERE version_id=? AND ambito='pedido'"
        " AND clave=?",
        (dec["snapshot_maestro"],
         json.loads(doc["campos_json"] or "{}").get("pedido"))
    ).fetchall() if doc["campos_json"] else []
    if notas:
        print(f"\n{AZUL}5 · NOTAS DE ALBERTO{FIN}")
        for n in notas:
            print(f"     {GRIS}· {n['texto']}{FIN}")
    print()
    return 0


def _vision(con: sqlite3.Connection, doc: sqlite3.Row) -> None:
    """Seccion 1b: que hizo la fase 2, si se llego a ejecutar."""
    from alberto.extraccion.cascada import INTENTO_VISION

    fila = con.execute(
        "SELECT * FROM extracciones WHERE doc_id=? AND intento=?",
        (doc["doc_id"], INTENTO_VISION)).fetchone()
    if fila is None:
        return

    estado = (f"{VERDE}aceptada{FIN}" if fila["aceptada"]
              else f"{ROJO}rechazada{FIN} ({fila['motivo_rechazo']})")
    print(f"\n{AZUL}1b · VISION{FIN}  {estado}  {GRIS}modelo={fila['modelo']} · "
          f"{fila['n_llamadas']} llamadas · {fila['tokens_entrada']}+"
          f"{fila['tokens_salida']} tokens · {fila['coste_eur']} EUR · "
          f"{fila['latencia_ms']} ms{FIN}")

    origen = (json.loads(fila["campos_json"]) or {}).get("_origen") or {}
    if origen:
        de_vision = [c for c, v in origen.items() if v == "vision"]
        print(f"     {GRIS}campos que aporto:{FIN} "
              f"{', '.join(de_vision) if de_vision else GRIS + 'ninguno' + FIN}")
        print(f"     {GRIS}el resto lo leyo la regex y la vision NO puede pisarlo{FIN}")

    arts = con.execute(
        "SELECT rol, count(*) n, sum(a.bytes) b FROM extraccion_artefactos ea"
        " JOIN artefactos a USING(sha256) WHERE ea.doc_id=? AND ea.intento=?"
        " GROUP BY rol", (doc["doc_id"], INTENTO_VISION)).fetchall()
    if arts:
        print(f"     {GRIS}raw: " + " · ".join(
            f"{r['rol']} x{r['n']} ({r['b']:,} B)" for r in arts) + FIN)
