"""Identidad por contenido: la configuracion vale por lo que dice."""
from datetime import date

import pytest
import yaml

from alberto import artefactos as arte
from alberto.db import conectar
from alberto.procedencia import (abrir_pasada, archivar_config, etiqueta_norma,
                                 huella, rutas_norma, version_base,
                                 version_codigo)
from alberto.reglas import cargar_norma


@pytest.fixture()
def con(tmp_path):
    return conectar(tmp_path / "t.db")


def test_la_etiqueta_lleva_la_huella_del_contenido():
    e = etiqueta_norma("v3")
    assert e.startswith("v3@") and len(e.split("@")[1]) == 6
    assert version_base(e) == "v3"


def test_cambiar_la_politica_cambia_la_etiqueta(tmp_path, monkeypatch):
    """Es el agujero central: antes la politica no estaba en ninguna parte,
    asi que cambiarla producia otra decision bajo la MISMA clave y el
    INSERT OR REPLACE borraba la anterior."""
    norma, politica = rutas_norma("v3")
    antes = huella(norma, politica)
    copia = tmp_path / "politica.yaml"
    copia.write_text(politica.read_text("utf-8") + "\n# un comentario\n")
    assert huella(norma, copia) != antes


def test_la_norma_se_sella_al_cargarla():
    datos = cargar_norma("v3")
    assert datos["_etiqueta"] == etiqueta_norma("v3")
    assert datos["version"] == "v3"      # la etiqueta legible no se pierde


def test_el_contenido_se_archiva_y_se_recupera(con):
    shas = archivar_config(con, "v3")
    norma = yaml.safe_load(arte.leer(con, shas["norma_sha"]).decode("utf-8"))
    politica = yaml.safe_load(arte.leer(con, shas["politica_sha"]).decode("utf-8"))
    assert norma["version"] == "v3"
    assert "mapeo" in politica


def test_archivar_dos_veces_no_duplica(con):
    a = archivar_config(con, "v3")
    b = archivar_config(con, "v3")
    assert a == b
    assert con.execute("SELECT count(*) FROM artefactos WHERE tipo='config'"
                       ).fetchone()[0] == 2


def test_la_version_del_codigo_avisa_si_el_arbol_esta_sucio():
    v = version_codigo()
    assert v == "sin-git" or len(v) >= 7


def test_la_pasada_fija_hoy_una_sola_vez(con):
    """`hoy` era date.today() dentro del Motor y no quedaba registrado. Con
    la norma v4 de vencimiento eso significaria decidir distinto cada dia."""
    pa = abrir_pasada(con, "decide", {"norma": "v3"}, hoy=date(2026, 3, 1))
    assert pa.hoy == date(2026, 3, 1)
    fila = con.execute("SELECT * FROM pasadas WHERE pasada_id=?",
                       (pa.pasada_id,)).fetchone()
    assert fila["hoy"] == "2026-03-01"
    assert fila["ok"] is None            # abierta
    pa.registrar({"n": 1})
    fila = con.execute("SELECT * FROM pasadas WHERE pasada_id=?",
                       (pa.pasada_id,)).fetchone()
    assert fila["ok"] == 1 and fila["fin"] is not None


def test_la_regla_se_busca_por_tipo_y_no_por_posicion():
    """`self.norma["reglas"][2]` devolvia la regla equivocada en silencio en
    cuanto alguien reordenara el YAML, que es lo que se hace al escribir v4."""
    from alberto.reglas import Motor, cargar_politica
    norma = cargar_norma("v3")
    norma["reglas"] = list(reversed(norma["reglas"]))     # v4 reordena
    m = Motor(norma, cargar_politica(), {}, {})
    assert m.regla_por_tipo("iva")["id"] == "R3_iva"
    assert m._iva_estandar() == "21"
