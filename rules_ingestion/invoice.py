"""invoice.py · the normalized shapes the checks read.

Two tiny contracts, both plain dicts (no framework), both normalized through
`rules_ingestion.normalize` so the invoice and the master compare like-for-like:

  invoice = {
    "file_id", "invoice_number", "vendor_id", "nif", "iban", "pedido",
    "base", "iva", "total", "currency", "date", "line_items"
  }
  master  = {
    "proveedores": {id: row}, "proveedores_by_nif": {nif: row},
    "pedidos": {pedido: row}, "erp_estado": {pedido: "PENDIENTE"|"PAGADA"},
    "seen_invoice_keys": set(), "seen_amount_date": set(), "today": "YYYY-MM-DD"
  }

PDF/e-mail extraction and the live ERP are NOT here — they just have to produce
these two dicts. That keeps the checks a pure function of normalized data.
"""
from __future__ import annotations

from typing import Optional

from . import normalize as N


def normalize_invoice(raw: dict) -> dict:
    """Coerce a raw invoice dict into the normalized contract above."""
    lines = raw.get("line_items") or []
    return {
        "file_id": raw.get("file_id"),
        "invoice_number": N.norm_text(raw.get("invoice_number")),
        "vendor_id": N.norm_text(raw.get("vendor_id")),
        "nif": N.norm_nif(raw.get("nif")),
        "iban": N.norm_iban(raw.get("iban")),
        "pedido": N.norm_text(raw.get("pedido")),
        "base": N.norm_importe(raw.get("base")),
        "iva": N.norm_importe(raw.get("iva")),
        "total": N.norm_importe(raw.get("total")),
        "currency": (N.norm_text(raw.get("currency")) or "EUR").upper(),
        "date": N.norm_fecha(raw.get("date")),
        "line_items": [N.norm_importe(x) for x in lines if N.norm_importe(x) is not None],
    }


def build_master(proveedores: dict, pedidos: dict,
                 erp_estado: Optional[dict] = None,
                 today: Optional[str] = None) -> dict:
    """Assemble the master lookup the checks read (adds the NIF index)."""
    by_nif = {}
    for row in proveedores.values():
        nif = N.norm_nif(row.get("nif"))
        if nif:
            by_nif[nif] = row
    return {
        "proveedores": proveedores,
        "proveedores_by_nif": by_nif,
        "pedidos": pedidos,
        "erp_estado": erp_estado or {},
        "seen_invoice_keys": set(),
        "seen_amount_date": set(),
        "today": today,
    }
