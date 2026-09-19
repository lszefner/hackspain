from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from rules_ingestion.checks import run_checks
from rules_ingestion.invoice import build_master

STORE = Path(__file__).with_name("rules.store.json")
FIELDS = {"nif": "nif", "iban": "iban", "pedido": "pedido", "importe": "total",
          "iva": "iva", "fecha": "date"}


def frozen_store() -> dict:
    return json.loads(STORE.read_text(encoding="utf-8"))


def store_hash() -> str:
    return hashlib.sha256(STORE.read_bytes()).hexdigest()


def serial(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serial(v) for v in value]
    return value


def decimals(inv: dict) -> dict:
    inv = copy.deepcopy(inv)
    for key in ("base", "iva", "total", "pedido_importe", "importe_total"):
        if inv.get(key) is not None:
            inv[key] = Decimal(str(inv[key]))
    inv["line_items"] = [Decimal(str(v)) for v in inv.get("line_items", []) if v is not None]
    return inv


def snapshot(inv, vendor, pedido, *, erp_estado=None, today, hard_matches=(), soft_matches=()):
    return serial({"invoice": inv, "vendor": vendor, "pedido": pedido,
                   "erp_estado": erp_estado, "today": today,
                   "hard_matches": list(hard_matches), "soft_matches": list(soft_matches),
                   "rules_store": frozen_store(), "rules_store_sha256": store_hash()})


def evaluate(evidence: dict, policy: dict) -> tuple[str, list[dict]]:
    inv = decimals(evidence["invoice"])
    vendor = copy.deepcopy(evidence.get("vendor"))
    pedido = decimals(evidence["pedido"]) if evidence.get("pedido") else None
    master = build_master({vendor["id"]: vendor} if vendor else {},
                          {inv["pedido"]: pedido} if pedido and inv.get("pedido") else {},
                          {inv["pedido"]: evidence.get("erp_estado")} if inv.get("pedido") else {},
                          today=evidence["today"])
    if evidence.get("hard_matches"):
        master["seen_invoice_keys"].add((inv.get("invoice_number"), inv.get("vendor_id")))
    if evidence.get("soft_matches"):
        master["seen_amount_date"].add((str(inv.get("total")), inv.get("date")))
    store = copy.deepcopy(evidence.get("rules_store") or frozen_store())
    by_rule = {r["canonical"]: r for r in store["rules"]}
    by_rule["AMOUNT"]["params"]["tolerance_eur"] = policy["amount_tolerance_eur"]
    by_rule["DATES"]["params"]["enforce_payment_terms"] = policy["enforce_payment_terms"]
    auth = by_rule["AUTHORIZATION"]
    auth["enabled"] = policy.get("authorization_threshold_eur") is not None
    if auth["enabled"]:
        auth["params"]["escalate_above_eur"] = policy["authorization_threshold_eur"]
    exemption = next((e for e in policy.get("missing_pedido_exemptions", [])
                      if e["vendor_id"] == inv.get("vendor_id") and inv.get("total") is not None
                      and inv["total"] < Decimal(str(e["max_eur"]))), None)
    if exemption:
        by_rule["MISSING"]["params"]["required_fields"] = [
            f for f in by_rule["MISSING"]["params"]["required_fields"] if f != "pedido"]
    checks = run_checks(inv, master, store)
    for c in checks:
        canon = c["canonical"]
        params = by_rule[canon]["params"]
        c["rule_id"] = by_rule[canon].get("rule_id", by_rule[canon].get("id"))
        c["engine_reason"] = c["reason"]
        c["effect"] = (c.get("on_fail") or "ESCALAR") if c["verdict"] == "FAIL" else (
            "ESCALAR" if c["verdict"] == "NEEDS_REVIEW" else "PAGAR")
        if canon == "DUPLICATES" and c["verdict"] == "NEEDS_REVIEW":
            soft = policy["soft_duplicate_verdict"]
            c["effect"] = {"PASS": "PAGAR", "FAIL": "NO_PAGAR", "NEEDS_REVIEW": "ESCALAR"}[soft]
        detail = {"params": copy.deepcopy(params), "engine_verdict": c["verdict"]}
        if canon == "VENDOR":
            detail.update(nif=inv.get("nif"), nif_master=(vendor or {}).get("nif"),
                          iban_invoice=inv.get("iban"), iban_master=(vendor or {}).get("iban"),
                          vendor_id=inv.get("vendor_id"), vendor_found=vendor is not None)
            c["reason"] = ("proveedor no encontrado en el maestro" if vendor is None else
                           "NIF de la factura distinto del maestro" if inv.get("nif") and inv["nif"] != vendor.get("nif") else
                           "el IBAN de la factura no coincide con el maestro" if inv.get("iban") and inv["iban"] != vendor.get("iban") else
                           "proveedor encontrado; NIF e IBAN presentes coinciden con el maestro")
        elif canon == "DUPLICATES":
            hard = evidence.get("hard_matches", [])
            soft = evidence.get("soft_matches", [])
            detail.update(hard_key=[inv.get("invoice_number"), inv.get("vendor_id")],
                          soft_key=[str(inv.get("total")), inv.get("date")] if soft else None,
                          hard_matches=hard, soft_matches=soft, matches=hard or soft,
                          erp_estado=evidence.get("erp_estado"), pedido=inv.get("pedido"),
                          soft_duplicate_verdict=policy["soft_duplicate_verdict"])
            c["reason"] = (f"pedido {inv.get('pedido')} ya PAGADA en el ERP" if evidence.get("erp_estado") == "PAGADA" else
                           "mismo numero y proveedor que " + ", ".join(hard) if hard else
                           "mismo importe y fecha que " + ", ".join(soft) if soft else
                           "sin coincidencias de numero/proveedor ni importe/fecha; sin pago previo registrado en ERP")
        elif canon == "AMOUNT":
            base, iva, total = (inv.get(k) for k in ("base", "iva", "total"))
            order_total = (pedido or {}).get("importe_total")
            line_sum = sum(inv["line_items"], Decimal(0)) if inv["line_items"] else None
            detail.update(base=base, iva=iva, total=total, currency=inv.get("currency"),
                          line_items=inv["line_items"], line_sum=line_sum,
                          pedido_importe=order_total, tolerance=policy["amount_tolerance_eur"],
                          delta=total - order_total if total is not None and order_total is not None else None,
                          arithmetic_delta=base + iva - total if None not in (base, iva, total) else None,
                          line_delta=line_sum - base if line_sum is not None and base is not None else None)
            c["reason"] = (f"base {base}, IVA {iva}, total {total}; suma de lineas {line_sum}; "
                           f"pedido {order_total}; tolerancia {policy['amount_tolerance_eur']} EUR. "
                           + ("Las comparaciones disponibles cuadran." if c["verdict"] == "PASS" else
                              "Hay una discrepancia de moneda o importe; ver valores comparados."))
        elif canon == "DATES":
            detail.update(issued=inv.get("date"), due=inv.get("due_date"), today=evidence["today"],
                          terms=(vendor or {}).get("condiciones"))
            c["reason"] = (f"fecha {inv.get('date')}, referencia {evidence['today']}, "
                           f"condiciones {(vendor or {}).get('condiciones')}: "
                           + ("fecha futura" if c["verdict"] == "FAIL" else
                              "plazo de pago superado" if c["verdict"] == "NEEDS_REVIEW" else
                              "pasa las comprobaciones de fecha habilitadas"))
        elif canon == "MISSING":
            required = params["required_fields"]
            absent = [f for f in required if inv.get(FIELDS.get(f, f)) in (None, "")]
            detail.update(required=required, absent=absent, exemption=exemption,
                          compared={f: inv.get(FIELDS.get(f, f)) for f in required})
            c["reason"] = "faltan campos obligatorios: " + ", ".join(absent) if absent else "campos obligatorios presentes"
            if exemption:
                c["reason"] += f"; exencion de pedido para {exemption['vendor']} por debajo de {exemption['max_eur']} EUR"
        elif canon == "AUTHORIZATION":
            detail.update(total=inv.get("total"), threshold=policy["authorization_threshold_eur"])
            c["reason"] = (f"importe {inv.get('total')}, umbral de autorizacion "
                           f"{policy['authorization_threshold_eur']} EUR: "
                           + ("requiere segunda firma" if c["verdict"] != "PASS" else "dentro del umbral"))
        c["detail"] = serial(detail)
    effects = {c["effect"] for c in checks}
    decision = next((d for d in store.get("precedence", ["NO_PAGAR", "ESCALAR", "PAGAR"])
                     if d in effects), "ESCALAR")
    return decision, checks
