"""La subida web: el sha256 manda, y un duplicado se explica en vez de callarse.

Rapidos: no necesitan ni ERP ni La Caja entera, solo dos PDF de muestra.
"""
import json
import threading
from datetime import date
from decimal import Decimal
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from alberto.contratos import Asiento, FacturaExtraida, Proveedor
from alberto.db import conectar
from alberto.erp import snapshot as snap
from alberto.maestros import guardar as guardar_maestro
from alberto.pipeline import _guardar_extraccion, decidir
from alberto.procedencia import etiqueta_norma
from alberto.web import subida
from alberto.web.servidor import crear_handler

RAIZ = Path(__file__).resolve().parents[1]
CON_TEXTO = RAIZ / "facturas" / "2026-01-08_P001.pdf"
SIN_TEXTO = RAIZ / "facturas" / "scan_001.pdf"

NIF, IBAN, PEDIDO = "B46102331", "ES2100491500051234567890", "PO-2026-0096"
# La huella de las reglas que hay en disco AHORA. Una decision tomada con
# ella es "las mismas reglas"; cualquier otra etiqueta es "han cambiado".
NORMA_REAL = etiqueta_norma("v3")
NORMA_OTRA = "v3@0tra00"

sin_muestras = pytest.mark.skipif(
    not (CON_TEXTO.is_file() and SIN_TEXTO.is_file()),
    reason="no hay PDF de muestra en facturas/")

# claves que espera frontend/src/lib/types.ts
ESTADO_DOCUMENTO = {"documento", "decision", "historial", "resolucion",
                    "ultimo_result", "reprocesable", "motivo_bloqueo",
                    "motivo_reproceso", "norma_decision", "norma_actual",
                    "norma_cambiada", "aviso"}
SUBIDA_OK = {"ok", "file_id", "doc_id", "renombrado_de", "tiene_texto",
             "decision", "sin_decidir", "aviso"}
REPROCESO = {"ok", "anterior", "nueva", "misma_clave", "pasada_id", "aviso"}


@pytest.fixture()
def caja(tmp_path):
    d = tmp_path / "caja" / "facturas"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def con(tmp_path):
    """BD vacia pero con ERP y maestro cargados: lo minimo para decidir."""
    c = conectar(tmp_path / "t.db")
    snap.guardar(c, [Asiento(asiento_id="AS-1", pedido=PEDIDO, nif=NIF,
                             proveedor_id="P001",
                             importe_esperado=Decimal("3012.89"),
                             estado="PENDIENTE", fecha_registro="08/01/2026")],
                 {"total_declarado": 1, "completo": True, "metricas": {}})
    guardar_maestro(c, {"proveedores": {NIF: Proveedor(
        proveedor_id="P001", nif=NIF, razon_social="X", iban=IBAN,
        condiciones_dias=60)}, "pedidos": {}, "notas": [], "duplicados": []},
        origen="test.xlsx")
    return c


def _subir(con, caja, ruta_o_bytes, nombre=None):
    contenido = (ruta_o_bytes.read_bytes() if isinstance(ruta_o_bytes, Path)
                 else ruta_o_bytes)
    return subida.subir(con, nombre=nombre or CON_TEXTO.name,
                        contenido=contenido, carpeta=caja)


def _unica_decision(con, doc_id, result, *, norma=NORMA_REAL, motivo="x"):
    """Deja UNA sola decision, la que quiere el test.

    Sin el DELETE, la que tomo el pipeline al subir sigue siendo la vigente
    (gana por `creado_at`) y el test estaria midiendo otra cosa.
    """
    con.execute("DELETE FROM decisiones WHERE doc_id=?", (doc_id,))
    con.execute(
        "INSERT INTO decisiones (doc_id, norma_version, snapshot_erp,"
        " snapshot_maestro, result, motivo, reglas_json, creado_at)"
        " VALUES (?,?,'erp-0','m-0',?,?,'[]','2020-01-01T00:00:00')",
        (doc_id, norma, result, motivo))


# --- el camino feliz --------------------------------------------------------
@sin_muestras
def test_una_factura_nueva_se_guarda_registra_extrae_y_decide(con, caja):
    r = _subir(con, caja, CON_TEXTO)

    assert r["ok"] and set(r) == SUBIDA_OK
    assert r["file_id"] == CON_TEXTO.name and r["renombrado_de"] is None
    assert (caja / CON_TEXTO.name).is_file()
    assert con.execute("SELECT count(*) n FROM documentos").fetchone()["n"] == 1
    assert con.execute("SELECT count(*) n FROM extracciones").fetchone()["n"] == 1
    assert r["decision"]["result"] in ("PAGAR", "NO_PAGAR", "ESCALAR")
    assert r["sin_decidir"] is None


@sin_muestras
def test_la_subida_deja_pasada_y_evento_atribuidos_a_la_web(con, caja):
    """Sin esto la web seria una segunda forma de decidir sin rastro."""
    _subir(con, caja, CON_TEXTO)
    args = con.execute("SELECT argumentos_json a FROM pasadas"
                       " WHERE verbo='decide'").fetchone()["a"]
    assert json.loads(args)["origen"] == "web"
    assert con.execute("SELECT count(*) n FROM eventos"
                       " WHERE etapa='subida'").fetchone()["n"] == 1


# --- el duplicado -----------------------------------------------------------
@sin_muestras
def test_el_mismo_contenido_con_otro_nombre_no_se_sube(con, caja):
    primera = _subir(con, caja, CON_TEXTO)
    segunda = _subir(con, caja, CON_TEXTO, nombre="copia distinta.pdf")

    assert segunda["ok"] is False
    assert set(segunda["duplicado"]) == ESTADO_DOCUMENTO
    assert segunda["duplicado"]["documento"]["file_id"] == primera["file_id"]
    assert segunda["nombre_subido"] == "copia distinta.pdf"
    assert con.execute("SELECT count(*) n FROM documentos").fetchone()["n"] == 1
    assert not (caja / "copia distinta.pdf").exists(), "el duplicado toco disco"


@sin_muestras
def test_el_duplicado_cuenta_su_decision_y_su_motivo(con, caja):
    r = _subir(con, caja, CON_TEXTO)
    dup = _subir(con, caja, CON_TEXTO)["duplicado"]

    assert dup["ultimo_result"] == r["decision"]["result"]
    assert dup["decision"]["motivo"] == r["decision"]["motivo"]
    assert len(dup["historial"]) == 1


# --- mismo nombre, otro contenido -------------------------------------------
@sin_muestras
def test_mismo_nombre_y_contenido_distinto_se_renombra(con, caja):
    _subir(con, caja, CON_TEXTO)
    otro = CON_TEXTO.read_bytes() + b"\n%% distinto\n"
    r = _subir(con, caja, otro, nombre=CON_TEXTO.name)

    assert r["ok"] and r["file_id"] == "2026-01-08_P001 (2).pdf"
    assert r["renombrado_de"] == CON_TEXTO.name
    assert (caja / "2026-01-08_P001 (2).pdf").is_file()
    assert (caja / CON_TEXTO.name).is_file()

    tercero = _subir(con, caja, otro + b"mas\n", nombre=CON_TEXTO.name)
    assert tercero["file_id"] == "2026-01-08_P001 (3).pdf"


# --- el bloqueo por pagada, y su excepcion: reglas distintas ----------------
@sin_muestras
def test_una_pagada_con_las_mismas_reglas_no_se_reprocesa(con, caja):
    doc_id = _subir(con, caja, CON_TEXTO)["doc_id"]
    _unica_decision(con, doc_id, "PAGAR", norma=NORMA_REAL)

    estado = subida.estado_documento(con, doc_id)
    assert estado["norma_cambiada"] is False
    assert estado["reprocesable"] is False
    assert "ya fue pagada" in estado["motivo_bloqueo"]
    assert "reglas no han cambiado" in estado["motivo_bloqueo"]
    assert subida.reprocesar(con, doc_id)["ok"] is False


@sin_muestras
def test_una_pagada_si_las_reglas_cambiaron_si_se_puede_reprocesar(con, caja):
    """El motivo por el que se vuelve a subir: con otra norma, otro output."""
    doc_id = _subir(con, caja, CON_TEXTO)["doc_id"]
    _unica_decision(con, doc_id, "PAGAR", norma=NORMA_OTRA)

    estado = subida.estado_documento(con, doc_id)
    assert estado["norma_cambiada"] is True
    assert estado["norma_decision"] == NORMA_OTRA
    assert estado["norma_actual"] == NORMA_REAL
    assert estado["reprocesable"] is True
    assert estado["motivo_bloqueo"] is None
    assert "reglas han cambiado" in estado["motivo_reproceso"]

    r = subida.reprocesar(con, doc_id)
    assert r["ok"] and r["nueva"]["norma_version"] == NORMA_REAL
    assert r["misma_clave"] is False


@sin_muestras
def test_una_escalada_con_reglas_nuevas_lo_dice_al_ofrecer_el_reproceso(con, caja):
    doc_id = _subir(con, caja, CON_TEXTO)["doc_id"]
    _unica_decision(con, doc_id, "ESCALAR", norma=NORMA_OTRA)

    estado = subida.estado_documento(con, doc_id)
    assert estado["reprocesable"] is True
    assert "reglas han cambiado" in estado["motivo_reproceso"]


@sin_muestras
def test_la_resolucion_humana_manda_sobre_el_motor(con, caja):
    """ESCALAR en el motor + PAGAR a mano = pagada. No se vuelve a procesar."""
    doc_id = _subir(con, caja, CON_TEXTO)["doc_id"]
    _unica_decision(con, doc_id, "ESCALAR", norma=NORMA_REAL)
    con.execute("INSERT INTO resoluciones (doc_id, result, motivo, resuelto_por,"
                " at) VALUES (?,'PAGAR','lo miro Alberto','alberto','now')",
                (doc_id,))

    estado = subida.estado_documento(con, doc_id)
    assert estado["ultimo_result"] == "PAGAR"
    assert estado["reprocesable"] is False
    assert "alberto" in estado["motivo_bloqueo"]


@sin_muestras
def test_ni_la_resolucion_humana_bloquea_si_las_reglas_cambiaron(con, caja):
    """La resolucion se conserva; lo que se recalcula es la decision."""
    doc_id = _subir(con, caja, CON_TEXTO)["doc_id"]
    _unica_decision(con, doc_id, "ESCALAR", norma=NORMA_OTRA)
    con.execute("INSERT INTO resoluciones (doc_id, result, motivo, resuelto_por,"
                " at) VALUES (?,'PAGAR','lo miro Alberto','alberto','now')",
                (doc_id,))

    estado = subida.estado_documento(con, doc_id)
    assert estado["ultimo_result"] == "PAGAR" and estado["reprocesable"] is True
    assert "ya fue pagada" in estado["motivo_reproceso"]
    assert subida.reprocesar(con, doc_id)["ok"] is True
    assert con.execute("SELECT count(*) n FROM resoluciones"
                       ).fetchone()["n"] == 1, "el reproceso borro la resolucion"


# --- el reproceso -----------------------------------------------------------
@sin_muestras
def test_reprocesar_una_escalada_anade_decision_sin_borrar_la_vieja(con, caja):
    doc_id = _subir(con, caja, CON_TEXTO)["doc_id"]
    _unica_decision(con, doc_id, "ESCALAR", norma="v3@viejo",
                    motivo="faltan datos")

    r = subida.reprocesar(con, doc_id)
    assert r["ok"] and set(r) == REPROCESO
    assert r["anterior"]["result"] == "ESCALAR"
    assert r["misma_clave"] is False
    assert con.execute("SELECT count(*) n FROM decisiones WHERE doc_id=?",
                       (doc_id,)).fetchone()["n"] == 2
    vieja = con.execute("SELECT creado_at FROM decisiones WHERE doc_id=? AND"
                        " norma_version='v3@viejo'", (doc_id,)).fetchone()
    assert vieja["creado_at"] == "2020-01-01T00:00:00", "se piso la decision vieja"
    assert con.execute("SELECT count(*) n FROM eventos"
                       " WHERE etapa='reproceso'").fetchone()["n"] == 1


@sin_muestras
def test_reprocesar_sin_que_nada_cambie_lo_dice(con, caja):
    """Misma norma y mismos snapshots: la clave es la misma y la fila se pisa."""
    doc_id = _subir(con, caja, CON_TEXTO)["doc_id"]
    con.execute("UPDATE decisiones SET result='ESCALAR', motivo='x'"
                " WHERE doc_id=?", (doc_id,))

    r = subida.reprocesar(con, doc_id)
    assert r["ok"] and r["misma_clave"] is True
    assert con.execute("SELECT count(*) n FROM decisiones WHERE doc_id=?",
                       (doc_id,)).fetchone()["n"] == 1


def test_reprocesar_algo_que_no_existe_no_revienta(con):
    r = subida.reprocesar(con, "no-existe")
    assert r["ok"] is False and r["error"] == "no_encontrado"


# --- el aislamiento del filtro por documento --------------------------------
def test_decidir_un_documento_no_toca_los_demas(con):
    for i, doc in enumerate(("d1", "d2"), 1):
        con.execute("INSERT INTO documentos (doc_id, file_id, ruta, bytes,"
                    " tiene_texto, lote, creado_at) VALUES (?,?,?,1,1,'lote1','x')",
                    (doc, f"f{i}.pdf", f"/x/f{i}.pdf"))
        _guardar_extraccion(con, FacturaExtraida(
            doc_id=doc, file_id=f"f{i}.pdf", plantilla="p", via="determinista",
            pedido=PEDIDO, nif_emisor=NIF, iban=IBAN, fecha=date(2026, 1, 8),
            base=Decimal("2489.99"), iva_pct=Decimal("21"),
            iva_importe=Decimal("522.90"), total=Decimal("3012.89")), 1,
            aceptada=True)
    sid, mid = subida._ultimos(con)
    decidir(con, snapshot_erp=sid, snapshot_maestro=mid, hoy=date(2026, 9, 20))
    antes = con.execute("SELECT creado_at FROM decisiones WHERE doc_id='d2'"
                        ).fetchone()["creado_at"]

    decidir(con, snapshot_erp=sid, snapshot_maestro=mid, hoy=date(2026, 9, 20),
            doc_ids=["d1"])
    despues = con.execute("SELECT creado_at FROM decisiones WHERE doc_id='d2'"
                          ).fetchone()["creado_at"]
    assert antes == despues

    n = con.execute("SELECT count(*) n FROM decisiones").fetchone()["n"]
    decidir(con, snapshot_erp=sid, snapshot_maestro=mid, doc_ids=[])
    assert con.execute("SELECT count(*) n FROM decisiones").fetchone()["n"] == n


# --- casos de borde ---------------------------------------------------------
@sin_muestras
def test_un_pdf_sin_capa_de_texto_se_acepta_y_escala(con, caja):
    r = _subir(con, caja, SIN_TEXTO, nombre=SIN_TEXTO.name)
    assert r["ok"] and r["tiene_texto"] is False
    assert r["decision"]["result"] == "ESCALAR"


@sin_muestras
def test_sin_snapshot_se_registra_pero_se_dice_que_no_hay_decision(tmp_path, caja):
    c = conectar(tmp_path / "vacia.db")
    r = _subir(c, caja, CON_TEXTO)
    assert r["ok"] and r["decision"] is None and r["sin_decidir"]
    assert c.execute("SELECT count(*) n FROM extracciones").fetchone()["n"] == 1


@pytest.mark.parametrize("nombre, contenido, trozo", [
    ("factura.txt", b"%PDF-1.4 x", "solo se admiten PDF"),
    ("", b"%PDF-1.4 x", "no tiene nombre"),
    ("../../fuera.pdf", b"%PDF-1.4 x", None),      # se queda en 'fuera.pdf'
    ("factura.pdf", b"no soy un pdf", "no es un PDF"),
])
def test_lo_que_no_es_una_factura_se_rechaza(con, caja, nombre, contenido, trozo):
    if trozo is None:
        assert subida.nombre_seguro(nombre) == "fuera.pdf"
        return
    with pytest.raises(subida.SubidaInvalida, match=trozo):
        subida.subir(con, nombre=nombre, contenido=contenido, carpeta=caja)


def test_un_fichero_enorme_se_rechaza_antes_de_escribirlo(con, caja):
    with pytest.raises(subida.SubidaInvalida, match="MB"):
        subida.subir(con, nombre="grande.pdf",
                     contenido=b"%PDF-" + b"0" * subida.MAX_BYTES, carpeta=caja)
    assert not list(caja.iterdir())


# --- la API HTTP ------------------------------------------------------------
@pytest.fixture()
def servidor(tmp_path, con, caja):
    """El handler real, en un puerto libre. `con` deja la BD ya sembrada."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), crear_handler(
        tmp_path / "t.db", lote="lote1", caja=caja.parent, norma="v3"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@sin_muestras
def test_la_api_sube_una_vez_y_a_la_segunda_responde_409(servidor):
    cabeceras = {"Content-Type": "application/pdf",
                 "X-File-Name": quote("Facturación ofimática.pdf")}
    contenido = CON_TEXTO.read_bytes()

    r1 = httpx.post(f"{servidor}/api/subir", content=contenido, headers=cabeceras)
    assert r1.status_code == 200 and r1.json()["ok"]
    assert r1.json()["file_id"] == "Facturación ofimática.pdf"

    r2 = httpx.post(f"{servidor}/api/subir", content=contenido, headers=cabeceras)
    assert r2.status_code == 409
    assert r2.json()["duplicado"]["documento"]["file_id"] == "Facturación ofimática.pdf"

    sha = r1.json()["doc_id"]
    r3 = httpx.get(f"{servidor}/api/documento/{sha}")
    assert r3.status_code == 200 and set(r3.json()) == ESTADO_DOCUMENTO
    assert httpx.get(f"{servidor}/api/documento/nope").status_code == 404


def test_la_api_rechaza_lo_que_no_es_pdf_con_422(servidor):
    r = httpx.post(f"{servidor}/api/subir", content=b"hola",
                   headers={"X-File-Name": "x.pdf"})
    assert r.status_code == 422 and r.json()["errores"]


def test_la_api_no_se_come_un_cuerpo_sin_longitud(servidor):
    r = httpx.post(f"{servidor}/api/subir", content=b"",
                   headers={"X-File-Name": "x.pdf"})
    assert r.status_code == 411
