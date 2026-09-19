"""La fase de validacion: cobertura, no regresion y el contrato de entrega."""
import json

import pytest

from alberto.contratos import FacturaExtraida
from alberto.db import conectar
from alberto.extraccion.cascada import INTENTO_DETERMINISTA, INTENTO_VISION
from alberto.pipeline import _guardar_extraccion
from alberto.validacion import (campos_por_regla, congelar, contrato,
                                instantanea, medir, regresiones)
from datetime import date
from decimal import Decimal

COMPLETA = dict(pedido="PO-2026-0096", nif_emisor="B46102331",
                iban="ES2100491500051234567890", fecha=date(2026, 1, 8),
                base=Decimal("100.00"), iva_pct=Decimal("21"),
                iva_importe=Decimal("21.00"), total=Decimal("121.00"))


@pytest.fixture()
def con(tmp_path):
    c = conectar(tmp_path / "t.db")
    for i, cambios in enumerate([{}, {"fecha": None}], 1):
        doc = f"d{i}"
        c.execute("INSERT INTO documentos (doc_id, file_id, ruta, bytes,"
                  " tiene_texto, lote, creado_at) VALUES (?,?,?,1,1,'lote1','now')",
                  (doc, f"f{i}.pdf", f"/x/f{i}.pdf"))
        campos = COMPLETA | cambios
        f = FacturaExtraida(doc_id=doc, file_id=f"f{i}.pdf", plantilla="p",
                            via="determinista", **campos,
                            campos_faltantes=tuple(k for k in ("fecha",)
                                                   if campos[k] is None))
        _guardar_extraccion(c, f, INTENTO_DETERMINISTA, aceptada=True)
    return c


def test_los_campos_exigidos_salen_de_la_propia_norma():
    """Si manana se anade una regla al YAML, la validacion la exige sola."""
    porc = campos_por_regla("v3")
    assert "R1_nif_iban" in porc["iban"]
    assert "R4_fecha" in porc["fecha"]
    assert set(porc) >= {"nif_emisor", "iban", "pedido", "total", "fecha"}


def test_mide_cobertura_y_veredicto(con):
    m = medir(con)
    assert m["total"] == 2 and m["listos"] == 1
    assert m["cobertura"]["fecha"] == 1
    assert m["cobertura"]["iban"] == 2
    assert m["incompletos"] == [("f2.pdf", "fecha")]


def test_el_contrato_devuelve_una_fila_por_documento_con_via(con):
    filas = contrato(con)
    assert len(filas) == 2
    assert {f["via"] for f in filas} == {"determinista"}
    assert all("campos_json" in f.keys() for f in filas)


def test_no_regresion_detecta_un_campo_perdido(con, tmp_path):
    oro = tmp_path / "oro.json"
    congelar(con, oro)
    assert regresiones(con, oro) == []
    # alguien rompe el extractor y f1 pierde el IBAN
    f = FacturaExtraida(doc_id="d1", file_id="f1.pdf", plantilla="p",
                        via="determinista", **(COMPLETA | {"iban": None}))
    _guardar_extraccion(con, f, INTENTO_DETERMINISTA, aceptada=True)
    fallos = regresiones(con, oro)
    assert len(fallos) == 1 and "f1.pdf" in fallos[0] and "iban" in fallos[0]


def test_ganar_campos_no_es_regresion(con, tmp_path):
    """Es justo lo que la fase 2 viene a hacer."""
    oro = tmp_path / "oro.json"
    congelar(con, oro)
    f = FacturaExtraida(doc_id="d2", file_id="f2.pdf", plantilla="p",
                        via="vision", **COMPLETA)
    _guardar_extraccion(con, f, INTENTO_VISION, aceptada=True)
    assert regresiones(con, oro) == []
    assert medir(con)["listos"] == 2


def test_la_procedencia_campo_a_campo_viaja_en_campos_json(con):
    f = FacturaExtraida(doc_id="d2", file_id="f2.pdf", plantilla="p",
                        via="vision", **COMPLETA)
    _guardar_extraccion(con, f, INTENTO_VISION, aceptada=True,
                        origen={"fecha": "vision", "iban": "determinista"})
    fila = [x for x in contrato(con) if x["file_id"] == "f2.pdf"][0]
    assert json.loads(fila["campos_json"])["_origen"]["fecha"] == "vision"
