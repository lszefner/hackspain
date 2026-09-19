from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from rules_ingestion.invoice import normalize_invoice
from rules_ingestion.normalize import norm_fecha, norm_importe, norm_text

FIELD_PATHS = {
    "invoice_number": "/invoice_number",
    "nif": "/supplier/tax_id",
    "iban": "/payment/iban",
    "pedido": "/purchase_order_reference",
    "base": "/totals/taxable_base",
    "total": "/totals/total",
    "currency": "/currency",
    "date": "/issue_date",
}

_VAT_LABEL = re.compile(r"^(IVA|VAT)\b", re.IGNORECASE)


def _pointer_get(invoice: dict, pointer: str):
    node = invoice
    for part in pointer.strip("/").split("/"):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _issue(code: str, reason: str) -> dict:
    return {"code": code, "kind": "readiness", "reason": reason}


def _rate(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _aggregate_iva(invoice: dict, issues: list[dict]):
    taxes = invoice.get("taxes") or []
    vat_rows = []
    non_vat = []
    for row in taxes:
        label = norm_text(row.get("label")) or ""
        if _VAT_LABEL.match(label):
            vat_rows.append(row)
        else:
            non_vat.append(row)
    if non_vat:
        labels = sorted({norm_text(r.get("label")) or "?" for r in non_vat})
        issues.append(_issue(
            "unsupported_tax",
            f"non-VAT tax rows present ({', '.join(labels)}); not added to IVA",
        ))
    if not taxes or not vat_rows:
        return None, None
    if any(row.get("amount") is None for row in vat_rows):
        issues.append(_issue("vat_amount_missing", "a VAT row has no amount"))
        return None, None
    iva = Decimal(0)
    for row in vat_rows:
        amount = norm_importe(row.get("amount"))
        if amount is None:
            issues.append(_issue("vat_amount_missing", "a VAT row has no amount"))
            return None, None
        iva += amount
    rates = set()
    bad_rate = False
    for row in vat_rows:
        rate = _rate(row.get("rate_percent"))
        if rate is None or not (Decimal(0) <= rate <= Decimal(100)):
            bad_rate = True
        else:
            rates.add(rate)
    if bad_rate:
        issues.append(_issue("vat_rate_invalid", "VAT rate missing or outside 0..100"))
        return iva, None
    if len(rates) > 1:
        issues.append(_issue(
            "vat_mixed_rates",
            "multiple VAT rates without per-rate base; cannot verify IVA",
        ))
        return iva, None
    return iva, next(iter(rates)) if rates else None


def adapt_invoice(invoice: dict, master: dict, contracts) -> tuple[dict, list[dict]]:
    contracts.validate("invoice", invoice)
    issues: list[dict] = []

    if invoice.get("document_type") != "invoice":
        issues.append(_issue(
            "document_type", f"document_type is {invoice.get('document_type')!r}",
        ))

    raw = {key: _pointer_get(invoice, ptr) for key, ptr in FIELD_PATHS.items()}
    raw["file_id"] = invoice["file_id"]
    raw["line_items"] = [line.get("amount") for line in invoice.get("lines") or []]

    if not invoice.get("lines"):
        issues.append(_issue("lines_empty", "invoice has no lines"))
    elif any(line.get("amount") is None for line in invoice["lines"]):
        issues.append(_issue("lines_partial", "a line has no amount"))

    if norm_text(raw.get("invoice_number")) is None:
        issues.append(_issue("invoice_number_missing", "missing invoice number"))
    if norm_importe(raw.get("base")) is None:
        issues.append(_issue("base_missing", "missing taxable base"))
    if norm_text(raw.get("currency")) is None:
        issues.append(_issue("currency_missing", "missing currency"))
    if raw.get("date") is None:
        issues.append(_issue("date_missing", "missing issue date"))
    elif norm_fecha(raw.get("date")) is None:
        issues.append(_issue("date_malformed", f"unparseable issue date {raw['date']!r}"))
    total = norm_importe(raw.get("total"))
    if total is None:
        issues.append(_issue("total_missing", "missing total"))
    elif total <= 0:
        issues.append(_issue("total_nonpositive", f"total {total} is not positive"))

    iva, vat_rate = _aggregate_iva(invoice, issues)
    raw["iva"] = iva

    inv = normalize_invoice(raw)
    currency = norm_text(invoice.get("currency"))
    inv["currency"] = currency.upper() if currency else None
    inv["vat_rate"] = vat_rate

    nif = inv.get("nif")
    vendor_id = None
    if nif:
        matches = master.get("by_nif", {}).get(nif) or []
        if len(matches) == 1:
            vendor_id = matches[0]
        elif len(matches) > 1:
            issues.append(_issue(
                "vendor_ambiguous",
                f"NIF {nif} matches {len(matches)} vendors; none selected",
            ))
    inv["vendor_id"] = vendor_id
    inv["field_sources"] = dict(FIELD_PATHS)
    return inv, issues
