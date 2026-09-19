"""Parseo del XML del bridge de 2009. Funciones puras: se testean sin red."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from alberto.contratos import Asiento

_IMPORTE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^-?\d+(?:,\d+)?$")


def importe_es(valor: str) -> Decimal:
    """'12.874,40' -> Decimal('12874.40'). Punto de miles, coma decimal."""
    v = (valor or "").strip().replace("\xa0", "").replace(" ", "").replace("€", "")
    if not _IMPORTE.match(v):
        raise ValueError(f"importe con formato inesperado: {valor!r}")
    try:
        return Decimal(v.replace(".", "").replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"importe no convertible: {valor!r}") from exc


def fecha_es(valor: str) -> date:
    """'08/01/2026' -> date(2026,1,8). El bridge siempre usa DD/MM/AAAA."""
    d, m, a = (valor or "").strip().split("/")
    return date(int(a), int(m), int(d))


def _raiz(payload: bytes | str) -> ET.Element:
    # Se parsea desde BYTES: el XML declara ISO-8859-1 y decodificarlo antes
    # es como se cuelan las mojibake.
    return ET.fromstring(payload if isinstance(payload, bytes) else payload.encode("iso-8859-1"))


def meta_de_xml(payload: bytes) -> dict[str, int]:
    """El <meta> trae total y paginas: es nuestra comprobacion de integridad."""
    meta = _raiz(payload).find("meta")
    if meta is None:
        return {}
    salida: dict[str, int] = {}
    for hijo in meta:
        if hijo.text and hijo.text.strip().isdigit():
            salida[hijo.tag] = int(hijo.text.strip())
    return salida


def asientos_de_xml(payload: bytes) -> list[Asiento]:
    filas = []
    for nodo in _raiz(payload).iter("asiento"):
        get = lambda t: (nodo.findtext(t) or "").strip()  # noqa: E731
        filas.append(
            Asiento(
                asiento_id=get("id"),
                pedido=get("pedido"),
                nif=get("nif"),
                proveedor_id=get("proveedor"),
                importe_esperado=importe_es(get("importe")),
                estado=get("estado"),
                fecha_registro=get("fecha"),
            )
        )
    return filas


def token_de_xml(payload: bytes) -> str:
    tok = _raiz(payload).findtext("token")
    if not tok:
        raise ValueError("el bridge no devolvio token")
    return tok.strip()
