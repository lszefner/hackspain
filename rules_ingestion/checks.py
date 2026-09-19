"""checks.py · the 6 canonical rules written as Python if-conditions.

Each check has the SAME signature and returns the SAME shape:

    check(inv: dict, master: dict, params: dict) -> (verdict, reason)
    verdict ∈ {"PASS", "FAIL", "NEEDS_REVIEW"}

`params` come straight from the matching rule in rules.store.json, so toggling a
param there changes the condition here with no code change. There is NO
cross-rule decision here — each condition stands alone (precedence comes later).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Callable, Dict, List, Tuple

from .normalize import is_future, payment_terms_days

Verdict = Tuple[str, str]


def _dec(x, default: str) -> Decimal:
    return Decimal(str(x)) if x is not None else Decimal(default)


def _find_vendor(inv: dict, master: dict):
    return (master["proveedores"].get(inv.get("vendor_id"))
            or master["proveedores_by_nif"].get(inv.get("nif")))


# --- R1 · VENDOR ----------------------------------------------------------- #
def check_vendor(inv: dict, master: dict, params: dict) -> Verdict:
    vendor = _find_vendor(inv, master)
    if vendor is None:
        return "FAIL", "vendor not found in the master"

    if params.get("require_nif_in_master", True):
        if inv.get("nif") and inv["nif"] != vendor.get("nif"):
            return "FAIL", f"NIF {inv['nif']} does not match the master ({vendor.get('nif')})"

    if params.get("require_iban_match", True):
        if inv.get("iban") and inv["iban"] != vendor.get("iban"):
            return "FAIL", "invoice IBAN does not match the master's (possible fraud)"

    return "PASS", "vendor, NIF and IBAN match the master"


# --- R2 · DUPLICATES ------------------------------------------------------- #
def check_duplicates(inv: dict, master: dict, params: dict) -> Verdict:
    estado = None
    if inv.get("pedido"):
        estado = master.get("erp_estado", {}).get(inv["pedido"])

    # already paid in the ERP (authoritative state) -> hard stop
    if params.get("require_erp_pending", True) and inv.get("pedido"):
        if estado == "PAGADA":
            return "FAIL", f"order {inv['pedido']} is already marked PAGADA in the ERP"

    # same invoice + same vendor already processed -> hard stop
    hard_key = (inv.get("invoice_number"), inv.get("vendor_id"))
    if all(hard_key) and hard_key in master.get("seen_invoice_keys", set()):
        return "FAIL", "same invoice and vendor already processed"

    if inv.get("pedido") and inv["pedido"] in master.get("seen_order_keys", set()):
        return params.get("soft_duplicate_verdict", "NEEDS_REVIEW"), \
            "multiple invoices claim the same order; reconcile before payment"

    if params.get("require_erp_pending", True) and inv.get("pedido"):
        if estado != "PENDIENTE":
            return "NEEDS_REVIEW", "ERP state is missing or not PENDIENTE"

    # same amount + same date -> not certain, needs a human
    soft_key = (inv.get("total"), inv.get("date"))
    if all(x is not None for x in soft_key) \
            and soft_key in master.get("seen_amount_date", set()):
        return params.get("soft_duplicate_verdict", "NEEDS_REVIEW"), \
            "same amount and date as another invoice"

    return "PASS", "no signs of a duplicate payment"


# --- R3 · AMOUNT ----------------------------------------------------------- #
def check_amount(inv: dict, master: dict, params: dict) -> Verdict:
    tol = _dec(params.get("tolerance_eur", 0.01), "0.01")
    base, iva, total = inv.get("base"), inv.get("iva"), inv.get("total")

    allowed = params.get("allowed_currencies")
    if allowed and inv.get("currency") not in allowed:
        return "FAIL", f"currency {inv.get('currency')} not allowed"

    if params.get("check_line_items_sum", True) and inv.get("line_items") and base is not None:
        suma = sum(inv["line_items"])
        if abs(suma - base) > tol:
            return "FAIL", f"line items sum to {suma}, not the base {base}"

    if params.get("check_total_is_base_plus_iva", True) and None not in (base, iva, total):
        if abs((base + iva) - total) > tol:
            return "FAIL", f"total {total} ≠ base+VAT ({base + iva})"

    vat_rate = inv.get("vat_rate")
    if params.get("check_iva", True) and vat_rate is not None \
            and base is not None and iva is not None:
        if abs(base * vat_rate / 100 - iva) > tol:
            return "FAIL", f"VAT {iva} invalid for base {base} at rate {vat_rate}%"

    if params.get("check_matches_pedido", True) and total is not None:
        ped = master["pedidos"].get(inv.get("pedido"))
        if ped is None or ped.get("importe_total") is None:
            return "NEEDS_REVIEW", "purchase order missing or has no amount"
        if abs(total - ped["importe_total"]) > tol:
            return "FAIL", f"total {total} ≠ purchase-order amount {ped['importe_total']}"

    return "PASS", "amounts, VAT and total add up"


# --- R4 · AUTHORIZATION (off in the v3 profile) ---------------------------- #
def check_authorization(inv: dict, master: dict, params: dict) -> Verdict:
    threshold = _dec(params.get("escalate_above_eur", 10000), "10000")
    total = inv.get("total")
    if total is not None and total > threshold:
        return "NEEDS_REVIEW", f"amount {total} exceeds threshold {threshold}: requires approval"
    return "PASS", "amount below the authorization threshold"


# --- R5 · DATES ------------------------------------------------------------ #
def check_dates(inv: dict, master: dict, params: dict) -> Verdict:
    date = inv.get("date")
    if date is None:
        return "PASS", "no date (covered by the required-fields rule)"

    if not params.get("allow_future", False) and is_future(date, master.get("today")):
        return "FAIL", f"date {date} is in the future"

    if params.get("enforce_payment_terms", True):
        vendor = _find_vendor(inv, master)
        dias = payment_terms_days(vendor.get("condiciones")) if vendor else None
        if dias is not None and master.get("today"):
            # overdue beyond the vendor's payment window -> a human should look
            from datetime import date as _date
            y, m, d = (int(x) for x in date.split("-"))
            ty, tm, td = (int(x) for x in master["today"].split("-"))
            if (_date(ty, tm, td) - _date(y, m, d)).days > dias:
                return "NEEDS_REVIEW", f"invoice overdue: more than {dias} days since {date}"

    return "PASS", "date valid, not future, and within terms"


# --- R6 · MISSING ---------------------------------------------------------- #
_FIELD_MAP = {"nif": "nif", "iban": "iban", "pedido": "pedido",
              "importe": "total", "iva": "iva", "fecha": "date"}


def check_missing(inv: dict, master: dict, params: dict) -> Verdict:
    required = params.get("required_fields", list(_FIELD_MAP))
    missing = [f for f in required
               if inv.get(_FIELD_MAP.get(f, f)) in (None, "")]
    if missing:
        return "FAIL", "missing required fields: " + ", ".join(missing)
    return "PASS", "all required fields are present"


# --- registry keyed by canonical (matches rules.store.json) ---------------- #
CHECKS: Dict[str, Callable[[dict, dict, dict], Verdict]] = {
    "VENDOR": check_vendor,
    "DUPLICATES": check_duplicates,
    "AMOUNT": check_amount,
    "AUTHORIZATION": check_authorization,
    "DATES": check_dates,
    "MISSING": check_missing,
}


def run_checks(inv: dict, master: dict, ruleset: dict) -> List[dict]:
    """Run every ENABLED rule's condition against one invoice.

    Returns a per-rule list (NO precedence, NO final decision):
        [{rule_id, canonical, enabled, verdict, reason, on_fail}]
    """
    out: List[dict] = []
    for rule in ruleset.get("rules", []):
        if not rule.get("enabled", True):
            continue
        check = CHECKS.get(rule["canonical"])
        if check is None:
            out.append({
                "rule_id": rule.get("id", rule.get("rule_id")),
                "canonical": rule.get("canonical"),
                "verdict": "NEEDS_REVIEW",
                "reason": "unsupported canonical rule; never executed",
                "on_fail": rule.get("on_fail"),
            })
            continue
        verdict, reason = check(inv, master, rule.get("params", {}))
        out.append({
            "rule_id": rule.get("id", rule.get("rule_id")),
            "canonical": rule["canonical"],
            "verdict": verdict,
            "reason": reason,
            "on_fail": rule.get("on_fail"),
        })
    return out
