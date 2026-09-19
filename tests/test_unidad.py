"""Tests que no necesitan ni el ERP ni La Caja. Corren en milisegundos."""
from datetime import date
from decimal import Decimal

import pytest

from alberto.contratos import Decision, FacturaExtraida, nfc
from alberto.erp.parseo import fecha_es, importe_es
from alberto.extraccion.campos import _fechas_en, _num, extraer_campos


# --- el bug fatal del prototipo anterior: RECHAZAR no existe ----------------
def _decision(result, motivo=""):
    return Decision(doc_id="d", file_id="f.pdf", result=result, motivo=motivo,
                    norma_version="v3", snapshot_erp="e", snapshot_maestro="m")


def test_rechazar_no_es_un_resultado_valido():
    with pytest.raises(ValueError, match="no es un resultado valido"):
        _decision("RECHAZAR")


@pytest.mark.parametrize("r", ["PAGAR", "NO_PAGAR", "ESCALAR"])
def test_vocabulario_completo(r):
    assert _decision(r, motivo="x").result == r


def test_escalar_exige_motivo():
    with pytest.raises(ValueError, match="motivo"):
        _decision("ESCALAR", motivo="   ")


def test_file_id_se_normaliza_a_nfc():
    nfd = "papelería.pdf"          # i + acento combinante
    assert nfc(nfd) == "papelería.pdf"
    assert len(nfc(nfd)) < len(nfd)


# --- los dos formatos numericos que conviven en la Caja ---------------------
@pytest.mark.parametrize("bruto,esperado", [
    ("1.409,40", "1409.40"),      # espanol
    ("EUR 1409.40".split()[1], "1409.40"),   # punto decimal
    ("12.874,40", "12874.40"),
    ("1.250", "1250.00"),         # punto de miles, sin decimales
    ("994,45", "994.45"),
    ("930.20", "930.20"),
])
def test_desambiguacion_de_importes(bruto, esperado):
    assert _num(bruto) == Decimal(esperado)


def test_importe_erp_rechaza_formato_anglosajon():
    assert importe_es("12.874,40") == Decimal("12874.40")
    with pytest.raises(ValueError):
        importe_es("1,250.50")


# --- los TRES formatos de fecha de la Caja ---------------------------------
@pytest.mark.parametrize("texto", [
    "Fecha: 26/01/2026", "Fecha: 2026-01-26", "Fecha de emision: 26 de enero de 2026",
])
def test_tres_formatos_de_fecha(texto):
    assert date(2026, 1, 26) in _fechas_en(texto)


def test_fecha_erp_es_dia_mes_ano():
    assert fecha_es("08/01/2026") == date(2026, 1, 8)


# --- el NIF del emisor, nunca el CIF del cliente ---------------------------
def test_no_coge_el_cif_del_banco_miralmar():
    texto = ("Suministros Levante S.L.\nNIF: B46102331\n"
             "Cliente: Banco Miralmar S.A. · CIF: A58231074\n"
             "Pedido: PO-2026-0096\nTOTAL: 3.012,89\n")
    assert extraer_campos(texto)["nif_emisor"] == "B46102331"


# --- la regresion concreta que rompia el prototipo -------------------------
@pytest.mark.parametrize("texto,esperado", [
    ("TRANSPORTES GUADAIRA\nPEDIDO CLIENTE: PO-2026-0123", "PO-2026-0123"),
    ("Limpiezas Turia\nRef. Pedido: PO-2026-0456", "PO-2026-0456"),
    ("PAPELERIA RUZAFA\nSu pedido: PO-2026-0789", "PO-2026-0789"),
])
def test_el_pedido_se_extrae_por_forma_no_por_etiqueta(texto, esperado):
    """El prototipo devolvia 'RTES', 'iezas' y 'APELER' en estos tres casos."""
    assert extraer_campos(texto)["pedido"] == esperado


# --- autovalidacion aritmetica ---------------------------------------------
def _factura(base, iva, total, pct="21"):
    return FacturaExtraida(doc_id="d", file_id="f", plantilla="p", via="determinista",
                           base=Decimal(base), iva_importe=Decimal(iva),
                           total=Decimal(total), iva_pct=Decimal(pct))


def test_aritmetica_cuadra():
    assert _factura("2489.99", "522.90", "3012.89").cuadra_interna() is True


def test_aritmetica_detecta_total_inflado():
    # caso real: 2026-0811-B_catering.pdf, 125 EUR de mas en el total
    assert _factura("2310.00", "485.10", "2920.10").cuadra_interna() is False


def test_sin_datos_no_opina():
    assert FacturaExtraida(doc_id="d", file_id="f", plantilla="p",
                           via="sin_texto").cuadra_interna() is None
