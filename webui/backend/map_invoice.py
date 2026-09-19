"""Maps ingestion's evidence-linked invoice.json (benchmark/schemas/invoice.json)
to the flat raw shape rules_ingestion.invoice.normalize_invoice expects.

Two different "invoice" contracts meet here: the OCR/LLM extraction pipeline
(ingestion/) proves every field with evidence; the payment-decision checks
(rules_ingestion/) read a flat dict. This is the seam between them.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _dec(value: str | None) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def to_raw_invoice(file_id: str, invoice: dict) -> dict:
    supplier = invoice.get("supplier") or {}
    payment = invoice.get("payment") or {}
    totals = invoice.get("totals") or {}
    taxes = invoice.get("taxes") or []
    lines = invoice.get("lines") or []

    iva_total = sum((_dec(t.get("amount")) or Decimal(0)) for t in taxes) if taxes else None

    return {
        "file_id": file_id,
        "invoice_number": invoice.get("invoice_number"),
        "vendor_id": None,  # resolved by NIF via master["proveedores_by_nif"]
        "nif": supplier.get("tax_id"),
        "iban": payment.get("iban"),
        "pedido": invoice.get("purchase_order_reference"),
        "base": totals.get("taxable_base"),
        "iva": str(iva_total) if iva_total is not None else None,
        "total": totals.get("total"),
        "currency": invoice.get("currency"),
        "date": invoice.get("issue_date"),
        "line_items": [ln.get("amount") for ln in lines if ln.get("amount") is not None],
    }
