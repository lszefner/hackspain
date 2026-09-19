"""Motor de reglas. Dos responsabilidades separadas a proposito:

  1. evaluar()  -> que dice CADA regla (PASA / FALLA / NA) con su evidencia
  2. decidir()  -> que decidimos con ese conjunto de veredictos

La separacion es lo que permite cambiar la politica sin re-extraer nada, y
migrar a la norma v4 del sabado editando un YAML.

El motor evalua TODAS las reglas, no corta en la primera que falla: la traza
completa vale mas que el atajo.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from alberto.contratos import (Asiento, Decision, FacturaExtraida, Proveedor,
                               Resultado, VeredictoRegla)

RAIZ = Path(__file__).parent


def cargar_norma(version: str = "v3") -> dict:
    return yaml.safe_load((RAIZ / f"norma_{version}.yaml").read_text("utf-8"))


def cargar_politica(ruta: Path | None = None) -> dict:
    return yaml.safe_load((ruta or RAIZ / "politica.yaml").read_text("utf-8"))


class Motor:
    def __init__(self, norma: dict, politica: dict,
                 proveedores: dict[str, Proveedor], asientos: dict[str, Asiento],
                 *, revisar: frozenset[str] = frozenset(), hoy: date | None = None):
        self.norma, self.politica = norma, politica
        self.proveedores, self.asientos = proveedores, asientos
        self.revisar = revisar
        self.hoy = hoy or date.today()

    # ------------------------------------------------------------- evaluacion
    def _una(self, r: dict, f: FacturaExtraida) -> VeredictoRegla:
        rid, tipo = r["id"], r["tipo"]
        faltan = [c for c in r.get("requiere", []) if getattr(f, c, None) is None]
        if faltan:
            return VeredictoRegla(rid, "NA", {"faltan": faltan})

        tol = Decimal(r.get("tolerancia", "0.01"))
        ev: dict[str, Any] = {}

        if tipo == "nif_iban":
            prov = self.proveedores.get(f.nif_emisor or "")
            ev["nif"] = f.nif_emisor
            if prov is None:
                return VeredictoRegla(rid, "FALLA", ev | {"motivo": "NIF no esta en el maestro"})
            ev |= {"iban_factura": f.iban, "iban_maestro": prov.iban,
                   "proveedor": prov.razon_social}
            coincide = (f.iban or "").replace(" ", "") == prov.iban.replace(" ", "")
            return VeredictoRegla(rid, "PASA" if coincide else "FALLA",
                                  ev if coincide else ev | {"motivo": "el IBAN no coincide con el maestro"})

        if tipo == "pedido_importe":
            asi = self.asientos.get(f.pedido or "")
            ev["pedido"] = f.pedido
            if asi is None:
                return VeredictoRegla(rid, "FALLA", ev | {"motivo": "el pedido no consta en el ERP"})
            importe = getattr(f, r.get("campo_importe", "total"))
            ev |= {"importe_factura": str(importe), "importe_erp": str(asi.importe_esperado),
                   "nif_erp": asi.nif}
            if f.nif_emisor and asi.nif and f.nif_emisor != asi.nif:
                return VeredictoRegla(rid, "FALLA", ev | {"motivo": "el pedido es de otro proveedor"})
            desvio = abs(importe - asi.importe_esperado)
            ev["desvio"] = str(desvio)
            return VeredictoRegla(rid, "PASA" if desvio <= tol else "FALLA",
                                  ev if desvio <= tol else ev | {"motivo": "el importe no coincide con el pedido"})

        if tipo == "iva":
            ev |= {"base": str(f.base), "iva": str(f.iva_importe), "total": str(f.total)}
            if abs(f.base + f.iva_importe - f.total) > tol:
                return VeredictoRegla(rid, "FALLA", ev | {"motivo": "el total no es base mas IVA"})
            if f.iva_pct is not None:
                esperado = (f.base * f.iva_pct / Decimal(100)).quantize(tol)
                ev |= {"iva_pct": str(f.iva_pct), "iva_esperado": str(esperado)}
                if abs(esperado - f.iva_importe) > tol:
                    return VeredictoRegla(rid, "FALLA", ev | {"motivo": "el IVA esta mal calculado"})
            return VeredictoRegla(rid, "PASA", ev)

        if tipo == "fecha":
            ev["fecha"] = str(f.fecha)
            if f.fecha > self.hoy:
                return VeredictoRegla(rid, "FALLA", ev | {"motivo": "la fecha es futura"})
            return VeredictoRegla(rid, "PASA", ev)

        if tipo == "estado_erp":
            asi = self.asientos.get(f.pedido or "")
            if asi is None:
                return VeredictoRegla(rid, "NA", {"motivo": "el pedido no consta en el ERP"})
            ev["estado"] = asi.estado
            ok = asi.estado == r.get("estado_pagable", "PENDIENTE")
            return VeredictoRegla(rid, "PASA" if ok else "FALLA",
                                  ev if ok else ev | {"motivo": f"el pedido ya esta {asi.estado}"})

        if tipo == "vencimiento":            # preparado para la norma v4
            prov = self.proveedores.get(f.nif_emisor or "")
            dias = prov.condiciones_dias if prov else None
            if dias is None or f.fecha is None:
                return VeredictoRegla(rid, "NA", {"motivo": "sin condiciones de pago"})
            edad = (self.hoy - f.fecha).days
            ev |= {"dias_condiciones": dias, "dias_transcurridos": edad}
            maximo = int(r.get("dias_maximos", dias))
            return VeredictoRegla(rid, "PASA" if edad <= maximo else "FALLA",
                                  ev if edad <= maximo else ev | {"motivo": "factura fuera de plazo"})

        return VeredictoRegla(rid, "NA", {"motivo": f"tipo de regla desconocido: {tipo}"})

    def evaluar(self, f: FacturaExtraida) -> list[VeredictoRegla]:
        return [self._una(r, f) for r in self.norma["reglas"]]

    # -------------------------------------------------------------- decision
    def decidir(self, f: FacturaExtraida, veredictos: list[VeredictoRegla],
                *, snapshot_erp: str, snapshot_maestro: str) -> Decision:
        pol = self.politica
        motivos: list[str] = []
        result: Resultado = pol.get("por_defecto", "PAGAR")

        def peor(nuevo: Resultado) -> None:
            nonlocal result
            orden = {"PAGAR": 0, "ESCALAR": 1, "NO_PAGAR": 2}
            if orden[nuevo] > orden[result]:
                result = nuevo

        if f.via == "sin_texto" or f.total is None:
            peor(pol.get("documento_ilegible", "ESCALAR"))
            motivos.append("no se pudieron extraer los datos del documento")

        if f.pedido in self.revisar:
            peor(pol.get("marcado_revision", "ESCALAR"))
            motivos.append(f"{f.pedido} esta marcado para revision en el Excel de Alberto")

        if f.iva_pct is not None and str(int(f.iva_pct)) != str(
                self.norma["reglas"][2].get("iva_estandar", "21")):
            peor(pol.get("iva_no_estandar", "ESCALAR"))
            motivos.append(f"IVA al {f.iva_pct}%, no el estandar")

        for v in veredictos:
            if v.veredicto == "FALLA":
                peor(pol["mapeo"].get(v.id, {}).get("FALLA", "ESCALAR"))
                motivos.append(v.evidencia.get("motivo", f"{v.id} no se cumple"))
            elif v.veredicto == "NA" and v.evidencia.get("faltan"):
                peor(pol.get("datos_incompletos", "ESCALAR"))
                motivos.append(f"faltan datos: {', '.join(v.evidencia['faltan'])}")

        if result == "PAGAR" and not motivos:
            motivos.append("cumple las cinco reglas de la norma " + self.norma["version"])

        return Decision(
            doc_id=f.doc_id, file_id=f.file_id, result=result,
            motivo="; ".join(dict.fromkeys(motivos)),
            norma_version=self.norma["version"],
            snapshot_erp=snapshot_erp, snapshot_maestro=snapshot_maestro,
            reglas=tuple(veredictos), coste_eur=f.coste_eur, latencia_ms=f.latencia_ms,
        )
