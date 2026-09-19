"""Maps ingestion's evidence-linked invoice.json (benchmark/schemas/invoice.json)
to the flat raw shape rules_ingestion.invoice.normalize_invoice expects.

Two different "invoice" contracts meet here: the OCR/LLM extraction pipeline
(ingestion/) proves every field with evidence; the payment-decision checks
(rules_ingestion/) read a flat dict. This is the seam between them.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from rules_ingestion.decision_registry import VAT_LABEL_PATTERN

_VAT_LABEL = re.compile(VAT_LABEL_PATTERN, re.IGNORECASE)
_DECIMAL = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")


def _dec(value: str | None) -> Decimal | None:
    if not isinstance(value, str) or not _DECIMAL.match(value):
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def to_raw_invoice(file_id: str, invoice: dict) -> dict:
    supplier = invoice.get("supplier") or {}
    payment = invoice.get("payment") or {}
    totals = invoice.get("totals") or {}
    taxes = invoice.get("taxes") or []
    lines = invoice.get("lines") or []

    iva_total = None
    if taxes:
        subtotal = Decimal(0)
        explicit_vat = True
        for tax in taxes:
            if not isinstance(tax, dict):
                explicit_vat = False
                break
            label = tax.get("label")
            amount = _dec(tax.get("amount"))
            if not isinstance(label, str) or not _VAT_LABEL.match(label.strip()) \
                    or amount is None:
                explicit_vat = False
                break
            subtotal += amount
        if explicit_vat:
            iva_total = subtotal

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
        "line_items": [ln.get("amount") for ln in lines],
    }


def to_decision_context(outcome: dict, **kwargs):
    from rules_ingestion.decision_context import build_context

    return build_context(outcome, **kwargs)
