"""Builds a demonstrable dataset in the ledger from the REAL master data.

Vendors, NIFs, IBANs and pedidos come from FINAL_v7_DEFINITIVO_ahorasi.xlsx via
rules_ingestion.loader -- the same source the production checks use -- so the
shapes, ids and amounts are the real ones. What is synthesised here is the
invoice population itself (and the faults injected into it), because the live
pipeline needs API keys and 500 OCR calls to produce it.

When webui/data/revisiones.db holds real pipeline rows, adapters.import_real()
loads those instead; the ledger, the UI and the agents do not know the
difference. Deterministic: seeded RNG, so the demo is identical every run.
"""
from __future__ import annotations

import random
import re
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from desk import engine, master, rules
from desk.ledger import Ledger

TODAY = date(2026, 9, 19)
RULESET_VERSION = "v3.1-balanced"
SOURCES_YAML = str(ROOT / "rules_ingestion" / "sources.yaml")

# Cost model, measured from the pipeline's own emitted events (see docs/price-speed-handoff.md
# for the methodology); kept here so every seeded event carries a real-shaped cost.
COST_OCR_PAGE = 0.0021
COST_INTERPRET_1K = 0.0014


# Where each injected fault lives, so the escalation queue has real patterns.
PINNED_VENDOR = {
    "missing_pedido": "P007",   # Papeleria Ruzafa: small stationer, never sends a PO
    "iban_mismatch":  "P010",   # Informatica Benimamet: changed bank this quarter
    "terms_exceeded": "P009",   # Construcciones Benimaclet: 60d terms, always late
}


def _slug(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    plain = re.sub(r"\b(S\.?L\.?|S\.?A\.?|SLU|SCP)\b", "", plain, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", plain.lower())[:24] or "proveedor"


def _email(name: str) -> str:
    return f"facturacion@{_slug(name)}.es"


def load_master() -> tuple[dict, dict]:
    return master.load()


# --------------------------------------------------------------------------- #
# fault plan: which invoices are broken, and how
# --------------------------------------------------------------------------- #
FAULTS = {
    "hard_duplicate":  4,   # same invoice_number + vendor, already PAGADA in ERP
    "soft_duplicate":  3,   # same amount + date as another invoice
    "iban_mismatch":   3,   # IBAN differs from master data
    "missing_pedido":  6,   # no PO reference on the invoice
    "amount_mismatch": 3,   # total does not match the pedido
    "future_date":     2,   # issued in the future
    "terms_exceeded":  3,   # past the vendor's payment terms
}


def _rules_for(fault: str | None, inv: dict, vendor: dict) -> list[dict]:
    """Per-rule verdicts with the COMPARED VALUES kept, not just PASS/FAIL."""
    ok = lambda c, r, **d: {"canonical": c, "verdict": "PASS", "reason": r, "detail": d}
    total, base, iva = inv["total"], inv["base"], inv["iva"]

    vendor_chk = ok("VENDOR", f"{vendor['razon_social']} activo, NIF y IBAN coinciden con maestro",
                    nif=inv["nif"], iban_invoice=inv["iban"], iban_master=vendor["iban"], activo=True)
    dup_chk = ok("DUPLICATES", "sin coincidencia por (numero, proveedor) ni por (importe, fecha)",
                 hard_key=[inv["invoice_number"], vendor["id"]], matches=[])
    amt_chk = ok("AMOUNT", f"base {base:.2f} + IVA 21% = {total:.2f}, la factura dice {total:.2f}, delta 0.00",
                 base=base, iva=iva, total=total, delta=0.0, tolerance=0.01,
                 pedido_importe=inv.get("pedido_importe"))
    dates_chk = ok("DATES", f"emitida {inv['date']}, vencimiento {inv['due_date']} dentro de {vendor['condiciones']}",
                   issued=inv["date"], due=inv["due_date"], terms=vendor["condiciones"])
    miss_chk = ok("MISSING", "nif, iban, pedido, importe, iva y fecha presentes",
                  required=["nif", "iban", "pedido", "importe", "iva", "fecha"], absent=[])

    if fault == "hard_duplicate":
        dup_chk = {"canonical": "DUPLICATES", "verdict": "FAIL", "on_fail": "NO_PAGAR",
                   "reason": f"ya existe {inv['invoice_number']} de {vendor['razon_social']}"
                             f" liquidada en el ERP ({inv['dup_asiento']}, estado PAGADA)",
                   "detail": {"hard_key": [inv["invoice_number"], vendor["id"]],
                              "matches": [inv["dup_asiento"]], "erp_estado": "PAGADA",
                              "importe": total}}
    elif fault == "soft_duplicate":
        dup_chk = {"canonical": "DUPLICATES", "verdict": "NEEDS_REVIEW", "on_fail": "ESCALAR",
                   "reason": f"mismo importe {total:.2f} y misma fecha {inv['date']} que {inv['dup_ref']}",
                   "detail": {"soft_key": [total, inv["date"]], "matches": [inv["dup_ref"]]}}
    elif fault == "iban_mismatch":
        vendor_chk = {"canonical": "VENDOR", "verdict": "NEEDS_REVIEW", "on_fail": "ESCALAR",
                      "reason": "el IBAN de la factura no coincide con el del maestro de proveedores",
                      "detail": {"iban_invoice": inv["iban"], "iban_master": vendor["iban"],
                                 "nif": inv["nif"], "activo": True}}
    elif fault == "missing_pedido":
        miss_chk = {"canonical": "MISSING", "verdict": "NEEDS_REVIEW", "on_fail": "ESCALAR",
                    "reason": "falta el numero de pedido en la factura",
                    "detail": {"required": ["nif", "iban", "pedido", "importe", "iva", "fecha"],
                               "absent": ["pedido"]}}
    elif fault == "amount_mismatch":
        delta = round(total - inv["pedido_importe"], 2)
        amt_chk = {"canonical": "AMOUNT", "verdict": "NEEDS_REVIEW", "on_fail": "ESCALAR",
                   "reason": f"el total {total:.2f} no cuadra con el pedido {inv['pedido']}"
                             f" ({inv['pedido_importe']:.2f}), diferencia {delta:+.2f}",
                   "detail": {"total": total, "pedido_importe": inv["pedido_importe"],
                              "delta": delta, "tolerance": 0.01}}
    elif fault == "future_date":
        dates_chk = {"canonical": "DATES", "verdict": "FAIL", "on_fail": "NO_PAGAR",
                     "reason": f"fecha de emision {inv['date']} posterior a hoy {TODAY.isoformat()}",
                     "detail": {"issued": inv["date"], "today": TODAY.isoformat()}}
    elif fault == "terms_exceeded":
        dates_chk = {"canonical": "DATES", "verdict": "NEEDS_REVIEW", "on_fail": "ESCALAR",
                     "reason": f"vencimiento {inv['due_date']} fuera de las condiciones pactadas"
                               f" ({vendor['condiciones']})",
                     "detail": {"issued": inv["date"], "due": inv["due_date"],
                                "terms": vendor["condiciones"]}}

    return [vendor_chk, dup_chk, amt_chk, dates_chk, miss_chk]


def _decision(checks: list[dict]) -> str:
    verdicts = {c["verdict"] for c in checks}
    if "FAIL" in verdicts:
        return "NO_PAGAR"
    if "NEEDS_REVIEW" in verdicts:
        return "ESCALAR"
    return "PAGAR"


def build(ledger: Ledger, n: int = 140, seed: int = 7) -> dict:
    """Emit the full event chain for n invoices. Idempotent per ledger file."""
    if ledger.by_kind("VERDICT"):
        return {"invoices": len(ledger.files()), "ruleset_version": rules.current(ledger)["version"]}
    rng = random.Random(seed)
    proveedores, pedidos = load_master()
    vendors = list(proveedores.values())
    pedido_list = [p for p in pedidos.values() if p.get("importe_total")]
    rng.shuffle(pedido_list)

    plan: list[str | None] = []
    for fault, count in FAULTS.items():
        plan += [fault] * count
    plan += [None] * (n - len(plan))
    rng.shuffle(plan)

    paid_history: list[dict] = []
    emitted: list[dict] = []

    for i, fault in enumerate(plan):
        pedido = pedido_list[i % len(pedido_list)]
        vendor = proveedores.get(pedido["proveedor_id"]) or rng.choice(vendors)
        pedido_importe = round(float(pedido["importe_total"]), 2)

        # Faults are concentrated on the vendors where they are realistic, which is
        # also what makes them CLUSTER: a small stationer that never sends a PO, one
        # supplier that changed bank. Scattered faults would be noise, not patterns.
        if fault in PINNED_VENDOR:
            vendor = proveedores[PINNED_VENDOR[fault]]
            pedido = {"pedido": f"PO-2026-{rng.randint(1, 516):04d}",
                      "proveedor_id": vendor["id"], "importe_total": pedido_importe}
        if fault == "missing_pedido":
            pedido_importe = round(rng.uniform(48, 195), 2)

        total = pedido_importe
        if fault == "amount_mismatch":
            total = round(pedido_importe + rng.choice([-1, 1]) * rng.uniform(40, 900), 2)
        base = round(total / 1.21, 2)
        iva = round(total - base, 2)

        issued = TODAY - timedelta(days=rng.randint(2, 70))
        if fault == "future_date":
            issued = TODAY + timedelta(days=rng.randint(3, 25))
        terms_days = int(re.search(r"\d+", vendor.get("condiciones", "30 dias")).group())
        due = issued + timedelta(days=terms_days)
        if fault == "terms_exceeded":
            due = issued + timedelta(days=terms_days + rng.randint(25, 60))

        number = f"F-{2026}{rng.randint(1000, 9999)}"
        file_id = f"factura_{4000 + i}.pdf"

        inv = {
            "file_id": file_id,
            "invoice_number": number,
            "vendor_id": vendor["id"],
            "vendor": vendor["razon_social"],
            "vendor_email": _email(vendor["razon_social"]),
            "nif": vendor["nif"],
            "iban": vendor["iban"],
            "pedido": pedido["pedido"],
            "pedido_importe": pedido_importe,
            "base": base, "iva": iva, "total": total,
            "date": issued.isoformat(), "due_date": due.isoformat(),
            "currency": "EUR",
            "terms": vendor.get("condiciones", "30 dias"),
        }

        if fault == "hard_duplicate" and paid_history:
            twin = rng.choice(paid_history)
            inv.update(invoice_number=twin["invoice_number"], vendor_id=twin["vendor_id"],
                       vendor=twin["vendor"], vendor_email=twin["vendor_email"],
                       nif=twin["nif"], iban=twin["iban"], total=twin["total"],
                       base=twin["base"], iva=twin["iva"],
                       dup_asiento=f"AS-{rng.randint(100, 899):05d}")
            vendor = proveedores[twin["vendor_id"]]
        elif fault == "hard_duplicate":
            inv["dup_asiento"] = f"AS-{rng.randint(100, 899):05d}"
        if fault == "soft_duplicate" and emitted:
            twin = rng.choice(emitted)
            inv.update(total=twin["total"], base=twin["base"], iva=twin["iva"],
                       date=twin["date"], dup_ref=twin["file_id"])
        elif fault == "soft_duplicate":
            inv["dup_ref"] = "factura_4001.pdf"
        if fault == "iban_mismatch":
            inv["iban"] = vendor["iban"][:-4] + f"{rng.randint(1000, 9999)}"
        if fault == "missing_pedido":
            inv["pedido"] = None

        # ---- the event chain -------------------------------------------
        t = f"2026-09-19T0{rng.randint(1,5)}:{rng.randint(10,59)}:{rng.randint(10,59)}.000+00:00"
        pages = rng.randint(1, 3)
        led = ledger

        led.record("FILE_RECEIVED", file_id=file_id, actor="pipeline", ts=t,
                   source="facturas/", pages=pages,
                   sha256=f"{rng.getrandbits(256):064x}", bytes=rng.randint(80_000, 400_000))

        # ~6% of files lose their primary provider and fall back -- resilience is
        # visible in the trace rather than asserted in a slide.
        attempts, provider, model = 1, "helmcode-vision", "helmcode-ocr-1"
        if rng.random() < 0.06:
            led.record("PROVIDER_FAILED", file_id=file_id, actor="pipeline", ts=t,
                       provider="helmcode-vision", error_code="HTTP_503",
                       action="retry with backoff, then fallback")
            attempts, provider, model = 2, "deepseek", "deepseek-vl"
        ocr_cost = round(COST_OCR_PAGE * pages * attempts, 5)
        led.record("EXTRACTED", file_id=file_id, actor="agent:extractor", ts=t,
                   provider=provider, model=model, attempts=attempts, pages=pages,
                   confidence=round(rng.uniform(0.88, 0.99), 3),
                   fields={k: inv[k] for k in ("invoice_number", "nif", "iban", "pedido",
                                               "base", "iva", "total", "date")},
                   cost_usd=ocr_cost, ms=rng.randint(1800, 5200))
        led.record("NORMALIZED", file_id=file_id, actor="pipeline", ts=t,
                   changes=[{"field": "total", "from": f"{total:,.2f}".replace(",", "."),
                             "to": f"{total:.2f}"},
                            {"field": "date", "from": issued.strftime("%d/%m/%Y"),
                             "to": issued.isoformat()}],
                   cost_usd=round(COST_INTERPRET_1K * rng.uniform(0.6, 1.8), 5),
                   ms=rng.randint(20, 90))

        retries = 0 if rng.random() > 0.22 else rng.randint(1, 3)
        led.record("ERP_RECONCILED", file_id=file_id, actor="agent:conciliador", ts=t,
                   pedido=inv["pedido"], asiento=None if not inv["pedido"] else
                   f"AS-{rng.randint(100, 899):05d}",
                   erp_estado="PAGADA" if fault == "hard_duplicate" else "PENDIENTE",
                   retries=retries,
                   errors=["ORA-00600"] * retries,
                   note="reintento sobre la misma consulta, tal y como indica el manual de 2009"
                        if retries else None,
                   ms=rng.randint(120, 2400) + retries * 900)

        evaluation = engine.snapshot(
            {**inv, "line_items": []}, vendor,
            {"pedido": inv.get("pedido"), "importe_total": inv.get("pedido_importe")},
            erp_estado="PAGADA" if fault == "hard_duplicate" else "PENDIENTE",
            today=TODAY.isoformat(),
            hard_matches=[inv["dup_asiento"]] if fault == "hard_duplicate" else [],
            soft_matches=[inv["dup_ref"]] if fault == "soft_duplicate" else [],
        )
        decision, checks = engine.evaluate(evaluation, rules.current(led))
        for chk in checks:
            led.record("RULE_EVALUATED", file_id=file_id, actor="engine", ts=t,
                       ruleset_version=RULESET_VERSION, ms=rng.randint(1, 9), **chk)

        blocking = [c for c in checks if c["effect"] != "PAGAR"]
        led.record("VERDICT", file_id=file_id, actor="engine", ts=t,
                   ruleset_version=RULESET_VERSION, decision=decision,
                   deterministic=True, fault=fault, demo=True, evaluation=evaluation, checks=checks,
                   precedence=["NO_PAGAR", "ESCALAR", "PAGAR"],
                   driven_by=[c["canonical"] for c in blocking] or ["all rules passed"],
                   invoice=inv, ms=rng.randint(8, 40))

        inv["_fault"] = fault
        inv["_decision"] = decision
        emitted.append(inv)
        if decision == "PAGAR":
            paid_history.append(inv)

    return {"invoices": len(emitted), "ruleset_version": RULESET_VERSION}


if __name__ == "__main__":
    from datetime import UTC, datetime
    out = ROOT / "desk" / "data" / f"demo-{datetime.now(UTC):%Y%m%d%H%M%S%f}.db"
    print(build(Ledger(out)))
