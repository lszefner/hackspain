"""Los contratos entre etapas. CONGELADO: cambios solo por acuerdo de los 4.

Tres invariantes que no se negocian:
  1. Decimal en todo importe. Nunca float.
  2. unicodedata.normalize("NFC", file_id) al escribir el JSONL.
  3. doc_id = sha256(fichero) como unica clave de idempotencia.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

Resultado = Literal["PAGAR", "NO_PAGAR", "ESCALAR"]
RESULTADOS_VALIDOS: frozenset[str] = frozenset({"PAGAR", "NO_PAGAR", "ESCALAR"})

Via = Literal["determinista", "ocr", "vision", "sin_texto"]
Veredicto = Literal["PASA", "FALLA", "NA"]

CENTIMO = Decimal("0.01")


def nfc(nombre: str) -> str:
    """Normaliza a NFC. macOS puede devolver NFD al listar directorios y el
    verificador privado compara cadenas: un NFD silencioso suspende la entrega."""
    return unicodedata.normalize("NFC", nombre)


@dataclass(frozen=True, slots=True)
class Documento:
    doc_id: str          # sha256 del contenido
    file_id: str         # nombre EXACTO del fichero, NFC
    ruta: Path
    bytes: int
    tiene_texto: bool    # decidido por contenido, NUNCA por el nombre
    lote: str = "lote1"


@dataclass(frozen=True, slots=True)
class FacturaExtraida:
    doc_id: str
    file_id: str
    plantilla: str
    via: Via
    num_factura: str | None = None
    fecha: date | None = None
    pedido: str | None = None
    nif_emisor: str | None = None
    iban: str | None = None
    base: Decimal | None = None
    iva_pct: Decimal | None = None
    iva_importe: Decimal | None = None
    total: Decimal | None = None
    campos_faltantes: tuple[str, ...] = ()
    coste_eur: Decimal = Decimal("0")
    latencia_ms: int = 0

    # --- autovalidacion: una factura se comprueba a si misma -----------------
    def cuadra_interna(self) -> bool | None:
        """base + iva == total  y  base * iva_pct == iva. None si faltan datos."""
        if self.base is None or self.iva_importe is None or self.total is None:
            return None
        if abs(self.base + self.iva_importe - self.total) > CENTIMO:
            return False
        if self.iva_pct is not None:
            esperado = (self.base * self.iva_pct / Decimal(100)).quantize(CENTIMO)
            if abs(esperado - self.iva_importe) > CENTIMO:
                return False
        return True


@dataclass(frozen=True, slots=True)
class VeredictoRegla:
    id: str
    veredicto: Veredicto
    evidencia: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Decision:
    doc_id: str
    file_id: str
    result: Resultado
    motivo: str
    norma_version: str
    snapshot_erp: str
    snapshot_maestro: str
    reglas: tuple[VeredictoRegla, ...] = ()
    coste_eur: Decimal = Decimal("0")
    latencia_ms: int = 0

    def __post_init__(self) -> None:
        if self.result not in RESULTADOS_VALIDOS:
            raise ValueError(
                f"{self.result!r} no es un resultado valido. "
                f"Solo {sorted(RESULTADOS_VALIDOS)}"
            )
        if self.result == "ESCALAR" and not self.motivo.strip():
            raise ValueError("La regla 6 exige motivo al escalar")


@dataclass(frozen=True, slots=True)
class Asiento:
    asiento_id: str
    pedido: str
    nif: str
    proveedor_id: str
    importe_esperado: Decimal
    estado: str          # PENDIENTE | PAGADA
    fecha_registro: str


@dataclass(frozen=True, slots=True)
class Proveedor:
    proveedor_id: str
    nif: str
    razon_social: str
    iban: str
    condiciones_dias: int | None = None   # candidato a regla v4
