"""Orquestacion: ingesta -> snapshot -> extraccion -> decision -> JSONL."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import date
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

from alberto import artefactos as arte
from alberto.contratos import FacturaExtraida, nfc
from alberto.db import ahora, log
from alberto.extraccion.cascada import (INTENTO_DETERMINISTA, INTENTO_VISION,
                                        aceptar, faltantes, fusionar,
                                        necesita_vision)
from alberto.erp import ClienteERP
from alberto.erp import snapshot as snap
from alberto.extraccion import extraer_campos, texto_de_pdf
from alberto.maestros import cargar_excel, cargar_proveedores
from alberto.procedencia import (Pasada, abrir_pasada, archivar_config,
                                 guardar_contexto)
from alberto.maestros import guardar as guardar_maestro
from alberto.reglas import Motor, cargar_norma, cargar_politica


def sincronizar_erp(con: sqlite3.Connection, url: str, *, origen: str = "bridge") -> str:
    with ClienteERP(url) as cli:
        asientos, informe = cli.descargar_todo()
    if not informe["completo"]:
        log(con, "erp", "snapshot INCOMPLETO", nivel="warn",
            **{k: v for k, v in informe.items() if k != "metricas"})
    # Las metricas SI se registran: reintentos por ORA-00600, esperas por 429
    # y relogins son la evidencia de que hablar con un ERP de 2009 cuesta.
    log(con, "erp", "metricas del bridge", **informe["metricas"])
    return snap.guardar(con, asientos, informe, origen=origen)


def cargar_maestro(con: sqlite3.Connection, xlsx: Path) -> str:
    return guardar_maestro(con, cargar_excel(xlsx), origen=xlsx.name)


def extraer(con: sqlite3.Connection, *, lote: str = "lote1", forzar: bool = False) -> dict:
    """Fase 1: extraccion determinista. Barata, sin red y sin coste.

    Guarda ademas el texto crudo de pdfplumber como artefacto `raw`: es la
    capa intermedia de procedencia entre el PDF original y los campos.
    """
    pendientes = con.execute(
        "SELECT * FROM documentos WHERE lote=?" + ("" if forzar else
        " AND doc_id NOT IN (SELECT doc_id FROM extracciones)"), (lote,)).fetchall()
    hechos = sin_texto = incompletos = 0
    for d in pendientes:
        t0 = time.monotonic()
        if d["tiene_texto"]:
            texto = texto_de_pdf(Path(d["ruta"]))
            campos = extraer_campos(texto)
            via, plantilla = "determinista", campos.pop("_plantilla", "")
            sha = arte.guardar_texto(con, tipo="texto_pdf", texto=texto)
            arte.enlazar(con, d["doc_id"], INTENTO_DETERMINISTA, "texto_pdf", sha)
        else:
            campos, via, plantilla = {}, "sin_texto", "imagen"
            sin_texto += 1
        f = FacturaExtraida(
            doc_id=d["doc_id"], file_id=d["file_id"], plantilla=plantilla, via=via,
            latencia_ms=int((time.monotonic() - t0) * 1000),
            **{k: v for k, v in campos.items() if not k.startswith("_")})
        # campos_faltantes TAMBIEN en la dataclass: antes se calculaba, se
        # guardaba en la columna y se perdia al serializar campos_json.
        f = replace(f, campos_faltantes=faltantes(f))
        if f.campos_faltantes:
            incompletos += 1
        _guardar_extraccion(con, f, INTENTO_DETERMINISTA, aceptada=True)
        con.execute("UPDATE documentos SET estado=? WHERE doc_id=?",
                    ("extraido_parcial" if f.campos_faltantes else "extraido",
                     d["doc_id"]))
        hechos += 1
    log(con, "extraccion", f"{hechos} documentos", lote=lote,
        sin_texto=sin_texto, incompletos=incompletos)
    return {"extraidos": hechos, "sin_texto": sin_texto,
            "incompletos": incompletos}


def _guardar_extraccion(con: sqlite3.Connection, f: FacturaExtraida, intento: int,
                        *, aceptada: bool, motivo_rechazo: str = "",
                        modelo: str = "", tokens_entrada: int = 0,
                        tokens_salida: int = 0, n_llamadas: int = 0,
                        origen: dict | None = None) -> None:
    campos = {k: str(v) if v is not None else None for k, v in asdict(f).items()}
    campos["campos_faltantes"] = list(f.campos_faltantes)
    if origen:
        campos["_origen"] = origen          # procedencia campo a campo
    con.execute(
        "INSERT OR REPLACE INTO extracciones (doc_id, intento, plantilla, via,"
        " campos_json, campos_faltantes, cuadra_interna, coste_eur, latencia_ms,"
        " aceptada, motivo_rechazo, modelo, tokens_entrada, tokens_salida,"
        " n_llamadas, creado_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f.doc_id, intento, f.plantilla, f.via,
         json.dumps(campos, ensure_ascii=False),
         ",".join(f.campos_faltantes),
         None if f.cuadra_interna() is None else int(f.cuadra_interna()),
         str(f.coste_eur), f.latencia_ms, int(aceptada), motivo_rechazo or None,
         modelo or None, tokens_entrada, tokens_salida, n_llamadas, ahora()))


def _factura_de_fila(fila: sqlite3.Row) -> FacturaExtraida:
    c = json.loads(fila["campos_json"])
    def d(k):
        return Decimal(c[k]) if c.get(k) else None
    f = c.get("fecha")
    return FacturaExtraida(
        doc_id=c["doc_id"], file_id=c["file_id"], plantilla=c.get("plantilla") or "",
        via=c.get("via") or "determinista", pedido=c.get("pedido"),
        nif_emisor=c.get("nif_emisor"), iban=c.get("iban"),
        fecha=date.fromisoformat(f) if f else None,
        base=d("base"), iva_pct=d("iva_pct"), iva_importe=d("iva_importe"), total=d("total"),
        campos_faltantes=tuple(c.get("campos_faltantes") or ()),
        coste_eur=Decimal(c.get("coste_eur") or "0"), latencia_ms=int(c.get("latencia_ms") or 0))


def decidir(con: sqlite3.Connection, *, snapshot_erp: str, snapshot_maestro: str,
            norma: str = "v3", lote: str = "lote1",
            hoy: date | None = None, pasada: Pasada | None = None) -> dict:
    asientos = snap.cargar(con, snapshot_erp)
    proveedores = cargar_proveedores(con, snapshot_maestro)
    # Filtrado por snapshot_maestro: sin esto la clave de `decisiones`
    # afirmaba una reproducibilidad que el codigo no entregaba.
    revisar = frozenset(
        r["clave"] for r in con.execute(
            "SELECT clave FROM notas WHERE version_id=? AND ambito='pedido'"
            " AND clave IS NOT NULL", (snapshot_maestro,)))
    propia = pasada is None
    if propia:
        pasada = abrir_pasada(con, "decide",
                              {"norma": norma, "lote": lote,
                               "snapshot_erp": snapshot_erp,
                               "snapshot_maestro": snapshot_maestro}, hoy=hoy)
    # El contenido de los dos YAML queda archivado como artefacto: es lo que
    # permite a `alberto audita` reconstruir el motor exacto mas adelante.
    shas = archivar_config(con, norma)
    # `hoy` se resuelve UNA vez por pasada y se pasa explicito. Antes cada
    # Motor llamaba a date.today() y esa entrada no quedaba en ningun sitio.
    motor = Motor(cargar_norma(norma), cargar_politica(version=norma),
                  proveedores, asientos, revisar=revisar, hoy=pasada.hoy)

    # extraccion_vigente, no extracciones: con dos intentos por documento la
    # tabla devolveria dos filas y la decision dependeria del orden del cursor.
    filas = con.execute(
        "SELECT x.* FROM extraccion_vigente x JOIN documentos d USING(doc_id)"
        " WHERE d.lote=?", (lote,)).fetchall()
    conteo: dict[str, int] = {}
    for fila in filas:
        f = _factura_de_fila(fila)
        dec = motor.decidir(f, motor.evaluar(f),
                            snapshot_erp=snapshot_erp, snapshot_maestro=snapshot_maestro)
        con.execute(
            "INSERT OR REPLACE INTO decisiones (doc_id, norma_version, snapshot_erp,"
            " snapshot_maestro, result, motivo, reglas_json, coste_eur, latencia_ms, creado_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (dec.doc_id, dec.norma_version, dec.snapshot_erp, dec.snapshot_maestro,
             dec.result, dec.motivo,
             json.dumps([asdict(v) for v in dec.reglas], ensure_ascii=False, default=str),
             str(dec.coste_eur), dec.latencia_ms, ahora()))
        guardar_contexto(con, doc_id=dec.doc_id, norma_version=dec.norma_version,
                         snapshot_erp=snapshot_erp, snapshot_maestro=snapshot_maestro,
                         pasada=pasada, shas=shas, intento=fila["intento"])
        con.execute("UPDATE documentos SET estado=? WHERE doc_id=?",
                    ("escalado" if dec.result == "ESCALAR" else "decidido", dec.doc_id))
        conteo[dec.result] = conteo.get(dec.result, 0) + 1
        if dec.result != "PAGAR":
            # Senal por documento para lo que NO es la via feliz: es lo que
            # Alberto necesita mirar, y `eventos` solo tenia una fila por etapa.
            log(con, "decision", dec.result, doc_id=dec.doc_id,
                pasada_id=pasada.pasada_id, nivel="warn", motivo=dec.motivo)
    resumen = dict(conteo, norma=motor.etiqueta)
    log(con, "decision", f"{len(filas)} decisiones", norma=motor.etiqueta,
        pasada_id=pasada.pasada_id, **conteo)
    if propia:
        pasada.registrar(resumen)
    return resumen


# ---------------------------------------------------------------- fase 2
SQL_PENDIENTES_VISION = """
SELECT d.* FROM documentos d
  JOIN extracciones x ON x.doc_id = d.doc_id AND x.intento = ?
 WHERE d.lote = ?
   AND COALESCE(x.campos_faltantes, '') <> ''
   AND NOT EXISTS (SELECT 1 FROM extracciones y
                    WHERE y.doc_id = d.doc_id AND y.intento = ?)
   AND d.intentos < ?
 ORDER BY d.file_id
"""


def pendientes_vision(con: sqlite3.Connection, *, lote: str = "lote1",
                      max_intentos: int = 2) -> list[sqlite3.Row]:
    """Los documentos que la fase 2 debe mirar.

    Se deriva de `extracciones`, que es la fuente autoritativa, y no de
    `documentos.estado`, que `decidir()` sobrescribe.

    Cuatro mecanismos encadenados, y cada uno sobra si funcionan los demas:
      1. la PK (doc_id, intento) hace imposible duplicar;
      2. el NOT EXISTS excluye los ya hechos, incluidos los rechazados: se
         intentaron y se decidio, no hay nada que repetir;
      3. `intentos < max_intentos` saca de la cola un documento que falla
         siempre, para no girar en bucle sobre el;
      4. el PNG se persiste ANTES de la llamada.
    """
    return con.execute(SQL_PENDIENTES_VISION,
                       (INTENTO_DETERMINISTA, lote, INTENTO_VISION, max_intentos)
                       ).fetchall()


def rasterizar(con: sqlite3.Connection, doc: sqlite3.Row, *, dpi: int = 200,
               dir_raw=None) -> list[bytes]:
    """PDF -> PNG por pagina, persistidos en la capa raw ANTES de gastar una
    llamada. Va fuera del tramo async: es CPU, bloquea, y el portado tiene
    un Lock global."""
    from alberto.extraccion.vision.portado.pdf import render_pdf
    paginas = render_pdf(Path(doc["ruta"]).read_bytes(), dpi=dpi)
    imagenes = []
    for pagina in paginas:
        png = pagina["image"]
        sha = arte.guardar(con, tipo="pagina_png", datos=png, mime="image/png",
                           dir_raw=dir_raw)
        arte.enlazar(con, doc["doc_id"], INTENTO_VISION, "pagina_png", sha,
                     orden=pagina["page"])
        imagenes.append(png)
    return imagenes


def extraer_con_vision(con: sqlite3.Connection, *, lote: str = "lote1",
                       concurrencia: int = 4, dpi: int = 200,
                       limite: int | None = None, max_intentos: int = 2,
                       en_seco: bool = False, dir_raw=None,
                       lector=None, cfg: dict | None = None) -> dict:
    """Fase 2: vision para lo que la fase 1 dejo incompleto. Reanudable.

    `lector` permite inyectar una funcion de lectura en los tests, sin red
    y sin monkeypatch.
    """
    import asyncio

    from alberto.extraccion.cascada import CAMPOS_FUSIONABLES  # noqa: F401
    from alberto.extraccion.vision.lector import campos_de_lectura, leer_lote

    docs = pendientes_vision(con, lote=lote, max_intentos=max_intentos)
    if limite is not None:
        docs = docs[:limite]
    if not docs:
        log(con, "vision", "nada pendiente", lote=lote)
        return {"candidatos": 0, "leidos": 0, "aceptados": 0,
                "rechazados": 0, "fallidos": 0, "coste_eur": "0"}

    trabajos = [(d["doc_id"], rasterizar(con, d, dpi=dpi, dir_raw=dir_raw))
                for d in docs]
    if en_seco:
        log(con, "vision", f"{len(trabajos)} documentos rasterizados (en seco)",
            lote=lote)
        return {"candidatos": len(trabajos), "leidos": 0, "aceptados": 0,
                "rechazados": 0, "fallidos": 0, "coste_eur": "0",
                "paginas": sum(len(p) for _, p in trabajos)}

    por_id = {d["doc_id"]: d for d in docs}
    previas = {d["doc_id"]: _factura_de_fila(
        con.execute("SELECT * FROM extracciones WHERE doc_id=? AND intento=?",
                    (d["doc_id"], INTENTO_DETERMINISTA)).fetchone()) for d in docs}
    cuenta = {"leidos": 0, "aceptados": 0, "rechazados": 0, "fallidos": 0}
    coste_total = Decimal("0")

    def al_terminar(doc_id: str, resultado) -> None:
        nonlocal coste_total
        if isinstance(resultado, Exception):
            # NO se escribe fila: el documento sigue pendiente, pero se
            # cuenta el intento para no girar en bucle sobre el.
            con.execute("UPDATE documentos SET intentos = intentos + 1,"
                        " ultimo_error = ? WHERE doc_id = ?",
                        (f"{type(resultado).__name__}: {resultado}"[:400], doc_id))
            log(con, "vision", "fallo del proveedor", doc_id=doc_id, nivel="warn",
                error=str(resultado)[:200])
            cuenta["fallidos"] += 1
            return

        cuenta["leidos"] += 1
        coste_total += resultado.coste_eur
        sha = arte.guardar_json(con, tipo="lectura",
                                objeto={"texto": resultado.texto,
                                        "llamadas": list(resultado.llamadas)})
        arte.enlazar(con, doc_id, INTENTO_VISION, "lectura", sha,
                     modelo=resultado.modelo)

        previa = previas[doc_id]
        candidata, origen = fusionar(
            previa, campos_de_lectura(resultado.texto),
            coste_eur=resultado.coste_eur, latencia_ms=resultado.latencia_ms,
            modelo=resultado.modelo)
        ok, motivo = aceptar(previa, candidata, origen)
        _guardar_extraccion(con, candidata, INTENTO_VISION, aceptada=ok,
                            motivo_rechazo=motivo, modelo=resultado.modelo,
                            tokens_entrada=resultado.tokens_entrada,
                            tokens_salida=resultado.tokens_salida,
                            n_llamadas=len(resultado.llamadas), origen=origen)
        cuenta["aceptados" if ok else "rechazados"] += 1
        if ok:
            con.execute(
                "UPDATE documentos SET estado=? WHERE doc_id=?",
                ("extraido" if not candidata.campos_faltantes
                 else "extraido_parcial", doc_id))
        log(con, "vision", "aceptada" if ok else f"rechazada ({motivo})",
            doc_id=doc_id, file_id=por_id[doc_id]["file_id"],
            coste_eur=str(resultado.coste_eur))

    if lector is not None:
        for doc_id, paginas in trabajos:
            try:
                al_terminar(doc_id, lector(doc_id, paginas))
            except Exception as exc:                      # noqa: BLE001
                al_terminar(doc_id, exc)
    else:
        from alberto.extraccion.vision.ajustes import (ajustes, cliente_http,
                                                       peticion_de)
        configuracion = cfg or ajustes()

        async def correr() -> None:
            async with cliente_http() as cliente:
                await leer_lote(trabajos, configuracion, peticion_de(cliente),
                                concurrencia=concurrencia, al_terminar=al_terminar)

        asyncio.run(correr())

    resumen = {"candidatos": len(trabajos), **cuenta,
               "coste_eur": str(coste_total)}
    log(con, "vision", f"{cuenta['leidos']} lecturas", lote=lote, **cuenta)
    return resumen
