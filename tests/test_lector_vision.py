"""El lector de la fase 2: las dos trampas que romperian todo en silencio."""
from decimal import Decimal

from alberto.extraccion.vision.ajustes import sin_imagenes
from alberto.extraccion.vision.coste import (cargar_precios, coste_de_llamadas,
                                             tokens_de)
from alberto.extraccion.vision.lector import (campos_de_lectura,
                                              texto_primer_plano)

IBAN_BUENO = "ES2100491500051234567890"
IBAN_FONDO = "ES9900000000000000000000"


def test_el_documento_de_fondo_no_aporta_campos():
    """Los bloques -bg- son OTRO documento: la factura espejada que se
    transparenta en el escaneo. `VisionReader.run` devuelve una clave `text`
    que los une todos; usarla meteria el IBAN de otra factura en esta."""
    pagina = {"blocks": [
        {"id": "p1-fg-b1", "text": f"Pedido PO-2026-0007  IBAN {IBAN_BUENO}"},
        {"id": "p1-bg-b1", "text": f"IBAN {IBAN_FONDO}  Pedido PO-2026-9999"}]}
    texto = texto_primer_plano(pagina)
    assert IBAN_BUENO in texto
    assert IBAN_FONDO not in texto
    assert campos_de_lectura(texto)["iban"] == IBAN_BUENO


def test_unreadable_dentro_de_un_importe_no_produce_un_total_falso():
    """'TOTAL: 3.0[unreadable]2,89' -> RE_NUM ve ['3.0','2,89'] y el
    extractor se quedaria con el ultimo: 2,89 EUR. Mejor ningun total."""
    campos = campos_de_lectura("Base imponible: 2.050,00\n"
                               "TOTAL: 3.0[unreadable]2,89")
    assert campos["total"] is None
    assert campos["base"] == Decimal("2050.00")


def test_los_identificadores_se_rescatan_aunque_la_linea_tenga_ruido():
    """Pedido, NIF, IBAN y fecha tienen forma canonica: su regex ya es el
    filtro, asi que una marca al lado no los corrompe."""
    campos = campos_de_lectura(f"NIF: B46102331 [unreadable]\nIBAN {IBAN_BUENO}")
    assert campos["nif_emisor"] == "B46102331"
    assert campos["iban"] == IBAN_BUENO


def test_el_nif_malformado_del_modelo_se_descarta_por_forma():
    """B9623341B es lo que main leyo de verdad. RE_NIF no casa: hueco, y el
    documento escala. Correcto, y sin que nadie lo programara para eso."""
    assert campos_de_lectura("NIF: B9623341B")["nif_emisor"] is None


def test_las_imagenes_no_se_persisten_en_base64():
    llamada = {"request": {"model": "gemma4", "messages": [{"content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 50_000}}]}]},
        "image_sha256": ["abc123"]}
    limpia = sin_imagenes(llamada)
    url = limpia["request"]["messages"][0]["content"][0]["image_url"]["url"]
    assert url == "sha256:abc123"
    assert "A" * 100 not in str(limpia)


def test_el_coste_no_cuenta_dos_veces_los_tokens():
    """`usage` trae total_tokens, que YA es la suma. Sumar 'cualquier clave
    numerica' triplicaria la factura, y esa cifra iria a la diapositiva de
    escalabilidad."""
    usage = {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}
    assert tokens_de(usage) == (1000, 500)


def test_cada_llamada_se_atribuye_a_su_modelo():
    """El lector usa dos modelos (layout y vision) y acumula un unico dict
    usage: el desglose exacto solo esta en raw.calls."""
    precios = cargar_precios()
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
    llamadas = [{"request": {"model": "qwen3.6"}, "response": {"usage": usage}},
                {"request": {"model": "gemma4"}, "response": {"usage": usage}}]
    coste, ent, sal, sin_tarifa = coste_de_llamadas(llamadas, precios)
    assert (ent, sal) == (2_000_000, 0)
    assert coste == Decimal("0.230000")      # 0,09 + 0,14
    assert sin_tarifa == []


def test_un_modelo_sin_tarifa_se_sobreestima_y_se_avisa():
    precios = cargar_precios()
    llamadas = [{"request": {"model": "modelo-nuevo"},
                 "response": {"usage": {"prompt_tokens": 1_000_000}}}]
    coste, _, _, sin_tarifa = coste_de_llamadas(llamadas, precios)
    assert sin_tarifa == ["modelo-nuevo"]
    assert coste == Decimal("0.500000")
