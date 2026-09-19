"""La cascada de dos fases, sin BD, sin red y sin reloj."""
from datetime import date
from decimal import Decimal

import pytest

from alberto.contratos import FacturaExtraida
from alberto.extraccion.cascada import (aceptar, faltantes, fusionar,
                                        necesita_vision)

BASE = dict(doc_id="d", file_id="f.pdf", plantilla="", via="determinista")
NIF_BUENO = "B46102331"
NIF_MALO = "B9623341B"      # el que main leyo de verdad en scan_023


def f(**cambios) -> FacturaExtraida:
    campos = dict(BASE, nif_emisor=NIF_BUENO, iban="ES2100491500051234567890",
                  pedido="PO-2026-0096", fecha=date(2026, 1, 8),
                  base=Decimal("100.00"), iva_pct=Decimal("21"),
                  iva_importe=Decimal("21.00"), total=Decimal("121.00"))
    campos |= cambios
    x = FacturaExtraida(**campos)
    return x.__class__(**{**campos, "campos_faltantes": faltantes(x)})


def test_la_vision_no_puede_pisar_lo_que_la_regex_leyo_bien():
    """El test titular. En la medicion de main el modelo devolvio B9623341B
    -- una letra de mas -- sin marca de incertidumbre. Si la fase 2 pudiera
    sobrescribir, ese glifo desplazaria a un NIF correcto."""
    previa = f(fecha=None)
    candidata, origen = fusionar(previa, {"fecha": date(2026, 3, 1),
                                          "nif_emisor": NIF_MALO})
    assert candidata.nif_emisor == NIF_BUENO
    assert candidata.fecha == date(2026, 3, 1)
    assert origen["nif_emisor"] == "determinista"
    assert origen["fecha"] == "vision"


def test_los_nueve_inflados_no_van_a_vision():
    """Candado de la decision de diseno: la aritmetica que no cuadra con
    todos los campos presentes es un HALLAZGO. Si alguien cambia el criterio
    a 'o no cuadra la aritmetica', este test salta."""
    inflada = f(total=Decimal("999.00"))
    assert inflada.cuadra_interna() is False
    assert necesita_vision(inflada) is False


def test_faltar_un_campo_si_dispara_la_vision():
    assert necesita_vision(f(fecha=None)) is True
    assert necesita_vision(f()) is False


def test_si_no_aporta_nada_se_rechaza():
    previa = f(fecha=None)
    candidata, origen = fusionar(previa, {"num_factura": "X-1"})
    assert aceptar(previa, candidata, origen) == (False, "no_aporta_campos")


def test_importes_de_vision_que_no_cierran_se_rechazan():
    """Sobre una lectura de modelo, una cuenta que no cuadra es una lectura
    sospechosa, no un hallazgo: no hay lectura independiente que la respalde."""
    previa = f(base=None, iva_importe=None, total=None, fecha=None)
    candidata, origen = fusionar(previa, {
        "fecha": date(2026, 2, 2), "base": Decimal("100.00"),
        "iva_importe": Decimal("21.00"), "total": Decimal("999.00")})
    ok, motivo = aceptar(previa, candidata, origen)
    assert ok is False
    assert motivo.startswith("aritmetica_no_cierra")


def test_el_desajuste_heredado_del_determinista_se_conserva():
    """Si los tres importes venian de la regex, el desajuste SI es hallazgo
    y la fase 2 no debe borrarlo por rellenar otra cosa."""
    previa = f(total=Decimal("999.00"), fecha=None)
    candidata, origen = fusionar(previa, {"fecha": date(2026, 2, 2)})
    assert candidata.cuadra_interna() is False
    assert aceptar(previa, candidata, origen) == (True, "")


def test_la_via_pasa_a_vision_en_cuanto_aporta_un_campo():
    previa = f(fecha=None)
    candidata, _ = fusionar(previa, {"fecha": date(2026, 2, 2)})
    assert candidata.via == "vision"


def test_el_coste_y_la_latencia_se_acumulan():
    previa = f(fecha=None)
    candidata, _ = fusionar(previa, {"fecha": date(2026, 2, 2)},
                            coste_eur=Decimal("0.004"), latencia_ms=101_000)
    assert candidata.coste_eur == Decimal("0.004")
    assert candidata.latencia_ms == 101_000
