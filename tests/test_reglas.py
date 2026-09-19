"""El motor de reglas con datos sinteticos: sin ERP, sin Caja, sin red."""
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from alberto.contratos import Asiento, FacturaExtraida, Proveedor
from alberto.reglas import Motor, cargar_norma, cargar_politica

NIF, IBAN = "B46102331", "ES2100491500051234567890"
PEDIDO = "PO-2026-0096"

PROVEEDORES = {NIF: Proveedor(proveedor_id="P001", nif=NIF,
                              razon_social="Suministros Levante S.L.",
                              iban=IBAN, condiciones_dias=60)}
ASIENTOS = {PEDIDO: Asiento(asiento_id="AS-00096", pedido=PEDIDO, nif=NIF,
                            proveedor_id="P001", importe_esperado=Decimal("3012.89"),
                            estado="PENDIENTE", fecha_registro="08/01/2026")}


def motor(**kw) -> Motor:
    return Motor(cargar_norma("v3"), cargar_politica(), PROVEEDORES, ASIENTOS,
                 hoy=date(2026, 9, 20), **kw)


def factura(**cambios) -> FacturaExtraida:
    base = dict(doc_id="d", file_id="f.pdf", plantilla="p", via="determinista",
                pedido=PEDIDO, nif_emisor=NIF, iban=IBAN, fecha=date(2026, 1, 8),
                base=Decimal("2489.99"), iva_pct=Decimal("21"),
                iva_importe=Decimal("522.90"), total=Decimal("3012.89"))
    return FacturaExtraida(**(base | cambios))


def decidir(m: Motor, f: FacturaExtraida):
    return m.decidir(f, m.evaluar(f), snapshot_erp="e", snapshot_maestro="m")


def test_la_factura_limpia_se_paga():
    d = decidir(motor(), factura())
    assert d.result == "PAGAR"
    assert all(v.veredicto == "PASA" for v in d.reglas)


# --- cada regla, su fallo y su consecuencia segun politica.yaml -------------
@pytest.mark.parametrize("cambios,regla,esperado", [
    ({"iban": "ES9999999999999999999999"}, "R1_nif_iban",   "NO_PAGAR"),
    ({"nif_emisor": "B00000000"},          "R1_nif_iban",   "NO_PAGAR"),
    ({"total": Decimal("3999.99"), "iva_importe": Decimal("1510.00")},
                                            "R2_pedido",     "ESCALAR"),
    ({"pedido": "PO-2026-9999"},            "R2_pedido",     "ESCALAR"),
    ({"iva_importe": Decimal("100.00"), "total": Decimal("2589.99")},
                                            "R3_iva",        "ESCALAR"),
    ({"fecha": date(2027, 1, 1)},           "R4_fecha",      "ESCALAR"),
])
def test_cada_regla_falla_como_manda_la_politica(cambios, regla, esperado):
    d = decidir(motor(), factura(**cambios))
    assert d.result == esperado
    assert any(v.id == regla and v.veredicto == "FALLA" for v in d.reglas)
    assert d.motivo


def test_nunca_pagar_dos_veces():
    pagado = {PEDIDO: replace(ASIENTOS[PEDIDO], estado="PAGADA")}
    m = Motor(cargar_norma("v3"), cargar_politica(), PROVEEDORES, pagado,
              hoy=date(2026, 9, 20))
    d = decidir(m, factura())
    assert d.result == "NO_PAGAR"
    assert "PAGADA" in d.motivo


def test_el_motor_evalua_TODAS_las_reglas_no_corta_en_la_primera():
    d = decidir(motor(), factura(iban="ES9999999999999999999999",
                                 fecha=date(2027, 1, 1)))
    assert len(d.reglas) == len(cargar_norma("v3")["reglas"])
    fallan = {v.id for v in d.reglas if v.veredicto == "FALLA"}
    assert {"R1_nif_iban", "R4_fecha"} <= fallan


def test_datos_incompletos_escalan_nunca_se_adivina():
    d = decidir(motor(), factura(total=None, base=None, iva_importe=None))
    assert d.result == "ESCALAR"
    assert "faltan datos" in d.motivo


def test_documento_ilegible_escala_con_motivo():
    f = FacturaExtraida(doc_id="d", file_id="scan_017.pdf", plantilla="imagen",
                        via="sin_texto")
    d = decidir(motor(), f)
    assert d.result == "ESCALAR"
    assert "no se pudieron extraer" in d.motivo


def test_iva_no_estandar_escala():
    """Nota de Alberto: 'preguntar a Sonia lo del IVA reducido (aplica??)'."""
    d = decidir(motor(), factura(iva_pct=Decimal("10"),
                                 iva_importe=Decimal("249.00"),
                                 total=Decimal("2738.99")))
    assert d.result == "ESCALAR"
    assert "10" in d.motivo


def test_pedido_marcado_en_pendiente_revisar_escala():
    d = decidir(motor(revisar=frozenset({PEDIDO})), factura())
    assert d.result == "ESCALAR"
    assert "revision" in d.motivo


def test_la_politica_es_datos_se_puede_cambiar_sin_tocar_codigo():
    """El mapeo FALLA -> resultado es lo unico incierto de la validacion binaria."""
    politica = cargar_politica()
    politica["mapeo"]["R2_pedido"]["FALLA"] = "NO_PAGAR"     # antes era ESCALAR
    m = Motor(cargar_norma("v3"), politica, PROVEEDORES, ASIENTOS, hoy=date(2026, 9, 20))
    assert decidir(m, factura(pedido="PO-2026-9999")).result == "NO_PAGAR"


def test_la_norma_v4_puede_anadir_reglas_sin_tocar_codigo():
    """Prueba de que el diseno aguanta el sabado a las 18:00."""
    norma = cargar_norma("v3")
    norma["version"] = "v4"
    norma["reglas"].append({"id": "R6_vencimiento", "tipo": "vencimiento",
                            "descripcion": "Dentro del plazo del proveedor",
                            "requiere": ["fecha"], "dias_maximos": 60})
    m = Motor(norma, cargar_politica(), PROVEEDORES, ASIENTOS, hoy=date(2026, 9, 20))
    d = decidir(m, factura())          # factura de enero, hoy septiembre: fuera de plazo
    assert any(v.id == "R6_vencimiento" for v in d.reglas)
    assert d.result == "ESCALAR" and "plazo" in d.motivo
