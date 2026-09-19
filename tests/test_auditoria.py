"""La prueba: volver a tomar la decision desde lo registrado."""
from datetime import date
from decimal import Decimal

import pytest

from alberto.auditoria import auditar
from alberto.contratos import FacturaExtraida
from alberto.db import conectar
from alberto.pipeline import _guardar_extraccion, decidir
from alberto.erp import snapshot as snap
from alberto.contratos import Asiento, Proveedor
from alberto.maestros import guardar as guardar_maestro

NIF, IBAN, PEDIDO = "B46102331", "ES2100491500051234567890", "PO-2026-0096"


@pytest.fixture()
def con(tmp_path):
    c = conectar(tmp_path / "t.db")
    c.execute("INSERT INTO documentos (doc_id, file_id, ruta, bytes, tiene_texto,"
              " lote, creado_at) VALUES ('d1','f1.pdf','/x',1,1,'lote1','now')")
    f = FacturaExtraida(
        doc_id="d1", file_id="f1.pdf", plantilla="p", via="determinista",
        pedido=PEDIDO, nif_emisor=NIF, iban=IBAN, fecha=date(2026, 1, 8),
        base=Decimal("2489.99"), iva_pct=Decimal("21"),
        iva_importe=Decimal("522.90"), total=Decimal("3012.89"))
    _guardar_extraccion(c, f, 1, aceptada=True)
    sid = snap.guardar(c, [Asiento(asiento_id="AS-1", pedido=PEDIDO, nif=NIF,
                                   proveedor_id="P001",
                                   importe_esperado=Decimal("3012.89"),
                                   estado="PENDIENTE", fecha_registro="08/01/2026")],
                       {"total_declarado": 1, "completo": True, "metricas": {}})
    mid = guardar_maestro(c, {"proveedores": {NIF: Proveedor(
        proveedor_id="P001", nif=NIF, razon_social="X", iban=IBAN,
        condiciones_dias=60)}, "pedidos": {}, "notas": [], "duplicados": []},
        origen="test.xlsx")
    return c, sid, mid


def test_una_decision_recien_tomada_se_reproduce(con):
    con, sid, mid = con
    decidir(con, snapshot_erp=sid, snapshot_maestro=mid, hoy=date(2026, 9, 20))
    res = auditar(con)
    assert len(res) == 1
    assert res[0].reproducible is True
    assert res[0].almacenado == res[0].rederivado == "PAGAR"


def test_la_auditoria_usa_el_hoy_registrado_y_no_el_de_ahora(con):
    """Si `hoy` no se registrara, una factura con fecha futura se
    reproduciria distinta segun el dia en que se audite."""
    con, sid, mid = con
    decidir(con, snapshot_erp=sid, snapshot_maestro=mid, hoy=date(2026, 1, 1))
    ctx = con.execute("SELECT hoy FROM decision_contexto").fetchone()
    assert ctx["hoy"] == "2026-01-01"
    assert auditar(con)[0].reproducible is True


def test_una_decision_sin_contexto_se_reporta_no_se_finge(con):
    con, _sid, _mid = con
    con.execute("INSERT INTO decisiones (doc_id, norma_version, snapshot_erp,"
                " snapshot_maestro, result, motivo, reglas_json, creado_at)"
                " VALUES ('d1','v3','erp-x','m-x','PAGAR','x','[]','now')")
    res = [r for r in auditar(con) if r.norma_version == "v3"]
    assert res[0].reproducible is False
    assert res[0].problema == "sin contexto registrado"


def test_si_falta_la_configuracion_archivada_se_dice(con):
    con, sid, mid = con
    decidir(con, snapshot_erp=sid, snapshot_maestro=mid, hoy=date(2026, 9, 20))
    con.execute("DELETE FROM artefactos WHERE tipo='config'")
    res = auditar(con)
    assert res[0].reproducible is False
    assert "configuracion" in res[0].problema
