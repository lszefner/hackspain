"""La cascada de dos fases: que documento pasa a la fase 2, como se fusiona
el resultado y cuando se acepta.

Logica PURA: sin BD, sin red, sin reloj. Todo lo de aqui se testea con datos
en memoria, y por eso las tres decisiones dificiles del diseno viven aqui y
no repartidas por el pipeline.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from alberto.contratos import FacturaExtraida

INTENTO_DETERMINISTA = 1
INTENTO_VISION = 2

# Los campos que exigen las reglas de norma_v3.yaml (su campo `requiere`).
CAMPOS_EXIGIDOS: tuple[str, ...] = ("pedido", "nif_emisor", "iban", "fecha", "total")
# Los que forman la identidad contable base + IVA == total.
CAMPOS_ARITMETICOS: tuple[str, ...] = ("base", "iva_importe", "total")
# Los que la fase 2 puede aportar.
CAMPOS_FUSIONABLES: tuple[str, ...] = (
    "num_factura", "fecha", "pedido", "nif_emisor", "iban",
    "base", "iva_pct", "iva_importe", "total")


def faltantes(f: FacturaExtraida) -> tuple[str, ...]:
    """Los CAMPOS_EXIGIDOS que estan a None. Un unico sitio donde se define
    que es una extraccion incompleta."""
    return tuple(c for c in CAMPOS_EXIGIDOS if getattr(f, c, None) is None)


def necesita_vision(f: FacturaExtraida) -> bool:
    """El criterio de la cascada: faltan campos.

    NO se dispara porque la aritmetica no cuadre. Medido sobre La Caja: los
    9 documentos cuya aritmetica falla tienen TODOS los campos extraidos y
    los 9 fallan tambien contra el importe del ERP. Son facturas infladas
    -- el hallazgo -- y no fallos de lectura. Mandarlas a un modelo seria
    pedirle que borre el hallazgo.
    """
    return bool(faltantes(f))


def fusionar(previa: FacturaExtraida, campos: dict, *,
             coste_eur: Decimal = Decimal("0"), latencia_ms: int = 0,
             modelo: str = "") -> tuple[FacturaExtraida, dict[str, str]]:
    """La fase 2 SOLO rellena huecos: nunca pisa un valor que el
    determinista ya leyo.

    Es la barandilla principal de todo esto. En la medicion de main un NIF
    bien impreso salio del modelo como `B9623341B`, con una letra de mas y
    sin ninguna marca de incertidumbre: si la vision pudiera sobrescribir,
    ese glifo habria desplazado a un NIF correcto.

    Devuelve la candidata y el mapa {campo: 'determinista'|'vision'}.
    Por construccion, faltantes(candidata) es subconjunto de faltantes(previa).
    """
    cambios: dict[str, object] = {}
    origen: dict[str, str] = {}
    for campo in CAMPOS_FUSIONABLES:
        actual = getattr(previa, campo, None)
        if actual is not None:
            origen[campo] = "determinista"
            continue
        nuevo = campos.get(campo)
        if nuevo is not None:
            cambios[campo] = nuevo
            origen[campo] = "vision"

    candidata = replace(
        previa, **cambios,
        via="vision" if cambios else previa.via,
        coste_eur=previa.coste_eur + coste_eur,
        latencia_ms=previa.latencia_ms + latencia_ms)
    candidata = replace(candidata, campos_faltantes=faltantes(candidata))
    return candidata, origen


def aceptar(previa: FacturaExtraida, candidata: FacturaExtraida,
            origen: dict[str, str]) -> tuple[bool, str]:
    """Regla de aceptacion de la fase 2. Devuelve (aceptada, motivo_rechazo).

    1. Si no rellena ningun hueco -> se rechaza. No se paga una llamada para
       reescribir lo que ya teniamos.
    2. Si la aritmetica NO cierra y algun importe lo aporto la vision -> se
       rechaza. Sobre una lectura de modelo, una cuenta que no cuadra es una
       lectura sospechosa y no un hallazgo: no hay una lectura independiente
       que la respalde. El documento se queda con la fase 1 y escala con
       motivo, que es exactamente el criterio de T02: las ilegibles escalan
       con motivo, no con un numero inventado.
       Si los tres importes venian del determinista, el desajuste SI es
       hallazgo y se conserva.
    3. En cualquier otro caso, se acepta.
    """
    if len(faltantes(candidata)) >= len(faltantes(previa)):
        return False, "no_aporta_campos"

    if candidata.cuadra_interna() is False:
        de_vision = [c for c in CAMPOS_ARITMETICOS if origen.get(c) == "vision"]
        if de_vision:
            return False, "aritmetica_no_cierra:" + ",".join(de_vision)

    return True, ""
