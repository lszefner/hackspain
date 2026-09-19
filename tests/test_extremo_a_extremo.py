"""Contra La Caja real y el ERP real. Se saltan solos si no estan disponibles.

    ./venv/bin/python -m pytest tests -q            # todo
    ./venv/bin/python -m pytest tests -q -m "not lento"
"""
import json
import os
from pathlib import Path

import httpx
import pytest

from alberto import pipeline, salida
from alberto.contratos import RESULTADOS_VALIDOS, nfc
from alberto.db import conectar
from alberto.ingesta import registrar

CAJA = Path(os.environ.get("ALBERTO_CAJA", "caja"))
ERP = os.environ.get("ALBERTO_ERP", "http://127.0.0.1:8009")

pytestmark = pytest.mark.lento


def _erp_vivo() -> bool:
    try:
        return httpx.get(f"{ERP}/erp/estado", timeout=2).status_code == 200
    except Exception:
        return False


sin_caja = pytest.mark.skipif(not CAJA.is_dir(), reason=f"no hay Caja en {CAJA}")
sin_erp = pytest.mark.skipif(not _erp_vivo(), reason=f"el ERP no responde en {ERP}")


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    return conectar(tmp_path_factory.mktemp("db") / "prueba.db")


# --- el ERP y sus tres averias ---------------------------------------------
@sin_erp
def test_descarga_completa_pese_a_los_ORA_00600():
    """El manual avisa: 'un cliente que no reintenta no llega a la pagina 26'."""
    from alberto.erp import ClienteERP
    with ClienteERP(ERP) as cli:
        asientos, informe = cli.descargar_todo()
    assert informe["completo"], informe
    assert informe["descargados"] == informe["total_declarado"] == 516
    assert informe["paginas_declaradas"] == 26
    assert not informe["paginas_fallidas"]
    assert informe["metricas"]["reintentos_ora"] > 0, "el ERP deberia haber fallado y no lo hizo"


# --- ingesta idempotente ----------------------------------------------------
@sin_caja
def test_ingesta_dos_veces_no_duplica(con):
    primera = registrar(con, CAJA / "facturas")
    segunda = registrar(con, CAJA / "facturas")
    assert len(primera) == len(segunda) == 500
    (filas,) = con.execute("SELECT count(*) FROM documentos").fetchone()
    assert filas == 500, "la segunda pasada duplico filas"


@sin_caja
def test_todos_los_file_id_estan_en_nfc(con):
    for (fid,) in con.execute("SELECT file_id FROM documentos"):
        assert fid == nfc(fid), f"{fid!r} no esta normalizado"


@sin_caja
def test_el_enrutado_es_por_contenido_no_por_nombre(con):
    """Enrutar por el prefijo 'scan_' perderia 3 ficheros que tambien son imagen."""
    imagenes = {f for (f,) in con.execute(
        "SELECT file_id FROM documentos WHERE tiene_texto=0")}
    assert len(imagenes) == 29
    no_scan = {f for f in imagenes if not f.startswith("scan_")}
    assert no_scan == {"fax_2026_0411.pdf", "reimpresion_0712.pdf",
                       "copia_2026_0518.pdf"}, no_scan


# --- el pipeline entero -----------------------------------------------------
@sin_caja
@sin_erp
def test_pipeline_completo_y_jsonl_entregable(con, tmp_path):
    sid = pipeline.sincronizar_erp(con, ERP)
    mid = pipeline.cargar_maestro(con, next(CAJA.glob("*.xlsx")))
    assert pipeline.extraer(con)["extraidos"] == 500
    conteo = pipeline.decidir(con, snapshot_erp=sid, snapshot_maestro=mid)

    assert sum(conteo.values()) == 500, "toda factura necesita exactamente un outcome"
    assert set(conteo) <= RESULTADOS_VALIDOS
    assert conteo.get("NO_PAGAR", 0) > 0, "sin NO_PAGAR no se pasa la validacion binaria"

    destino = tmp_path / "outcomes.jsonl"
    escrito = salida.escribir_jsonl(con, destino)
    assert escrito["sin_decision"] == []
    veredicto = salida.verificar(destino, 500)
    assert veredicto["ok"], veredicto["problemas"]

    ids = [json.loads(l)["file_id"] for l in destino.read_text("utf-8").splitlines()]
    assert len(ids) == len(set(ids)) == 500


@sin_caja
@sin_erp
def test_los_nueve_pedidos_pagados_nunca_se_pagan(con):
    filas = con.execute(
        "SELECT x.result FROM decisiones x WHERE x.motivo LIKE '%PAGADA%'").fetchall()
    assert filas, "no se detecto ningun pedido ya pagado"
    assert all(f["result"] == "NO_PAGAR" for f in filas)


@sin_caja
@sin_erp
def test_reproceso_no_duplica_decisiones(con):
    sid = con.execute("SELECT snapshot_id FROM snapshots_erp LIMIT 1").fetchone()[0]
    mid = con.execute("SELECT version_id FROM maestro_versiones LIMIT 1").fetchone()[0]
    pipeline.decidir(con, snapshot_erp=sid, snapshot_maestro=mid)
    (n,) = con.execute("SELECT count(*) FROM decisiones").fetchone()
    assert n == 500, f"el reproceso dejo {n} decisiones en vez de 500"
