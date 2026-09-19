"""La fase 2 contra SQLite: reanudabilidad, no duplicar, y la vista.

Sin red: se inyecta un lector falso por el parametro `lector`.
"""
from datetime import date
from decimal import Decimal

import pytest

from alberto import artefactos as arte
from alberto.db import conectar
from alberto.extraccion.cascada import INTENTO_DETERMINISTA, INTENTO_VISION
from alberto.extraccion.vision.lector import Lectura
from alberto.pipeline import (_guardar_extraccion, extraer_con_vision,
                              pendientes_vision)
from alberto.contratos import FacturaExtraida

PDF_MINIMO = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
              b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
              b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
              b"trailer<</Root 1 0 R>>\n%%EOF\n")


@pytest.fixture()
def con(tmp_path):
    pdf = tmp_path / "scan_x.pdf"
    pdf.write_bytes(PDF_MINIMO)
    c = conectar(tmp_path / "t.db")
    c.execute("INSERT INTO documentos (doc_id, file_id, ruta, bytes, tiene_texto,"
              " lote, estado, creado_at) VALUES ('d1','scan_x.pdf',?,?,0,'lote1',"
              "'extraido_parcial','now')", (str(pdf), len(PDF_MINIMO)))
    incompleta = FacturaExtraida(doc_id="d1", file_id="scan_x.pdf", plantilla="imagen",
                                 via="sin_texto",
                                 campos_faltantes=("pedido", "nif_emisor", "iban",
                                                   "fecha", "total"))
    _guardar_extraccion(c, incompleta, INTENTO_DETERMINISTA, aceptada=True)
    return c


def lectura_buena(doc_id, paginas):
    return Lectura(doc_id=doc_id,
                   texto=("Pedido PO-2026-0096\nNIF: B46102331\n"
                          "IBAN ES2100491500051234567890\nFecha: 08/01/2026\n"
                          "Base imponible: 100,00\nIVA 21%: 21,00\nTOTAL: 121,00"),
                   llamadas=({"request": {"model": "gemma4"},
                              "response": {"usage": {"prompt_tokens": 10}}},),
                   modelo="gemma4", coste_eur=Decimal("0.004"),
                   tokens_entrada=10, latencia_ms=101_000)


def test_una_lectura_valida_se_acepta_y_gana_la_vista(con, tmp_path):
    r = extraer_con_vision(con, lector=lectura_buena, dir_raw=tmp_path / "raw")
    assert (r["candidatos"], r["aceptados"], r["rechazados"]) == (1, 1, 0)
    vigente = con.execute("SELECT * FROM extraccion_vigente").fetchall()
    assert len(vigente) == 1
    assert vigente[0]["intento"] == INTENTO_VISION
    assert vigente[0]["via"] == "vision"
    assert vigente[0]["campos_faltantes"] == ""
    assert Decimal(vigente[0]["coste_eur"]) == Decimal("0.004")


def test_una_lectura_rechazada_deja_rastro_pero_no_decide(con, tmp_path):
    def vacia(doc_id, paginas):
        return Lectura(doc_id=doc_id, texto="pagina en blanco", modelo="gemma4")
    r = extraer_con_vision(con, lector=vacia, dir_raw=tmp_path / "raw")
    assert (r["aceptados"], r["rechazados"]) == (0, 1)
    vigente = con.execute("SELECT * FROM extraccion_vigente").fetchone()
    assert vigente["intento"] == INTENTO_DETERMINISTA     # manda la fase 1
    rechazada = con.execute("SELECT * FROM extracciones WHERE intento=?",
                            (INTENTO_VISION,)).fetchone()
    assert rechazada["aceptada"] == 0
    assert rechazada["motivo_rechazo"] == "no_aporta_campos"


def test_relanzar_no_repite_ni_duplica(con, tmp_path):
    llamadas = []

    def contando(doc_id, paginas):
        llamadas.append(doc_id)
        return lectura_buena(doc_id, paginas)

    extraer_con_vision(con, lector=contando, dir_raw=tmp_path / "raw")
    antes = con.execute("SELECT count(*) FROM extracciones").fetchone()[0]
    r2 = extraer_con_vision(con, lector=contando, dir_raw=tmp_path / "raw")
    assert len(llamadas) == 1                     # la segunda pasada no llama
    assert r2["candidatos"] == 0
    assert con.execute("SELECT count(*) FROM extracciones").fetchone()[0] == antes


def test_un_fallo_del_proveedor_no_escribe_fila_y_se_reintenta(con, tmp_path):
    def rota(doc_id, paginas):
        raise RuntimeError("502 del gateway")

    r = extraer_con_vision(con, lector=rota, dir_raw=tmp_path / "raw")
    assert r["fallidos"] == 1
    assert con.execute("SELECT count(*) FROM extracciones WHERE intento=?",
                       (INTENTO_VISION,)).fetchone()[0] == 0
    doc = con.execute("SELECT intentos, ultimo_error FROM documentos").fetchone()
    assert doc["intentos"] == 1
    assert "502" in doc["ultimo_error"]
    # sigue en la cola...
    assert len(pendientes_vision(con)) == 1
    # ...pero sale de ella tras agotar los intentos
    assert len(pendientes_vision(con, max_intentos=1)) == 0


def test_la_vista_devuelve_exactamente_una_fila_por_documento(con, tmp_path):
    extraer_con_vision(con, lector=lectura_buena, dir_raw=tmp_path / "raw")
    n_docs = con.execute("SELECT count(*) FROM documentos").fetchone()[0]
    n_vig = con.execute("SELECT count(*) FROM extraccion_vigente").fetchone()[0]
    n_ext = con.execute("SELECT count(*) FROM extracciones").fetchone()[0]
    assert n_ext == 2 and n_vig == n_docs == 1


def test_el_png_se_persiste_antes_de_llamar_al_modelo(con, tmp_path):
    """Por eso una pasada cortada a la mitad sigue siendo inspeccionable."""
    def rota(doc_id, paginas):
        raise RuntimeError("cae justo despues de rasterizar")

    extraer_con_vision(con, lector=rota, dir_raw=tmp_path / "raw")
    pngs = [r for r in arte.del_documento(con, "d1") if r["rol"] == "pagina_png"]
    assert pngs and pngs[0]["mime"] == "image/png"


def test_en_seco_rasteriza_sin_llamar_a_nadie(con, tmp_path):
    r = extraer_con_vision(con, en_seco=True, dir_raw=tmp_path / "raw")
    assert r["leidos"] == 0 and r["paginas"] >= 1
    assert con.execute("SELECT count(*) FROM extracciones WHERE intento=?",
                       (INTENTO_VISION,)).fetchone()[0] == 0
