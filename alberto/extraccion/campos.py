"""Extractor guiado por formato y por etiqueta.

Por que no el enfoque del ejemplo que nos pasaron: su regex de pedido era
`(?:Pedido|PO|P\\.O\\.|P)[:\\s#]*([A-Z0-9\\-_]+)` con IGNORECASE, y esa `P`
suelta casa dentro de cualquier palabra: "TRANSPORTES" -> 'RTES',
"Limpiezas" -> 'iezas', "PEDIDO CLIENTE:" -> 'CLIENTE'. 252 de 471 mal.

Aqui cada campo se busca por su FORMA cuando la tiene (el pedido siempre es
PO-AAAA-NNNN) y por etiqueta anclada a linea cuando no. Nunca por subcadena
suelta.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from alberto.contratos import CENTIMO

CIF_CLIENTE = "A58231074"          # Banco Miralmar: el pagador, nunca el emisor

RE_PEDIDO = re.compile(r"\bPO-\d{4}-\d{4}\b")
RE_NIF = re.compile(r"\b(?:[A-Z]\d{8}|\d{8}[A-Z])\b")
RE_IBAN = re.compile(r"\bES\d{2}(?:[ ]?\d{4}){5}\b")
RE_IVA_PCT = re.compile(r"IVA[^\n%]{0,20}?(\d{1,2})\s*%", re.IGNORECASE)
RE_FECHA = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
RE_FECHA_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# Tercer formato de la Caja: "15 de enero de 2026" (93 facturas).
MESES = {m: i for i, m in enumerate(
    ("enero febrero marzo abril mayo junio julio agosto septiembre "
     "octubre noviembre diciembre").split(), 1)}
MESES["setiembre"] = 9
RE_FECHA_LARGA = re.compile(
    r"\b(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})\b", re.IGNORECASE)
# La Caja mezcla DOS formatos numericos en el mismo corpus:
#   "1.409,40" (espanol) y "EUR 1409.40" (punto decimal, 91 facturas).
# Se captura cualquier token numerico y se desambigua en _num().
RE_NUM = re.compile(r"-?\d[\d.,]*")

SINONIMOS: dict[str, tuple[str, ...]] = {
    "base": ("base imponible", "importe base", "base", "subtotal", "suma y sigue"),
    "iva_importe": ("iva", "i.v.a", "cuota iva"),
    "total": ("total a pagar", "importe total", "total factura", "total"),
    "fecha": ("fecha factura", "fecha de emision", "fecha emision", "fecha"),
    "num_factura": ("factura n", "n de factura", "ref factura", "factura"),
}


def _plano(texto: str) -> str:
    """Minusculas sin acentos: las etiquetas cambian de caja y de tilde."""
    sin = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sin if not unicodedata.combining(c))


def _num(bruto: str) -> Decimal | None:
    """Desambigua '1.409,40', '1409.40' y '1.250' sin suponer la localizacion.

    Regla: el ULTIMO separador es decimal solo si le siguen 1 o 2 digitos.
    Con 3 digitos detras era separador de miles.
    """
    s = (bruto or "").strip().rstrip(".,")
    if not re.fullmatch(r"-?[\d.,]+", s) or not any(c.isdigit() for c in s):
        return None
    corte = max(s.rfind("."), s.rfind(","))
    if corte == -1:
        entero, decimales = s, ""
    else:
        cola = s[corte + 1:]
        if cola.isdigit() and len(cola) in (1, 2):
            entero, decimales = s[:corte], cola
        else:
            entero, decimales = s, ""
    entero = entero.replace(".", "").replace(",", "")
    if not entero.lstrip("-").isdigit():
        return None
    try:
        return Decimal(f"{entero}.{decimales or '0'}").quantize(CENTIMO)
    except (InvalidOperation, ValueError):
        return None


def _valor_de_etiqueta(lineas: list[str], claves: tuple[str, ...]) -> Decimal | None:
    """Ultimo importe de la linea cuya etiqueta empieza por una de las claves.

    Se ancla al COMIENZO de la etiqueta dentro de la linea y se recorre en el
    orden de `claves`, de mas especifica a menos: asi 'total a pagar' gana a
    'total' y 'base imponible' a 'base'.
    """
    for clave in claves:
        for linea in lineas:
            plano = _plano(linea)
            pos = plano.find(clave)
            if pos == -1:
                continue
            # La etiqueta debe abrir la linea o ir tras un separador, no dentro
            # de otra palabra ("Subtotal" no debe activar "total").
            if pos > 0 and (plano[pos - 1].isalnum() or plano[pos - 1] in "._"):
                continue
            resto = RE_FECHA.sub(" ", linea[pos:])   # una fecha no es un importe
            numeros = RE_NUM.findall(resto)
            if numeros:
                valor = _num(numeros[-1])
                if valor is not None:
                    return valor
    return None


def _fechas_en(texto: str) -> list[date]:
    """La Caja usa TRES formatos: 26/01/2026, 2026-01-26 y '15 de enero de 2026'."""
    salida: list[date] = []
    for d, m, a in RE_FECHA.findall(texto):
        try:
            salida.append(date(int(a), int(m), int(d)))
        except ValueError:
            pass
    for a, m, d in RE_FECHA_ISO.findall(texto):
        try:
            salida.append(date(int(a), int(m), int(d)))
        except ValueError:
            pass
    for d, mes, a in RE_FECHA_LARGA.findall(texto):
        numero = MESES.get(_plano(mes))
        if numero:
            try:
                salida.append(date(int(a), numero, int(d)))
            except ValueError:
                pass
    return salida


def _fecha(lineas: list[str]) -> date | None:
    for clave in SINONIMOS["fecha"]:
        for linea in lineas:
            if clave in _plano(linea):
                encontradas = _fechas_en(linea)
                if encontradas:
                    return encontradas[0]
    todas = _fechas_en("\n".join(lineas))
    return todas[0] if todas else None


def _nif_emisor(texto: str, lineas: list[str]) -> str | None:
    """El NIF del emisor, nunca el CIF del cliente."""
    for linea in lineas:
        plano = _plano(linea)
        if "cif" in plano and "cliente" in plano:
            continue                      # la linea del pagador se ignora entera
        if "nif" in plano or "cif" in plano:
            for cand in RE_NIF.findall(linea):
                if cand != CIF_CLIENTE:
                    return cand
    for cand in RE_NIF.findall(texto):
        if cand != CIF_CLIENTE:
            return cand
    return None


def extraer_campos(texto: str) -> dict:
    lineas = [l for l in texto.splitlines() if l.strip()]
    plano_total = _plano(texto)

    pedidos = RE_PEDIDO.findall(texto)
    ibans = RE_IBAN.findall(texto)
    iva_pct = RE_IVA_PCT.search(texto)

    base = _valor_de_etiqueta(lineas, SINONIMOS["base"])
    total = _valor_de_etiqueta(lineas, SINONIMOS["total"])
    iva_imp = _valor_de_etiqueta(lineas, SINONIMOS["iva_importe"])

    # Red de seguridad aritmetica: si falta una pata y las otras dos estan,
    # se deduce. No es adivinar: es cerrar una identidad contable.
    if base is not None and iva_imp is not None and total is None:
        total = base + iva_imp
    elif base is not None and total is not None and iva_imp is None:
        iva_imp = total - base
    elif iva_imp is not None and total is not None and base is None:
        base = total - iva_imp

    return {
        "pedido": pedidos[0] if pedidos else None,
        "nif_emisor": _nif_emisor(texto, lineas),
        "iban": ibans[0].replace(" ", "") if ibans else None,
        "fecha": _fecha(lineas),
        "base": base,
        "iva_pct": Decimal(iva_pct.group(1)) if iva_pct else None,
        "iva_importe": iva_imp,
        "total": total,
        "_plantilla": huella(plano_total),
    }


def huella(plano: str) -> str:
    """Identificador estable de maqueta, para medir cobertura por plantilla."""
    etiquetas = sorted({m for m in re.findall(r"(?m)^\s*([a-z. º]{3,22}?)\s*:", plano)})
    return "|".join(etiquetas[:4]) or "sin-etiquetas"
