"""Configuracion y transporte HTTP de la fase 2.

Los NOMBRES de las variables de entorno son identicos a los de la rama main
a proposito: el `.env` que ya existe alli funciona aqui sin editar una linea.
"""
from __future__ import annotations

import copy
import os
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

CLAVES_VISION = ("helmcode_base_url", "api_key", "vision_layout_model",
                 "vision_model", "vision_verify")


class ProveedorNoConfigurado(RuntimeError):
    """Falta una variable de entorno. Mejor esto que una llamada a ciegas
    contra una URL vacia que tarda 30 s en fallar."""


def _url_valida(url: str) -> str:
    u = urlparse(url)
    if u.scheme != "https" or not u.netloc or u.query or u.fragment or "@" in u.netloc:
        raise ProveedorNoConfigurado(
            f"HELMCODE_BASE_URL debe ser https sin credenciales ni query: {url!r}")
    return url.rstrip("/")


def ajustes(*, verificar: bool = False, interpretar: bool = False) -> dict:
    """Las claves que pide VisionReader.

    `verificar` va a False por defecto: activarlo pasa de 3 llamadas por
    pagina a hasta 7, y las mediciones de main que dieron 19-20/20 sobre los
    escaneos dificiles no lo usaron. El filtro aritmetico hace de red.
    """
    base = os.environ.get("HELMCODE_BASE_URL", "")
    clave = os.environ.get("HELMCODE_API_KEY", "")
    if not base or not clave:
        raise ProveedorNoConfigurado(
            "faltan HELMCODE_BASE_URL y/o HELMCODE_API_KEY. "
            "Copia .env.example a .env y rellenalas.")
    cfg: dict[str, Any] = {
        "helmcode_base_url": _url_valida(base),
        "api_key": clave,
        "vision_model": os.environ.get("HELMCODE_VISION_MODEL", "gemma4"),
        "vision_layout_model": os.environ.get("HELMCODE_LAYOUT_MODEL", "qwen3.6"),
        "vision_verify": bool(verificar),
    }
    if interpretar:
        modelo = os.environ.get("HELMCODE_DEEPSEEK_MODEL", "")
        if not modelo:
            raise ProveedorNoConfigurado(
                "--interpretar necesita HELMCODE_DEEPSEEK_MODEL")
        cfg["deepseek_model"] = modelo
    return cfg


def cliente_http(timeout: float = 180.0) -> httpx.AsyncClient:
    """180 s: en main una lectura completa tardo ~88 s repartidos en varias
    llamadas, y la de layout sobre una pagina densa es la mas lenta."""
    return httpx.AsyncClient(timeout=timeout)


def peticion_de(cliente: httpx.AsyncClient) -> Callable:
    """El callback que espera `_invoke_request`.

    Los parametros se llaman method/url/headers/body A PROPOSITO: si el
    tercero se llamara `json`, inspect.signature haria que el portado
    cambiara de convencion de llamada y empezaria a pasar keywords.
    """
    async def peticion(method, url, headers, body):
        return await cliente.request(method, url, headers=headers, json=body)
    return peticion


def sin_imagenes(llamada: dict) -> dict:
    """Sustituye cada data:image/...;base64 del cuerpo por 'sha256:<hash>'.

    Sin esto, cada pagina son decenas de MB de base64 repetido en la capa
    raw: el cuerpo lleva la imagen completa y hay varias llamadas y varias
    vistas por llamada. El hash ya viene calculado en `image_sha256`.
    """
    limpia = copy.deepcopy(llamada)
    hashes = list(limpia.get("image_sha256") or [])
    vistos = 0
    for mensaje in (limpia.get("request") or {}).get("messages") or []:
        contenido = mensaje.get("content")
        if not isinstance(contenido, list):
            continue
        for parte in contenido:
            url = (parte.get("image_url") or {}).get("url", "") if isinstance(parte, dict) else ""
            if isinstance(url, str) and url.startswith("data:"):
                sha = hashes[vistos] if vistos < len(hashes) else "desconocido"
                parte["image_url"]["url"] = f"sha256:{sha}"
                vistos += 1
    return limpia
