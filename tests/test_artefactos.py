"""La capa raw: direccionada por contenido."""
import pytest

from alberto import artefactos as arte
from alberto.db import conectar


@pytest.fixture()
def con(tmp_path):
    c = conectar(tmp_path / "t.db")
    c.execute("INSERT INTO documentos (doc_id, file_id, ruta, bytes, tiene_texto,"
              " lote, creado_at) VALUES ('d1','f.pdf','/x',1,1,'lote1','now')")
    return c


def test_guardar_dos_veces_lo_mismo_deja_una_fila(con):
    """Es lo que hace la reanudacion idempotente: re-renderizar el mismo PNG
    no duplica ni una fila ni un byte."""
    a = arte.guardar_texto(con, tipo="texto_pdf", texto="mismo contenido")
    b = arte.guardar_texto(con, tipo="texto_pdf", texto="mismo contenido")
    assert a == b
    assert con.execute("SELECT count(*) FROM artefactos").fetchone()[0] == 1


def test_el_texto_va_inline_y_el_png_a_disco(con, tmp_path):
    txt = arte.guardar_texto(con, tipo="texto_pdf", texto="hola")
    png = arte.guardar(con, tipo="pagina_png", datos=b"\x89PNG" + b"x" * 100,
                       mime="image/png", dir_raw=tmp_path / "raw")
    filas = {f["sha256"]: f for f in con.execute("SELECT * FROM artefactos")}
    assert filas[txt]["contenido"] is not None and filas[txt]["ruta"] is None
    assert filas[png]["ruta"] is not None and filas[png]["contenido"] is None
    assert arte.leer(con, png).startswith(b"\x89PNG")


def test_el_check_impide_una_fila_sin_contenido_ni_ruta(con):
    with pytest.raises(Exception):
        con.execute("INSERT INTO artefactos (sha256, tipo, mime, bytes,"
                    " contenido, ruta, creado_at) VALUES ('x','t','m',0,NULL,NULL,'now')")


def test_los_artefactos_de_un_documento_se_recuperan_en_orden(con, tmp_path):
    for n in (2, 1):
        sha = arte.guardar(con, tipo="pagina_png", datos=f"pag{n}".encode(),
                           mime="image/png", dir_raw=tmp_path / "raw")
        arte.enlazar(con, "d1", 2, "pagina_png", sha, orden=n)
    filas = arte.del_documento(con, "d1", intento=2)
    assert [f["orden"] for f in filas] == [1, 2]
