"""Subida de UNA factura desde la web, con el sha256 como unica identidad.

El mismo PDF con otro nombre no es otra factura: la clave de `documentos` ya
lo decia y aqui solo se respeta. La diferencia con `alberto ingesta` es que
alli el duplicado se ignora EN SILENCIO (TODO.md T28.3) y aqui se devuelve
con su historial: cuando entro, que se decidio y por que.

Que este duplicado se pueda RECHAZAR depende de las reglas. Rechazar una
factura ya pagada solo vale si la norma y la politica son las mismas que la
pagaron; si han cambiado, el motor puede dar otro resultado y negarse a
reprocesar seria esconder justo la respuesta que se viene a buscar.

Todo lo que escribe pasa por las mismas funciones que el CLI --
`registrar_uno`, `pipeline.extraer`, `pipeline.decidir` --, abre una `pasada`
y deja `eventos`. La web no tiene una segunda forma de decidir: esa era la
objecion que hasta ahora justificaba que `do_POST` no existiera.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from alberto import artefactos as arte
from alberto import pipeline
from alberto.contratos import nfc
from alberto.db import log
from alberto.erp import snapshot as snap
from alberto.ingesta import registrar_uno
from alberto.procedencia import abrir_pasada, etiqueta_norma
from alberto.resolucion import Ambigua, decision_de
from alberto.web import datos

MAX_BYTES = 20 * 1024 * 1024
MAGIA_PDF = b"%PDF-"

# ThreadingHTTPServer atiende cada peticion en un hilo: dos subidas iguales a
# la vez pasarian las dos la comprobacion de duplicado. El cerrojo cubre
# comprobar-y-escribir dentro del proceso; el BEGIN IMMEDIATE de mas abajo,
# contra un `alberto decide` corriendo en otro terminal.
_CERROJO = threading.Lock()


class SubidaInvalida(ValueError):
    """Lo que el usuario mando no es una factura que podamos aceptar."""


# ------------------------------------------------------------------ nombres
def nombre_seguro(nombre: str) -> str:
    """El nombre que mando el navegador, reducido a un nombre de fichero.

    NFC porque `file_id` viaja al JSONL y el verificador compara cadenas: un
    NFD silencioso suspende la entrega.
    """
    limpio = nfc(nombre.replace("\\", "/").split("/")[-1]).strip()
    if not limpio or limpio in (".", ".."):
        raise SubidaInvalida("el fichero no tiene nombre")
    if not limpio.lower().endswith(".pdf"):
        raise SubidaInvalida("solo se admiten PDF")
    return limpio


def nombre_libre(con: sqlite3.Connection, carpeta: Path, file_id: str) -> str:
    """`X.pdf` ocupado por OTRO contenido -> `X (2).pdf`.

    `documentos.file_id` es UNIQUE: sin esto la subida reventaria con
    IntegrityError, que es justo el fallo que TODO.md T28.2 tiene localizado.
    """
    def ocupado(nombre: str) -> bool:
        fila = con.execute("SELECT 1 FROM documentos WHERE file_id=?",
                           (nombre,)).fetchone()
        return fila is not None or (carpeta / nombre).exists()

    if not ocupado(file_id):
        return file_id
    tronco = file_id[:-4]
    n = 2
    while ocupado(f"{tronco} ({n}).pdf"):
        n += 1
    return f"{tronco} ({n}).pdf"


# ------------------------------------------------------------------- estado
def _ultimos(con: sqlite3.Connection) -> tuple[str | None, str | None]:
    """Snapshot del ERP y version del maestro mas recientes. Sin los dos no
    se puede decidir, y decir 'no se ha decidido' es mejor que inventarlo."""
    fila = con.execute("SELECT version_id FROM maestro_versiones"
                       " ORDER BY creado_at DESC LIMIT 1").fetchone()
    return snap.ultimo(con), (fila["version_id"] if fila else None)


def _etiqueta_actual(norma: str) -> str | None:
    """La huella de las reglas que hay AHORA en disco ('v3@806e4d').

    Cubre norma Y politica: las dos deciden, y cambiar cualquiera de las dos
    puede dar otro resultado sobre la misma factura. None si no se pueden
    leer, y entonces no se afirma que hayan cambiado.
    """
    try:
        return etiqueta_norma(norma)
    except (FileNotFoundError, OSError):
        return None


def estado_documento(con: sqlite3.Connection, doc_id: str, *,
                     norma: str = "v3") -> dict | None:
    """Todo lo que hay que contarle al que sube un fichero ya conocido.

    `norma` es la FAMILIA ('v3'), no la etiqueta con huella: `decision_de`
    resuelve dentro de la familia y se queda con la mas reciente.
    """
    doc = con.execute("SELECT * FROM documentos WHERE doc_id=?",
                      (doc_id,)).fetchone()
    if doc is None:
        return None

    aviso = None
    try:
        vigente = decision_de(con, doc_id, norma)
    except Ambigua as exc:
        vigente, aviso = None, str(exc)

    historial = con.execute(
        "SELECT * FROM decisiones WHERE doc_id=? ORDER BY creado_at DESC",
        (doc_id,)).fetchall()
    res = con.execute("SELECT * FROM resoluciones WHERE doc_id=?",
                      (doc_id,)).fetchone()

    # La resolucion humana manda sobre el motor: si Alberto ya dijo PAGAR a
    # mano, el documento esta pagado aunque la decision automatica escalara.
    ultimo = res["result"] if res else (vigente["result"] if vigente else None)

    # Rechazar una factura pagada solo tiene sentido si las reglas son las
    # MISMAS que la decidieron. Si han cambiado, volver a procesarla puede
    # dar otro resultado, y esa es justo la razon de querer subirla otra vez.
    actual = _etiqueta_actual(norma)
    de_la_decision = vigente["norma_version"] if vigente else None
    cambiada = bool(actual and de_la_decision and actual != de_la_decision)

    pagada = ultimo == "PAGAR"
    bloqueo = motivo_reproceso = None
    if pagada and not cambiada:
        bloqueo = "ya fue pagada"
        if res:
            bloqueo += f" (resuelta a mano por {res['resuelto_por'] or 'alguien'})"
        bloqueo += " y las reglas no han cambiado desde entonces"
    elif pagada and cambiada:
        motivo_reproceso = (
            f"ya fue pagada con {de_la_decision}, pero las reglas han cambiado"
            f" ({actual}): volver a procesarla puede dar otro resultado")
    elif cambiada:
        motivo_reproceso = (f"las reglas han cambiado desde que se decidio"
                            f" ({de_la_decision} -> {actual})")

    return {
        "documento": datos._documento(doc),
        "decision": datos._decision(vigente, datos._descripciones(
            vigente["norma_version"])) if vigente else None,
        "historial": [datos._decision(r, datos._descripciones(r["norma_version"]))
                      for r in historial],
        "resolucion": dict(res) if res else None,
        "ultimo_result": ultimo,
        "reprocesable": (not pagada) or cambiada,
        "motivo_bloqueo": bloqueo,
        "motivo_reproceso": motivo_reproceso,
        "norma_decision": de_la_decision,
        "norma_actual": actual,
        "norma_cambiada": cambiada,
        "aviso": aviso,
    }


def _aviso_norma(con: sqlite3.Connection, norma: str) -> str | None:
    """Si el YAML en disco ya no es el de la ultima pasada completa, la
    decision nueva estrena etiqueta y el muro pasaria a ensenar UN documento.
    Se avisa en vez de callarlo."""
    etiqueta = _etiqueta_actual(norma)
    activa = datos.norma_activa(con)
    if not etiqueta:
        return None
    if activa and activa != etiqueta:
        return (f"la norma en disco ({etiqueta}) no es la de la ultima pasada"
                f" ({activa}): lanza `alberto decide` para el lote entero")
    return None


# ------------------------------------------------------------------- subida
def subir(con: sqlite3.Connection, *, nombre: str, contenido: bytes,
          carpeta: Path, lote: str = "lote1", norma: str = "v3") -> dict:
    """Guarda, registra, extrae y decide UN PDF. O explica por que no."""
    file_id = nombre_seguro(nombre)
    if not contenido.startswith(MAGIA_PDF):
        raise SubidaInvalida("el contenido no es un PDF")
    if len(contenido) > MAX_BYTES:
        raise SubidaInvalida(f"el fichero pasa de {MAX_BYTES // 2**20} MB")

    # El hash sale de los BYTES, antes de tocar el disco: un duplicado no
    # llega a escribirse nunca.
    doc_id = arte.sha256_de(contenido)

    with _CERROJO:
        ya = estado_documento(con, doc_id, norma=norma)
        if ya is not None:
            log(con, "subida", "duplicado: ya estaba en la plataforma",
                doc_id=doc_id, origen="web", nombre_subido=file_id,
                file_id=ya["documento"]["file_id"],
                ultimo_result=ya["ultimo_result"])
            return {"ok": False, "duplicado": ya, "nombre_subido": file_id}

        if not carpeta.is_dir():
            raise SubidaInvalida(
                f"no encuentro la carpeta de facturas en {carpeta}:"
                f" arranca `alberto web --caja RUTA`")

        definitivo = nombre_libre(con, carpeta, file_id)
        renombrado_de = file_id if definitivo != file_id else None
        ruta = carpeta / definitivo
        ruta.write_bytes(contenido)

        # `conectar` va en autocommit: sin transaccion, un fallo a mitad deja
        # la fila registrada y sin extraer, y el fichero en disco.
        con.execute("BEGIN IMMEDIATE")
        try:
            doc = registrar_uno(con, ruta, lote=lote)
            log(con, "subida", "registrada desde la web", doc_id=doc_id,
                origen="web", file_id=definitivo, renombrado_de=renombrado_de,
                bytes=len(contenido))
            pipeline.extraer(con, lote=lote, doc_ids=[doc_id])
            sid, mid = _ultimos(con)
            sin_decidir = None
            if sid and mid:
                pasada = abrir_pasada(con, "decide", {
                    "norma": norma, "lote": lote, "snapshot_erp": sid,
                    "snapshot_maestro": mid, "origen": "web",
                    "doc_ids": [doc_id]})
                resumen = pipeline.decidir(
                    con, snapshot_erp=sid, snapshot_maestro=mid, norma=norma,
                    lote=lote, pasada=pasada, doc_ids=[doc_id])
                pasada.registrar(resumen)
            else:
                sin_decidir = ("no hay snapshot del ERP o maestro: corre"
                               " `alberto snapshot` y `alberto maestro`")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            ruta.unlink(missing_ok=True)
            raise

    estado = estado_documento(con, doc_id, norma=norma)
    return {"ok": True, "file_id": definitivo, "doc_id": doc_id,
            "renombrado_de": renombrado_de, "tiene_texto": doc.tiene_texto,
            "decision": estado["decision"] if estado else None,
            "sin_decidir": sin_decidir, "aviso": _aviso_norma(con, norma)}


def reprocesar(con: sqlite3.Connection, doc_id: str, *,
               norma: str = "v3") -> dict:
    """Vuelve a decidir UN documento con la norma y los snapshots de ahora.

    No se re-extrae: los campos leidos del PDF no cambian porque cambie la
    norma. Las decisiones viejas se conservan -- la clave de `decisiones`
    lleva norma y snapshots --, salvo que nada haya cambiado, en cuyo caso el
    INSERT OR REPLACE pisa la fila y se devuelve `misma_clave`.
    """
    estado = estado_documento(con, doc_id, norma=norma)
    if estado is None:
        return {"ok": False, "error": "no_encontrado",
                "motivo": "ese documento no esta en la plataforma"}
    if not estado["reprocesable"]:
        return {"ok": False, "motivo": estado["motivo_bloqueo"],
                "norma_decision": estado["norma_decision"],
                "norma_actual": estado["norma_actual"]}

    sid, mid = _ultimos(con)
    if not (sid and mid):
        return {"ok": False, "motivo": "no hay snapshot del ERP o maestro:"
                                       " corre `alberto snapshot` y `alberto maestro`"}

    lote = estado["documento"]["lote"]
    anterior = estado["decision"]
    misma_clave = bool(anterior
                       and anterior["norma_version"] == etiqueta_norma(norma)
                       and anterior["snapshot_erp"] == sid
                       and anterior["snapshot_maestro"] == mid)

    with _CERROJO:
        con.execute("BEGIN IMMEDIATE")
        try:
            # No-op si ya hay extraccion (el NOT IN de `extraer`). Esta aqui
            # para el documento que se registro y nunca se llego a extraer.
            pipeline.extraer(con, lote=lote, doc_ids=[doc_id])
            pasada = abrir_pasada(con, "decide", {
                "norma": norma, "lote": lote, "snapshot_erp": sid,
                "snapshot_maestro": mid, "origen": "web", "reproceso": True,
                "doc_ids": [doc_id]})
            resumen = pipeline.decidir(
                con, snapshot_erp=sid, snapshot_maestro=mid, norma=norma,
                lote=lote, pasada=pasada, doc_ids=[doc_id])
            pasada.registrar(resumen)
            nueva = decision_de(con, doc_id, norma)
            log(con, "reproceso",
                f"{anterior['result'] if anterior else 'sin decision'} ->"
                f" {nueva['result'] if nueva else 'sin decision'}",
                doc_id=doc_id, pasada_id=pasada.pasada_id, origen="web",
                misma_clave=misma_clave)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    return {"ok": True, "anterior": anterior,
            "nueva": datos._decision(nueva, datos._descripciones(
                nueva["norma_version"])) if nueva else None,
            "misma_clave": misma_clave, "pasada_id": pasada.pasada_id,
            "aviso": _aviso_norma(con, norma)}
