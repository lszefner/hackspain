"""La frontera con los datos. Lee de disco; no habla con nadie.

Antes esto abria `alberto.db`. Ese paquete ya no existe: el motor de decision
vive sobre Supabase y es la unica fuente de verdad. Pero Supabase no sirve
para atender una llamada -- ni por red (el puerto de Postgres esta cerrado en
la sala) ni por diseno (el motor no se puede consultar por numero de pedido,
que es lo unico que el proveedor dice en voz alta).

Asi que `phone_calls/exportar.py` hace el trabajo antes, una vez, y deja en
`datos/` un expediente por pedido con la decision del motor, su evidencia
regla a regla y los datos maestros que hagan falta. Aqui solo se lee eso.

La consecuencia buena: durante una llamada no hay red, ni credenciales, ni
base de datos, ni nada que se pueda caer a mitad de frase.
"""
from __future__ import annotations

import json
from pathlib import Path

DATOS = Path(__file__).parent / "datos"
RE_PEDIDO_JSON = "PO-*.json"


class SinDatos(RuntimeError):
    """No hay export. Se resuelve corriendo `make export`."""


def _leer(f: Path) -> dict:
    return json.loads(f.read_text(encoding="utf-8"))


def cargar(datos: Path = DATOS) -> dict:
    """Todo el export en memoria. Son tres ficheros: cabe de sobra.

    Se carga entero al arrancar en vez de por llamada porque asi un fichero
    corrupto se descubre al levantar el servidor y no en mitad de un turno.
    """
    if not datos.is_dir():
        raise SinDatos(f"no existe {datos}. Corre `make export` primero.")
    expedientes = {}
    for f in sorted(datos.glob(RE_PEDIDO_JSON)):
        exp = _leer(f)
        if exp.get("pedido"):
            expedientes[exp["pedido"]] = exp
    maestro_f = datos / "maestro.json"
    maestro = _leer(maestro_f) if maestro_f.is_file() else {"proveedores": [], "notas": {}}
    if not expedientes:
        raise SinDatos(f"no hay expedientes en {datos}. Corre `make export`.")
    return {"expedientes": expedientes, "maestro": maestro}


# ------------------------------------------------------------ consultas
def proveedores(caja: dict) -> dict[str, dict]:
    """NIF -> proveedor. `condiciones` viene del Excel como texto ('60 dias')."""
    return {p["nif"]: p for p in caja["maestro"].get("proveedores", []) if p.get("nif")}


def condiciones_dias(proveedor: dict | None) -> int | None:
    """'60 dias' -> 60. El Excel no guarda un entero."""
    if not proveedor:
        return None
    from rules_ingestion.normalize import payment_terms_days
    return payment_terms_days(proveedor.get("condiciones"))


def expediente_de_pedido(caja: dict, pedido: str) -> dict | None:
    return caja["expedientes"].get(pedido)


def pedidos(caja: dict) -> list[str]:
    return sorted(caja["expedientes"])


def notas_generales(caja: dict) -> list[str]:
    """Lo que Alberto apunto a mano en el Excel."""
    return list((caja["maestro"].get("notas") or {}).get("generales") or [])


def nif_del_pedido(exp: dict) -> str | None:
    """De quien es el pedido, para no dar datos de un tercero.

    Se prefiere el maestro sobre el NIF leido de la factura: la extraccion
    puede equivocarse en un digito de control, y de hecho se equivoca.
    """
    return ((exp.get("pedido_maestro") or {}).get("nif")
            or (exp.get("asiento") or {}).get("nif")
            or (exp.get("proveedor") or {}).get("nif"))
