"""Orquestacion de la fase 2: paginas PNG -> campos.

El lector portado devuelve bloques de texto; los campos los saca DESPUES
`extraer_campos`, las mismas 196 lineas cubiertas por los tests que ya
acertaban 468 de 471 pedidos. No se le pide al modelo un JSON de campos.

No es purismo. En los dos fallos que main midio sobre esta misma Caja, la
regex se comporto MEJOR que el interprete:
  · el NIF malformado `B9623341B` no casa con RE_NIF, asi que la regex
    devuelve None -> hueco -> escala. El interprete lo dio por bueno.
  · un IBAN completo al que el lector anadio `[unreadable]` detras: el
    interprete lo anulo; la regex lo recupera.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Sequence

from alberto.extraccion.campos import extraer_campos

from .coste import cargar_precios, coste_de_llamadas
from .portado.deepseek import ProviderError
from .portado.vision import VisionReader

# Marcas que el lector inserta donde no esta seguro. Una linea que las lleve
# NO puede usarse para un importe.
RE_INCIERTO = re.compile(r"\[(?:unreadable|illegible|ilegible|\?+)\]|\?\?\?",
                         re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Lectura:
    doc_id: str
    texto: str                       # SOLO bloques -fg-
    paginas: tuple[dict, ...] = ()
    llamadas: tuple[dict, ...] = ()  # ya sin base64
    usage: dict = field(default_factory=dict)
    modelo: str = ""
    latencia_ms: int = 0
    coste_eur: Decimal = Decimal("0")
    tokens_entrada: int = 0
    tokens_salida: int = 0


def texto_primer_plano(pagina: dict) -> str:
    """Une SOLO los bloques cuyo id contiene '-fg-'.

    Los '-bg-' son otro documento: la factura espejada que se transparenta
    en el escaneo. `VisionReader.run` devuelve una clave `text` que los une
    TODOS, y usarla significaria que el IBAN de otra factura puede acabar
    en esta. Por eso aqui no se usa esa clave nunca.
    """
    return "\n".join(
        b.get("text", "") for b in pagina.get("blocks", [])
        if "-fg-" in str(b.get("id", "")))


def _sin_lineas_inciertas(texto: str) -> str:
    """Quita las lineas con marcas de incertidumbre.

    `TOTAL: 3.0[unreadable]2,89` es el caso peligroso: RE_NUM encuentra
    ['3.0', '2,89'], _valor_de_etiqueta se queda con el ultimo y firmariamos
    un total de 2,89 EUR. Mejor ningun total que un total falso.
    """
    return "\n".join(l for l in texto.splitlines() if not RE_INCIERTO.search(l))


CAMPOS_POR_FORMA = ("pedido", "nif_emisor", "iban", "fecha", "num_factura")
CAMPOS_POR_ETIQUETA = ("base", "iva_pct", "iva_importe", "total")


def campos_de_lectura(texto: str) -> dict:
    """extraer_campos, con los importes leidos solo de lineas limpias.

    Los identificadores se buscan en el texto COMPLETO: pedido, NIF, IBAN y
    fecha tienen forma canonica y su propia regex ya hace de filtro, asi que
    una linea con ruido al lado no los corrompe. Los importes no tienen
    forma que los proteja, y ahi si se descarta la linea entera.
    """
    completo = extraer_campos(texto)
    limpio = extraer_campos(_sin_lineas_inciertas(texto))
    salida = {c: completo.get(c) for c in CAMPOS_POR_FORMA}
    salida |= {c: limpio.get(c) for c in CAMPOS_POR_ETIQUETA}
    return salida


async def leer_documento(doc_id: str, paginas_png: Sequence[bytes], cfg: dict,
                         peticion: Callable, *, precios: dict | None = None) -> Lectura:
    from .ajustes import sin_imagenes
    precios = precios or cargar_precios()
    t0 = time.monotonic()
    textos: list[str] = []
    paginas: list[dict] = []
    llamadas: list[dict] = []
    usage: dict[str, Any] = {}

    for n, png in enumerate(paginas_png, 1):
        lector = VisionReader(cfg, peticion)
        r = await lector.run(png, n)
        pagina = r["page"]
        paginas.append(pagina)
        textos.append(texto_primer_plano(pagina))
        llamadas.extend(sin_imagenes(c) for c in (r.get("raw") or {}).get("calls", []))
        for k, v in (r.get("usage") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                usage[k] = usage.get(k, 0) + v

    coste, t_ent, t_sal, _ = coste_de_llamadas(llamadas, precios)
    return Lectura(
        doc_id=doc_id, texto="\n".join(textos), paginas=tuple(paginas),
        llamadas=tuple(llamadas), usage=usage, modelo=cfg.get("vision_model", ""),
        latencia_ms=int((time.monotonic() - t0) * 1000),
        coste_eur=coste, tokens_entrada=t_ent, tokens_salida=t_sal)


async def _con_reintentos(doc_id: str, paginas: list[bytes], cfg: dict,
                          peticion: Callable, precios: dict, *,
                          reintentos: int, espera: float) -> Lectura:
    """Reintenta SOLO lo que el proveedor marca como reintentable.

    El portado ya clasifica: 429 y 5xx llevan `retryable=True`, y un JSON
    invalido o una salida truncada no. Reintentar un error de contrato es
    pagar dos veces por el mismo fallo, asi que aqui se distingue.

    Espera exponencial con tope, igual que el cliente del ERP hace con los
    ORA-00600: es el mismo problema y merece la misma respuesta.
    """
    ultimo: Exception | None = None
    for intento in range(reintentos + 1):
        try:
            return await leer_documento(doc_id, paginas, cfg, peticion,
                                        precios=precios)
        except ProviderError as exc:
            ultimo = exc
            if not getattr(exc, "retryable", False) or intento == reintentos:
                raise
            await asyncio.sleep(min(espera * 2 ** intento, 30.0))
    raise ultimo                                        # pragma: no cover


async def leer_lote(trabajos: Sequence[tuple[str, list[bytes]]], cfg: dict,
                    peticion: Callable, *, concurrencia: int = 4,
                    reintentos: int = 2, espera: float = 2.0,
                    al_terminar: Callable[[str, Any], None]) -> None:
    """Un bucle de eventos, un cliente, un semaforo de N.

    `al_terminar(doc_id, Lectura | Exception)` se invoca EN EL MISMO HILO
    segun cada documento acaba: ahi se escribe en SQLite. La conexion usa
    isolation_level=None, o sea autocommit por sentencia, asi que cada
    documento queda durable en cuanto termina. Un Ctrl-C solo pierde lo que
    estuviera en vuelo. Eso ES la reanudabilidad.
    """
    precios = cargar_precios()
    semaforo = asyncio.Semaphore(concurrencia)

    async def uno(doc_id: str, paginas: list[bytes]) -> None:
        async with semaforo:
            try:
                al_terminar(doc_id, await _con_reintentos(
                    doc_id, paginas, cfg, peticion, precios,
                    reintentos=reintentos, espera=espera))
            except Exception as exc:                    # noqa: BLE001
                al_terminar(doc_id, exc)

    await asyncio.gather(*(uno(d, p) for d, p in trabajos))
