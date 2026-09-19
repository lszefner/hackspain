"""De tokens medidos a euros.

Dos trampas que solo se ven leyendo el proveedor:

  1. `usage` trae `total_tokens`, que YA es la suma de entrada y salida.
     Sumar "cualquier clave numerica" triplica la factura. Aqui hay lista
     blanca, no heuristica.
  2. VisionReader usa DOS modelos distintos (layout y vision) y acumula un
     UNICO dict `usage`. El desglose por modelo solo esta en `raw.calls`,
     donde cada llamada lleva su propio `request.model`.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

RUTA_PRECIOS = Path(__file__).resolve().parents[2] / "precios.yaml"
MILLON = Decimal(1_000_000)
SEIS = Decimal("0.000001")

# Lista blanca. `total_tokens` queda FUERA a proposito.
CLAVES_ENTRADA = ("prompt_tokens", "input_tokens")
CLAVES_SALIDA = ("completion_tokens", "output_tokens")


def cargar_precios(ruta: Path | None = None) -> dict:
    return yaml.safe_load((ruta or RUTA_PRECIOS).read_text("utf-8"))


def tokens_de(usage: Mapping[str, Any]) -> tuple[int, int]:
    """(entrada, salida) por lista blanca."""
    def suma(claves):
        return sum(int(usage[k]) for k in claves
                   if isinstance(usage.get(k), (int, float))
                   and not isinstance(usage.get(k), bool))
    return suma(CLAVES_ENTRADA), suma(CLAVES_SALIDA)


def tarifa(modelo: str | None, precios: dict) -> tuple[Decimal, Decimal, bool]:
    """(EUR/M entrada, EUR/M salida, es_por_defecto)."""
    tabla = precios["modelos"]
    fila = tabla.get(modelo or "")
    conocido = fila is not None
    fila = fila or tabla["por_defecto"]
    return Decimal(str(fila["entrada"])), Decimal(str(fila["salida"])), not conocido


def coste_de_usage(usage: Mapping[str, Any], modelo: str | None,
                   precios: dict) -> Decimal:
    ent, sal = tokens_de(usage)
    p_ent, p_sal = tarifa(modelo, precios)[:2]
    return ((Decimal(ent) * p_ent + Decimal(sal) * p_sal) / MILLON).quantize(SEIS)


def coste_de_llamadas(llamadas: Iterable[dict], precios: dict
                      ) -> tuple[Decimal, int, int, list[str]]:
    """(EUR, tokens_entrada, tokens_salida, modelos_sin_tarifa).

    Atribuye CADA llamada a SU modelo. Es la unica via exacta cuando el
    lector mezcla dos modelos en una sola lectura.
    """
    total, t_ent, t_sal = Decimal(0), 0, 0
    sin_tarifa: list[str] = []
    for llamada in llamadas:
        modelo = ((llamada.get("request") or {}).get("model")) or llamada.get("modelo")
        usage = (llamada.get("response") or {}).get("usage") or llamada.get("usage") or {}
        ent, sal = tokens_de(usage)
        p_ent, p_sal, por_defecto = tarifa(modelo, precios)
        if por_defecto and modelo and modelo not in sin_tarifa:
            sin_tarifa.append(modelo)
        total += (Decimal(ent) * p_ent + Decimal(sal) * p_sal) / MILLON
        t_ent += ent
        t_sal += sal
    return total.quantize(SEIS), t_ent, t_sal, sin_tarifa
