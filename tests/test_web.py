"""La web lee lo decidido; no vuelve a decidir.

El backend de `main` recalculaba con su propio motor mientras el JSONL
salia de este: la misma factura podia aparecer con dos resultados. Estos
tests fijan que eso no puede volver a pasar.
"""
import json
from datetime import date
from decimal import Decimal

import pytest

from alberto.contratos import FacturaExtraida
from alberto.db import conectar
from alberto.pipeline import _guardar_extraccion
from alberto.resolucion import decisiones_de_lote
from alberto.web import datos

# las claves que el frontend declara en frontend/src/lib/api.ts
RESUMEN = {"total", "conteo", "decisiones", "facturas", "lote_tamano",
           "procesando", "error"}
DETALLE = {"file_id", "estado", "decision", "checks", "campos", "error"}
CHECK = {"rule_id", "canonical", "verdict", "reason", "on_fail"}
ESTADOS = {"pendiente", "procesando", "hecha", "error"}


@pytest.fixture()
def con(tmp_path):
    c = conectar(tmp_path / "t.db")
    c.execute("INSERT INTO documentos (doc_id, file_id, ruta, bytes, tiene_texto,"
              " lote, estado, creado_at) VALUES ('d1','f1.pdf','/x',1,1,'lote1',"
              "'decidido','now')")
    f = FacturaExtraida(doc_id="d1", file_id="f1.pdf", plantilla="p",
                        via="determinista", pedido="PO-2026-0096",
                        nif_emisor="B46102331", fecha=date(2026, 1, 8),
                        total=Decimal("121.00"))
    _guardar_extraccion(c, f, 1, aceptada=True)
    c.execute(
        "INSERT INTO decisiones (doc_id, norma_version, snapshot_erp,"
        " snapshot_maestro, result, motivo, reglas_json, creado_at)"
        " VALUES ('d1','v3','erp-1','m-1','NO_PAGAR','el IBAN no coincide', ?, 'now')",
        (json.dumps([
            {"id": "R1_nif_iban", "veredicto": "FALLA",
             "evidencia": {"motivo": "el IBAN no coincide con el maestro"}},
            {"id": "R4_fecha", "veredicto": "PASA", "evidencia": {"fecha": "2026-01-08"}},
            {"id": "R2_pedido", "veredicto": "NA", "evidencia": {"faltan": ["total"]}},
        ]),))
    return c


def test_el_resumen_tiene_la_forma_que_declara_el_frontend(con):
    r = datos.resumen(con)
    assert set(r) == RESUMEN
    assert r["total"] == 1
    assert r["decisiones"] == {"PAGAR": 0, "ESCALAR": 0, "NO_PAGAR": 1}
    assert set(r["conteo"]) == ESTADOS
    assert set(r["facturas"][0]) == {"file_id", "estado", "decision"}


def test_el_detalle_tiene_la_forma_que_declara_el_frontend(con):
    f = datos.factura(con, "f1.pdf")
    assert DETALLE <= set(f)
    assert f["decision"] == "NO_PAGAR"
    assert f["estado"] in ESTADOS
    assert all(set(c) == CHECK for c in f["checks"])


def test_los_veredictos_se_traducen_al_vocabulario_del_frontend(con):
    veredictos = {c["rule_id"]: c["verdict"] for c in
                  datos.factura(con, "f1.pdf")["checks"]}
    assert veredictos == {"R1_nif_iban": "FAIL", "R4_fecha": "PASS",
                          "R2_pedido": "NEEDS_REVIEW"}


def test_la_web_enseña_exactamente_lo_que_se_entrega(con):
    """El invariante que justifica todo este modulo."""
    entregado = {f["file_id"]: f["result"]
                 for f in decisiones_de_lote(con, lote="lote1", norma="v3")}
    en_pantalla = {f["file_id"]: f["decision"]
                   for f in datos.resumen(con)["facturas"]}
    assert entregado == en_pantalla


def test_el_detalle_lleva_la_procedencia_para_la_traza(con):
    p = datos.factura(con, "f1.pdf")["procedencia"]
    assert p["norma"] == "v3" and p["snapshot_erp"] == "erp-1"
    assert p["via"] == "determinista"


def test_una_factura_que_no_existe_devuelve_None(con):
    assert datos.factura(con, "no_existe.pdf") is None


def test_la_instantanea_cubre_todas_las_facturas(con):
    snap = datos.instantanea(con)
    assert set(snap) == {"resumen", "facturas"}
    assert set(snap["facturas"]) == {"f1.pdf"}
