"""La web lee lo decidido; no vuelve a decidir.

Estos tests fijan el contrato que declara frontend/src/lib/types.ts. Si
alguien cambia una forma aqui, la web deja de pintar y queremos enterarnos
en pytest, no en la defensa.
"""
import json
from datetime import date
from decimal import Decimal

import pytest

from alberto.contratos import FacturaExtraida
from alberto.db import conectar
from alberto.pipeline import _guardar_extraccion
from alberto.resolucion import decisiones_de_lote
from alberto.web import datos as D

# claves exactas de frontend/src/lib/types.ts
DOCUMENTO = {"doc_id", "file_id", "ruta", "bytes", "tiene_texto", "lote",
             "estado", "intentos", "ultimo_error", "creado_at"}
EXTRACCION = {"doc_id", "intento", "plantilla", "via", "campos",
              "campos_faltantes", "cuadra_interna", "coste_eur",
              "latencia_ms", "creado_at"}
CAMPOS = {"numero", "fecha", "nif_emisor", "proveedor", "pedido", "iban",
          "base_cent", "iva_pct", "iva_cent", "total_cent"}
DECISION = {"doc_id", "norma_version", "snapshot_erp", "snapshot_maestro",
            "result", "motivo", "reglas", "coste_eur", "latencia_ms",
            "creado_at"}
FACTURA_FILA = {"file_id", "doc_id", "lote", "etapa", "tiene_texto", "via",
                "proveedor", "nif", "pedido", "total_cent", "result",
                "motivo", "latencia_ms", "coste_eur", "intentos",
                "decidida_at"}
ESCALADO = {"file_id", "doc_id", "motivo", "categoria", "proveedor", "pedido",
            "total_cent", "importe_erp_cent", "email_posible", "resolucion"}
EXPEDIENTE = {"documento", "extraccion", "decision", "otrasDecisiones",
              "eventos", "notas", "proveedor", "asiento", "resolucion",
              "prev", "next"}
SALUD = {"erp", "llm", "pendientes", "reintentos", "errores24h"}
COSTE = {"rutas", "coste_por_doc_eur", "docs_por_segundo", "proyeccion",
         "punto_cruce_ocr"}


@pytest.fixture()
def con(tmp_path):
    c = conectar(tmp_path / "t.db")
    for i, (result, motivo) in enumerate(
            [("PAGAR", "cumple"), ("ESCALAR", "faltan datos: fecha"),
             ("NO_PAGAR", "el IBAN no coincide")], 1):
        doc = f"d{i}"
        c.execute("INSERT INTO documentos (doc_id, file_id, ruta, bytes,"
                  " tiene_texto, lote, estado, creado_at)"
                  " VALUES (?,?,?,1,1,'lote1','decidido','2026-09-19T10:00:00')",
                  (doc, f"f{i}.pdf", f"/x/f{i}.pdf"))
        f = FacturaExtraida(doc_id=doc, file_id=f"f{i}.pdf", plantilla="p",
                            via="determinista", pedido="PO-2026-0096",
                            nif_emisor="B46102331", fecha=date(2026, 1, 8),
                            base=Decimal("100.00"), iva_pct=Decimal("21"),
                            iva_importe=Decimal("21.00"),
                            total=Decimal("121.00"))
        _guardar_extraccion(c, f, 1, aceptada=True)
        c.execute(
            "INSERT INTO decisiones (doc_id, norma_version, snapshot_erp,"
            " snapshot_maestro, result, motivo, reglas_json, creado_at)"
            " VALUES (?,'v3@abc','erp-1','m-1',?,?,?,'2026-09-19T10:00:00')",
            (doc, result, motivo, json.dumps([
                {"id": "R1_nif_iban", "veredicto": "FALLA",
                 "evidencia": {"motivo": "el IBAN no coincide"}},
                {"id": "R4_fecha", "veredicto": "PASA", "evidencia": {}},
                {"id": "R2_pedido", "veredicto": "NA",
                 "evidencia": {"faltan": ["total"]}}])))
    return c


def test_los_importes_viajan_en_centimos_enteros(con):
    """El frontend no usa Decimal: base_cent, iva_cent, total_cent."""
    e = D.expediente(con, "f1.pdf", "v3@abc")
    campos = e["extraccion"]["campos"]
    assert set(campos) == CAMPOS
    assert campos["total_cent"] == 12100 and isinstance(campos["total_cent"], int)
    assert campos["base_cent"] == 10000


def test_los_veredictos_se_traducen(con):
    """PASA/FALLA/NA aqui, CUMPLE/FALLA/SIN_DATOS en la web."""
    reglas = D.expediente(con, "f1.pdf", "v3@abc")["decision"]["reglas"]
    assert {r["regla"]: r["veredicto"] for r in reglas} == {
        "R1_nif_iban": "FALLA", "R4_fecha": "CUMPLE", "R2_pedido": "SIN_DATOS"}


def test_las_formas_son_las_que_declara_el_frontend(con):
    e = D.expediente(con, "f1.pdf", "v3@abc")
    assert set(e) == EXPEDIENTE
    assert set(e["documento"]) == DOCUMENTO
    assert set(e["extraccion"]) == EXTRACCION
    assert set(e["decision"]) == DECISION
    assert set(D.facturas(con, {"norma": "v3@abc"})[0]) == FACTURA_FILA
    assert set(D.salud(con)) == SALUD
    assert set(D.coste(con)) == COSTE
    assert set(D.baldosas(con, {"norma": "v3@abc"})[0]) == {
        "file_id", "result", "motivo"}


def test_la_web_ensena_exactamente_lo_que_se_entrega(con):
    """El invariante que justifica todo este modulo."""
    entregado = {f["file_id"]: f["result"]
                 for f in decisiones_de_lote(con, lote="lote1", norma="v3@abc")}
    en_pantalla = {b["file_id"]: b["result"]
                   for b in D.baldosas(con, {"norma": "v3@abc"})}
    assert entregado == en_pantalla


def test_la_bandeja_solo_trae_escalados_y_los_clasifica(con):
    b = D.bandeja(con, "v3@abc")
    assert len(b) == 1 and set(b[0]) == ESCALADO
    assert b[0]["file_id"] == "f2.pdf"
    assert b[0]["categoria"] == "campo_faltante"


def test_los_kpis_suman_importes_y_cuentan_los_que_no_tienen(con):
    k = D.kpis(con, "v3@abc")
    assert k["nDocs"] == 3
    por = {r["result"]: r for r in k["porResultado"]}
    assert por["PAGAR"]["total_cent"] == 12100
    assert por["PAGAR"]["sin_importe"] == 0


def test_el_diff_entre_normas_necesita_la_huella(con):
    """Solo es posible porque norma_version lleva la huella del contenido:
    antes, cambiar la politica sobrescribia la decision anterior."""
    con.execute("INSERT INTO decisiones (doc_id, norma_version, snapshot_erp,"
                " snapshot_maestro, result, motivo, reglas_json, creado_at)"
                " VALUES ('d1','v3@xyz','erp-1','m-1','ESCALAR','otra cosa',"
                " '[]','2026-09-19T11:00:00')")
    cambios = D.diff_normas(con, "v3@abc", "v3@xyz")
    assert cambios == [{"file_id": "f1.pdf", "de": "PAGAR", "a": "ESCALAR",
                        "motivo": "otra cosa"}]


def test_una_factura_que_no_existe_no_revienta(con):
    assert D.expediente(con, "no_existe.pdf", "v3@abc") is None
