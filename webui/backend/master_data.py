"""Builds the `master` dict rules_ingestion.checks expects.

Vendor/purchase-order data comes from the same messy workbook
rules_ingestion.build_rules already parses (FINAL_v7_DEFINITIVO_ahorasi.xlsx,
via rules_ingestion.loader -- reused as-is, not reimplemented). Payment status
(PAGADA/PENDIENTE) comes from the live mock ERP (alberto_erp.py), which is the
authoritative, possibly-flaky source the checks are designed to trust over the
spreadsheet's own stale Estado column.
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

TODAY = "2026-09-19"


def load_proveedores_pedidos(sources_yaml: str):
    from rules_ingestion import loader

    result = loader.load(sources_yaml)
    return result.lookups.get("proveedores", {}), result.lookups.get("pedidos", {})


class ErpClient:
    """Minimal client for the local mock ERP (see alberto_erp.py / Makefile erp target)."""

    def __init__(self, base_url: str = "http://127.0.0.1:8009",
                usuario: str = "alberto", clave: str = "FACTURAS2009"):
        self.base_url = base_url.rstrip("/")
        self.usuario = usuario
        self.clave = clave
        self.token = None

    def _login(self) -> str:
        body = urllib.parse.urlencode({"usuario": self.usuario, "clave": self.clave}).encode()
        req = urllib.request.Request(f"{self.base_url}/erp/login", data=body, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml = resp.read().decode("iso-8859-1", errors="replace")
        return ET.fromstring(xml).findtext("token")

    def estado_por_pedido(self, *, max_pages: int = 200) -> dict[str, str]:
        """Page through /erp/asientos, tolerating the simulator's induced
        429/500 faults with a small retry+backoff (it is designed to be flaky)."""
        if self.token is None:
            self.token = self._login()
        estado: dict[str, str] = {}
        pagina = 1
        while pagina <= max_pages:
            url = f"{self.base_url}/erp/asientos?pagina={pagina}&token={self.token}"
            for attempt in range(5):
                try:
                    with urllib.request.urlopen(url, timeout=10) as resp:
                        xml = resp.read().decode("iso-8859-1", errors="replace")
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code == 401:
                        self.token = self._login()
                        url = f"{self.base_url}/erp/asientos?pagina={pagina}&token={self.token}"
                    time.sleep(min(2 ** attempt, 4) * 0.2)
                except urllib.error.URLError:
                    time.sleep(min(2 ** attempt, 4) * 0.2)
            else:
                break
            root = ET.fromstring(xml)
            filas = root.findall(".//asiento")
            if not filas:
                break
            for fila in filas:
                pedido = fila.findtext("pedido")
                estado_val = fila.findtext("estado")
                if pedido and estado_val:
                    estado[pedido] = estado_val
            meta = root.find(".//meta")
            total_paginas = int(meta.findtext("paginas", "1")) if meta is not None else 1
            if pagina >= total_paginas:
                break
            pagina += 1
        return estado


def build_master(sources_yaml: str, seen_invoice_keys: set, seen_amount_date: set,
                 erp_base_url: str = "http://127.0.0.1:8009") -> dict:
    from rules_ingestion.invoice import build_master as _build

    proveedores, pedidos = load_proveedores_pedidos(sources_yaml)
    try:
        erp_estado = ErpClient(erp_base_url).estado_por_pedido()
    except Exception:
        erp_estado = {}
    master = _build(proveedores, pedidos, erp_estado, today=TODAY)
    master["seen_invoice_keys"] = seen_invoice_keys
    master["seen_amount_date"] = seen_amount_date
    return master
