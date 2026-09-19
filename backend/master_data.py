"""Builds the `master` dict rules_ingestion.checks expects.

Vendor/purchase-order data comes from the same messy workbook
rules_ingestion.build_rules already parses (FINAL_v7_DEFINITIVO_ahorasi.xlsx,
via rules_ingestion.loader -- reused as-is, not reimplemented). Payment status
(PAGADA/PENDIENTE) comes from the live mock ERP (alberto_erp.py), which is the
authoritative, possibly-flaky source the checks are designed to trust over the
spreadsheet's own stale Estado column.
"""
from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import yaml

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

    def snapshot(self, *, max_pages: int = 200) -> dict:
        pages: list[dict] = []
        records: list[dict] = []
        error_code = None
        complete = False
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) \
                or max_pages < 1:
            return {"records": records, "pages": pages, "complete": False,
                    "error_code": "invalid_max_pages"}
        try:
            if self.token is None:
                self.token = self._login()
            if not self.token:
                return {"records": records, "pages": pages,
                        "complete": False, "error_code": "invalid_response"}
        except urllib.error.HTTPError as exc:
            return {"records": records, "pages": pages, "complete": False,
                    "error_code": f"http_{exc.code}"}
        except urllib.error.URLError:
            return {"records": records, "pages": pages, "complete": False,
                    "error_code": "connection_error"}
        except (ET.ParseError, ValueError, TypeError):
            return {"records": records, "pages": pages, "complete": False,
                    "error_code": "invalid_response"}
        expected_total = None
        expected_paginas = None
        expected_per_page = None
        pagina = 1
        while pagina <= max_pages:
            xml = None
            code = "unavailable"
            refreshed = False
            for attempt in range(5):
                url = f"{self.base_url}/erp/asientos?pagina={pagina}&token={self.token}"
                try:
                    with urllib.request.urlopen(url, timeout=10) as resp:
                        xml = resp.read().decode("iso-8859-1", errors="replace")
                    break
                except urllib.error.HTTPError as exc:
                    code = f"http_{exc.code}"
                    if exc.code == 401 and not refreshed:
                        refreshed = True
                        try:
                            self.token = self._login()
                        except Exception:
                            break
                        if not self.token:
                            code = "invalid_response"
                            break
                    time.sleep(min(2 ** attempt, 4) * 0.2)
                except (urllib.error.URLError, TimeoutError, OSError):
                    code = "connection_error"
                    time.sleep(min(2 ** attempt, 4) * 0.2)
            if xml is None:
                error_code = code
                break
            if (self.token and self.token in xml) \
                    or (self.clave and self.clave in xml):
                error_code = "sensitive_response"
                break
            pages.append({"page": pagina, "xml": xml})
            try:
                root = ET.fromstring(xml)
            except ET.ParseError:
                error_code = "invalid_xml"
                break
            meta = root.find(".//meta")
            try:
                meta_pagina = int(meta.findtext("pagina")) \
                    if meta is not None else None
                meta_paginas = int(meta.findtext("paginas")) \
                    if meta is not None else None
                meta_total = int(meta.findtext("total")) \
                    if meta is not None else None
                meta_per_page = int(meta.findtext("por_pagina")) \
                    if meta is not None else None
            except (TypeError, ValueError):
                meta_pagina = meta_paginas = meta_total = meta_per_page = None
            if None in (meta_pagina, meta_paginas, meta_total, meta_per_page) \
                    or meta_paginas < 1 or meta_total < 0 or meta_per_page < 1:
                error_code = "invalid_metadata"
                break
            if meta_pagina != pagina:
                error_code = "page_mismatch"
                break
            if expected_paginas is None:
                expected_paginas = meta_paginas
                expected_total = meta_total
                expected_per_page = meta_per_page
            elif meta_paginas != expected_paginas or meta_total != expected_total \
                    or meta_per_page != expected_per_page:
                error_code = "pagination_changed"
                break
            filas = root.findall(".//asiento")
            if not filas:
                if pagina != meta_paginas:
                    error_code = "empty_page"
                    break
            page_records = []
            malformed = False
            for row_index, fila in enumerate(filas):
                pedido = fila.findtext("pedido")
                estado_val = fila.findtext("estado")
                if not pedido or not estado_val:
                    malformed = True
                    break
                page_records.append({"pedido": pedido, "estado": estado_val,
                                     "page": pagina, "row": row_index + 1})
            if malformed:
                error_code = "malformed_record"
                break
            records.extend(page_records)
            if pagina == meta_paginas:
                if len(records) == expected_total:
                    complete = True
                else:
                    error_code = "count_mismatch"
                break
            pagina += 1
        if not complete and error_code is None:
            error_code = "truncated_max_pages"
        return {"records": records, "pages": pages, "complete": complete,
                "error_code": error_code}


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


def _unavailable_snapshot(kind: str, scopes: tuple, scope: str,
                          captured_at: str):
    from rules_ingestion.decision_context import SourceSnapshot

    return SourceSnapshot(kind=kind, payload=None, captured_at=captured_at,
                          asserted_by="master-data-capture",
                          authoritative_for=scopes, scope=scope,
                          availability="unavailable")


def capture_master_snapshots(sources_yaml: str, *, captured_at: str, loaded=None) -> dict:
    from rules_ingestion import loader
    from rules_ingestion.decision_context import SourceSnapshot

    supplier_scopes = ("supplier.identity", "supplier.bank_details",
                       "supplier.payment_terms")
    try:
        result = loaded if loaded is not None else loader.load(sources_yaml)
        cfg = yaml.safe_load(result.source_config_bytes) or {}
        sheets_cfg = (cfg.get("workbook") or {}).get("sheets") or {}
    except Exception as exc:
        scope = f"capture failed ({type(exc).__name__})"
        return {
            "workbook": _unavailable_snapshot(
                "master_workbook", (), scope, captured_at),
            "source_mapping": _unavailable_snapshot(
                "source_mapping", (), scope, captured_at),
            "suppliers": _unavailable_snapshot(
                "supplier_master", supplier_scopes, scope, captured_at),
            "orders": _unavailable_snapshot(
                "order_master", ("order.identity", "order.amount"),
                scope, captured_at),
        }
    workbook_sha = hashlib.sha256(result.workbook_bytes).hexdigest()
    config_sha = hashlib.sha256(result.source_config_bytes).hexdigest()
    snapshots = {
        "workbook": SourceSnapshot(
            kind="master_workbook", payload=result.workbook_bytes,
            captured_at=captured_at, asserted_by="master-data-capture",
            authoritative_for=(), scope="configured master workbook",
            availability="available"),
        "source_mapping": SourceSnapshot(
            kind="source_mapping", payload=result.source_config_bytes,
            captured_at=captured_at, asserted_by="master-data-capture",
            authoritative_for=(), scope="configured sources.yaml mapping",
            availability="available"),
    }

    def sheet_snapshot(sheet_key, kind, scopes, identity_columns):
        sheet_cfg = sheets_cfg.get(sheet_key) or {}
        declared = sheet_cfg.get("sheet")
        schema = result.schema.get(sheet_key)
        col_map = sheet_cfg.get("columns") or {}
        if schema is None or declared is None:
            return _unavailable_snapshot(
                kind, scopes, f"declared sheet {declared!r} not found",
                captured_at)
        missing = schema.get("missing_columns") or []
        found = schema.get("found_columns") or []
        duplicated = len(found) != len(set(found))
        identity_missing = any(
            col not in col_map or col_map.get(col) in missing
            for col in identity_columns)
        availability = "partial" if (identity_missing or duplicated) \
            else "available"
        warnings = [w for w in result.warnings
                    if w.startswith(f"[{sheet_key}]")]
        payload = {
            "records": result.source_records.get(sheet_key, []),
            "provenance": result.source_provenance.get(sheet_key, []),
            "sheet": declared,
            "workbook_sha256": workbook_sha,
            "config_sha256": config_sha,
            "missing_columns": missing,
            "warnings": warnings,
        }
        return SourceSnapshot(
            kind=kind, payload=payload, captured_at=captured_at,
            asserted_by="master-data-capture", authoritative_for=scopes,
            scope=f"workbook sheet {declared!r}", availability=availability)

    snapshots["suppliers"] = sheet_snapshot(
        "proveedores", "supplier_master", supplier_scopes, ("id", "nif"))
    order_scopes = ["order.identity", "order.amount"]
    pedidos_cfg = sheets_cfg.get("pedidos") or {}
    if "currency" in (pedidos_cfg.get("columns") or {}):
        order_scopes.append("order.currency")
    snapshots["orders"] = sheet_snapshot(
        "pedidos", "order_master", tuple(order_scopes),
        ("pedido", "proveedor_id"))
    return snapshots


def capture_erp_snapshot(client: ErpClient, *, captured_at: str):
    from rules_ingestion.decision_context import SourceSnapshot

    scopes = ("order.payment_status",)
    try:
        result = client.snapshot()
    except Exception as exc:
        return _unavailable_snapshot(
            "erp", scopes, f"erp capture failed ({type(exc).__name__})",
            captured_at)
    if not result.get("pages") and not result.get("records"):
        code = result.get("error_code") or "no_response"
        return _unavailable_snapshot(
            "erp", scopes, f"erp capture failed ({code})", captured_at)
    payload = {
        "records": result.get("records") or [],
        "pages": result.get("pages") or [],
        "complete": bool(result.get("complete")),
        "error_code": result.get("error_code"),
    }
    return SourceSnapshot(
        kind="erp", payload=payload, captured_at=captured_at,
        asserted_by="erp-bridge", authoritative_for=scopes,
        scope="erp asientos snapshot",
        availability="available" if result.get("complete") else "partial")
