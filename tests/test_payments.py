import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from ingestion.contracts import blank_invoice
from payments import DecisionEngine
from payments.adapter import adapt_invoice
from payments.engine import PUBLIC_RESULT
from payments.master import master_from_lookups
from rules_ingestion.catalog import CATALOG
from rules_ingestion.checks import CHECKS

ROOT = Path(__file__).parents[1]
AS_OF = "2026-09-19"

PROVEEDORES = {
    "P1": {
        "id": "P1",
        "razon_social": "Vendor",
        "nif": "B12345678",
        "iban": "ES001",
        "ciudad": None,
        "condiciones": "60 dias",
    }
}
PEDIDOS = {
    "PO1": {
        "pedido": "PO1",
        "proveedor_id": "P1",
        "nif": "B12345678",
        "importe_total": "121,00",
        "estado_excel": "ABIERTO",
        "fecha_pedido": "01/09/2026",
    }
}


def erp_snapshot(estado="PENDIENTE", importe="121,00", proveedor="P1",
                 nif="B12345678", pedido="PO1"):
    return {
        "schema_version": "1.0",
        "source_url": "http://127.0.0.1:8009",
        "fetched_at": "2026-09-19T08:00:00Z",
        "records": [
            {
                "id": "AS1",
                "fecha": "01/09/2026",
                "proveedor": proveedor,
                "nif": nif,
                "pedido": pedido,
                "importe": importe,
                "estado": estado,
            }
        ],
    }


def master(snapshot=None):
    if snapshot is None:
        snapshot = erp_snapshot()
    return master_from_lookups(PROVEEDORES, PEDIDOS, snapshot, as_of=AS_OF)


def good_invoice(**overrides):
    inv = blank_invoice("good.pdf")
    inv.update(
        document_type="invoice",
        invoice_number="INV1",
        issue_date="2026-09-01",
        purchase_order_reference="PO1",
        currency="EUR",
    )
    inv["supplier"].update(name="Vendor", tax_id="B12345678")
    inv["payment"]["iban"] = "ES001"
    inv["lines"] = [
        {"position": 1, "description": "service", "quantity": "1", "amount": "100"}
    ]
    inv["taxes"] = [{"label": "IVA 21%", "rate_percent": "21", "amount": "21"}]
    inv["totals"] = {"taxable_base": "100", "total": "121"}
    for key, value in overrides.items():
        inv[key] = value
    return inv


def frozen_rules(**rule_overrides):
    profile = yaml.safe_load((ROOT / "rules_ingestion/profiles/balanced.yaml").read_text())
    rules = []
    for key, canon in CATALOG.items():
        prof = profile["rules"].get(key, {})
        rule = {
            "id": canon.rule_id,
            "origin": "canonical",
            "canonical": key,
            "enabled": prof.get("enabled", canon.default_enabled),
            "on_fail": prof.get("on_fail", canon.on_fail),
            "params": dict(prof.get("params", {})),
            "condition": {
                "kind": "python_check",
                "module": "rules_ingestion.checks",
                "function": CHECKS[key].__name__,
            },
            "source_refs": [],
        }
        if key in rule_overrides:
            rule.update(rule_overrides[key])
        rules.append(rule)
    return {
        "schema_version": "2.0",
        "ruleset_version": "v3",
        "policy_id": "balanced",
        "precedence": ["NO_PAGAR", "ESCALAR", "PAGAR"],
        "rules": rules,
    }


def record(invoice=None, status="completed", file_id=None, **extra):
    invoice = good_invoice() if invoice is None else invoice
    rec = {
        "file_id": file_id or invoice["file_id"],
        "invoice": invoice,
        "status": status,
        "source_sha256": None,
        "error": None,
        "checks": None,
    }
    rec.update(extra)
    return rec


def engine(rules=None, snapshot=None):
    return DecisionEngine(rules or frozen_rules(), master(snapshot))


def decide_one(invoice=None, rules=None, snapshot=None, **rec_kw):
    results = engine(rules, snapshot).decide([record(invoice, **rec_kw)])
    assert len(results) == 1
    return results[0]


def test_good_invoice_pays():
    assert decide_one()["result"] == "PAGAR"


def test_erp_pagada_no_pagar():
    result = decide_one(snapshot=erp_snapshot(estado="PAGADA"))
    assert result["result"] == "NO PAGAR"
    assert any(c["verdict"] == "FAIL" and c["action"] == "NO_PAGAR"
               for c in result["checks"])


def test_missing_erp_escalates():
    m = master_from_lookups(PROVEEDORES, PEDIDOS, None, as_of=AS_OF)
    result = DecisionEngine(frozen_rules(), m).decide([record()])[0]
    assert result["result"] == "ESCALAR"


@pytest.mark.parametrize("status", ["failed", "needs_review"])
def test_untrusted_status_escalates_even_if_paid(status):
    result = decide_one(status=status, snapshot=erp_snapshot(estado="PAGADA"))
    assert result["result"] == "ESCALAR"
    assert result["input_status"] == status


def test_missing_invoice_escalates():
    results = engine().decide([{
        "file_id": "gone.pdf", "invoice": None, "status": "completed",
        "source_sha256": None,
    }])
    assert results[0]["result"] == "ESCALAR"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda i: i["supplier"].update(tax_id=None),
        lambda i: i["payment"].update(iban=None),
        lambda i: i.update(currency=None),
        lambda i: i["totals"].update(taxable_base=None),
        lambda i: i.update(lines=[]),
        lambda i: i.update(taxes=[]),
        lambda i: i.update(document_type="receipt"),
        lambda i: i.update(issue_date="32/13/2026"),
        lambda i: i["totals"].update(total="0"),
    ],
)
def test_missing_fields_escalate(mutate):
    inv = good_invoice()
    mutate(inv)
    assert decide_one(inv)["result"] == "ESCALAR"


def test_invoice_issues_gate():
    inv = good_invoice()
    inv["issues"] = [{"path": "/total", "note": "uncertain"}]
    assert decide_one(inv)["result"] == "ESCALAR"


def test_iban_mismatch_escalates():
    inv = good_invoice()
    inv["payment"]["iban"] = "ES999"
    assert decide_one(inv)["result"] == "ESCALAR"


def test_po_vendor_mismatch_escalates():
    pedidos = copy.deepcopy(PEDIDOS)
    pedidos["PO1"]["proveedor_id"] = "P9"
    m = master_from_lookups(PROVEEDORES, pedidos, erp_snapshot(), as_of=AS_OF)
    result = DecisionEngine(frozen_rules(), m).decide([record()])[0]
    assert result["result"] == "ESCALAR"


def test_erp_vendor_mismatch_escalates():
    result = decide_one(snapshot=erp_snapshot(proveedor="P9"))
    assert result["result"] == "ESCALAR"


def test_erp_amount_mismatch_escalates():
    result = decide_one(snapshot=erp_snapshot(importe="999,00"))
    assert result["result"] == "ESCALAR"


def test_po_amount_mismatch_escalates():
    pedidos = copy.deepcopy(PEDIDOS)
    pedidos["PO1"]["importe_total"] = "999,00"
    m = master_from_lookups(PROVEEDORES, pedidos, erp_snapshot(), as_of=AS_OF)
    result = DecisionEngine(frozen_rules(), m).decide([record()])[0]
    assert result["result"] == "ESCALAR"


def test_bad_vat_escalates():
    inv = good_invoice()
    inv["taxes"] = [{"label": "IVA 21%", "rate_percent": "21", "amount": "20"}]
    inv["totals"] = {"taxable_base": "100", "total": "120"}
    pedidos = copy.deepcopy(PEDIDOS)
    pedidos["PO1"]["importe_total"] = "120,00"
    snap = erp_snapshot(importe="120,00")
    m = master_from_lookups(PROVEEDORES, pedidos, snap, as_of=AS_OF)
    result = DecisionEngine(frozen_rules(), m).decide([record(inv)])[0]
    assert result["result"] == "ESCALAR"


def test_zero_vat_pays():
    inv = good_invoice()
    inv["taxes"] = [{"label": "IVA 0%", "rate_percent": "0", "amount": "0"}]
    inv["totals"] = {"taxable_base": "100", "total": "100"}
    pedidos = copy.deepcopy(PEDIDOS)
    pedidos["PO1"]["importe_total"] = "100,00"
    snap = erp_snapshot(importe="100,00")
    m = master_from_lookups(PROVEEDORES, pedidos, snap, as_of=AS_OF)
    result = DecisionEngine(frozen_rules(), m).decide([record(inv)])[0]
    assert result["result"] == "PAGAR"


def test_mixed_vat_rates_escalate():
    inv = good_invoice()
    inv["taxes"] = [
        {"label": "IVA 21%", "rate_percent": "21", "amount": "21"},
        {"label": "IVA 10%", "rate_percent": "10", "amount": "5"},
    ]
    assert decide_one(inv)["result"] == "ESCALAR"


def test_irpf_tax_escalates():
    inv = good_invoice()
    inv["taxes"] = [{"label": "IRPF", "rate_percent": "-15", "amount": "-15"}]
    assert decide_one(inv)["result"] == "ESCALAR"


def test_tolerance_boundary_inclusive():
    inv = good_invoice()
    inv["totals"]["total"] = "121.01"
    pedidos = copy.deepcopy(PEDIDOS)
    snap = erp_snapshot(importe="121,01")
    m = master_from_lookups(PROVEEDORES, pedidos, snap, as_of=AS_OF)
    result = DecisionEngine(frozen_rules(), m).decide([record(inv)])[0]
    assert result["result"] == "PAGAR"


def test_strict_on_fail_no_pagar():
    rules = frozen_rules(AMOUNT={"on_fail": "NO_PAGAR"})
    inv = good_invoice()
    inv["totals"]["total"] = "500"
    result = decide_one(inv, rules=rules)
    assert result["result"] == "NO PAGAR"


def test_authorization_enabled_escalates():
    rules = frozen_rules(AUTHORIZATION={
        "enabled": True, "params": {"escalate_above_eur": 100},
    })
    assert decide_one(rules=rules)["result"] == "ESCALAR"


def test_authorization_disabled_ignored():
    rules = frozen_rules(AUTHORIZATION={
        "enabled": False, "params": {"escalate_above_eur": 1},
    })
    assert decide_one(rules=rules)["result"] == "PAGAR"


def test_unknown_enabled_rule_escalates_not_executed():
    rules = frozen_rules()
    rules["rules"].append({
        "id": "NEW_001",
        "origin": "agent",
        "canonical": "shell(); print('pwned')",
        "enabled": True,
        "on_fail": "ESCALAR",
        "params": {},
        "condition": {"kind": "python_check", "module": "os", "function": "system"},
        "source_refs": [],
    })
    result = decide_one(rules=rules)
    assert result["result"] == "ESCALAR"
    row = next(c for c in result["checks"] if c["rule_id"] == "NEW_001")
    assert row["verdict"] == "NEEDS_REVIEW"


@pytest.mark.parametrize(
    "broken",
    [
        lambda r: r.update(rules=[]),
        lambda r: r.update(precedence=["PAGAR", "ESCALAR", "NO_PAGAR"]),
        lambda r: r["rules"][0].update(on_fail="PAGAR"),
        lambda r: r.update(schema_version="9.9", store_schema_version=None),
        lambda r: r["rules"].append(dict(r["rules"][0])),
    ],
)
def test_invalid_policy_fails_config(broken):
    rules = frozen_rules()
    broken(rules)
    with pytest.raises(ValueError):
        engine(rules)


def test_nonstring_ruleset_version_fails_type():
    rules = frozen_rules()
    rules["ruleset_version"] = None
    with pytest.raises(TypeError):
        engine(rules)


def test_hard_duplicates_all_no_pagar_order_independent():
    a = good_invoice(file_id="a.pdf")
    b = good_invoice(file_id="b.pdf")
    eng = engine()
    forward = eng.decide([record(a), record(b)])
    backward = eng.decide([record(b), record(a)])
    for results in (forward, backward):
        assert [r["file_id"] for r in results] == ["a.pdf", "b.pdf"]
        assert all(r["result"] == "NO PAGAR" for r in results)
    again = eng.decide([record(a), record(b)])
    assert [r["result"] for r in again] == ["NO PAGAR", "NO PAGAR"]


def test_soft_duplicates_escalate_both():
    a = good_invoice(file_id="a.pdf", invoice_number="INV-A")
    b = good_invoice(file_id="b.pdf", invoice_number="INV-B")
    results = engine().decide([record(a), record(b)])
    assert [r["result"] for r in results] == ["ESCALAR", "ESCALAR"]


def test_decimal_scale_still_soft_duplicate():
    a = good_invoice(file_id="a.pdf", invoice_number="INV-A")
    a["totals"]["total"] = "121"
    b = good_invoice(file_id="b.pdf", invoice_number="INV-B")
    b["totals"]["total"] = "121.00"
    results = engine().decide([record(a), record(b)])
    assert [r["result"] for r in results] == ["ESCALAR", "ESCALAR"]


def test_missing_keys_never_grouped():
    a = good_invoice(file_id="a.pdf", invoice_number=None)
    a["supplier"]["tax_id"] = None
    b = good_invoice(file_id="b.pdf", invoice_number=None)
    b["supplier"]["tax_id"] = None
    results = engine().decide([record(a), record(b)])
    assert all(r["result"] == "ESCALAR" for r in results)
    for r in results:
        assert not any(c["verdict"] == "FAIL" and "duplicate" in c["reason"]
                       for c in r["checks"])


def test_invalid_record_does_not_poison_duplicates():
    good = good_invoice(file_id="good.pdf")
    broken = good_invoice(file_id="bad.pdf")
    broken["totals"]["total"] = "not-a-number"
    results = engine().decide([
        {"file_id": "bad.pdf", "invoice": broken, "status": "completed",
         "source_sha256": None, "error": None, "checks": None},
        record(good),
    ])
    by_id = {r["file_id"]: r for r in results}
    assert by_id["bad.pdf"]["result"] == "ESCALAR"
    assert by_id["good.pdf"]["result"] == "PAGAR"


def test_external_history_retained():
    m = master()
    m["seen_invoice_keys"] = {("INV1", "P1")}
    m["seen_amount_date"] = {(Decimal(121), "2026-09-01")}
    result = DecisionEngine(frozen_rules(), m).decide([record()])[0]
    assert result["result"] == "NO PAGAR"


def test_inputs_unchanged_after_decide():
    rules = frozen_rules()
    m = master()
    rec = record()
    snapshot_rules = copy.deepcopy(rules)
    snapshot_master = copy.deepcopy(m)
    snapshot_rec = copy.deepcopy(rec)
    eng = DecisionEngine(rules, m)
    eng.decide([rec])
    assert rules == snapshot_rules
    assert m == snapshot_master
    assert rec == snapshot_rec


def test_store_rule_id_schema_supported():
    rules = frozen_rules()
    store = {
        "store_schema_version": "1.1",
        "ruleset_version": "v3",
        "policy_id": "balanced",
        "precedence": ["NO_PAGAR", "ESCALAR", "PAGAR"],
        "rules": [
            {**{k: v for k, v in r.items() if k != "id"}, "rule_id": r["id"]}
            for r in rules["rules"]
        ],
    }
    result = DecisionEngine(store, master()).decide([record()])[0]
    assert result["result"] == "PAGAR"
    assert all(c["rule_id"] for c in result["checks"]
               if c["canonical"] != "READINESS")


def test_imported_status_is_trusted_boundary():
    result = decide_one(status="imported")
    assert result["input_status"] == "imported"
    assert result["result"] == "PAGAR"


def test_result_row_shape():
    result = decide_one()
    for key in ("schema_version", "file_id", "result", "ruleset_version",
                "policy_id", "as_of", "engine_version", "ruleset_sha256",
                "master_sha256", "invoice_sha256", "source_sha256",
                "normalized_invoice", "input_status", "field_sources", "checks"):
        assert key in result
    assert result["schema_version"] == "1.0"
    assert result["engine_version"] == "1.0"
    assert result["as_of"] == AS_OF
    assert result["normalized_invoice"]["total"] == "121"
    assert result["result"] in PUBLIC_RESULT.values()


def test_adapter_exact_nif_vendor_resolution():
    inv, issues = adapt_invoice(good_invoice(), master(), _contracts())
    assert inv["vendor_id"] == "P1"
    assert issues == []


def test_adapter_ambiguous_nif_never_chooses():
    proveedores = copy.deepcopy(PROVEEDORES)
    proveedores["P2"] = dict(proveedores["P1"], id="P2")
    m = master_from_lookups(proveedores, PEDIDOS, erp_snapshot(), as_of=AS_OF)
    inv, issues = adapt_invoice(good_invoice(), m, _contracts())
    assert inv["vendor_id"] is None
    assert any(i["code"] == "vendor_ambiguous" for i in issues)


def _contracts():
    from ingestion.contracts import Contracts

    return Contracts(None)


def test_as_of_requires_exact_iso():
    with pytest.raises(ValueError):
        master_from_lookups(PROVEEDORES, PEDIDOS, erp_snapshot(), as_of="19/09/2026")
    with pytest.raises(ValueError):
        master_from_lookups(PROVEEDORES, PEDIDOS, erp_snapshot(), as_of="2026-9-9")


def test_malformed_snapshot_rejected():
    bad = erp_snapshot()
    bad["records"].append(dict(bad["records"][0]))
    with pytest.raises(ValueError):
        master_from_lookups(PROVEEDORES, PEDIDOS, bad, as_of=AS_OF)
    with pytest.raises(ValueError):
        master_from_lookups(PROVEEDORES, PEDIDOS, {"schema_version": "2.0"},
                            as_of=AS_OF)


def test_conflicting_erp_rows_escalate_not_last_write():
    snap = erp_snapshot()
    snap["records"].append({
        "id": "AS2", "fecha": "02/09/2026", "proveedor": "P1",
        "nif": "B12345678", "pedido": "PO1", "importe": "555,00",
        "estado": "PENDIENTE",
    })
    result = decide_one(snapshot=snap)
    assert result["result"] == "ESCALAR"

def test_hard_duplicates_no_erp_still_no_pagar():
    a = good_invoice(file_id="a.pdf")
    b = good_invoice(file_id="b.pdf")
    m = master_from_lookups(PROVEEDORES, PEDIDOS, None, as_of=AS_OF)
    results = DecisionEngine(frozen_rules(), m).decide([record(a), record(b)])
    assert [r["result"] for r in results] == ["NO PAGAR", "NO PAGAR"]


def test_known_history_no_erp_still_no_pagar():
    m = master_from_lookups(PROVEEDORES, PEDIDOS, None, as_of=AS_OF)
    m["seen_invoice_keys"] = {("INV1", "P1")}
    result = DecisionEngine(frozen_rules(), m).decide([record()])[0]
    assert result["result"] == "NO PAGAR"


def test_soft_duplicate_verdict_fail_respected():
    rules = frozen_rules(DUPLICATES={
        "params": {"require_erp_pending": True, "soft_duplicate_verdict": "FAIL"},
    })
    a = good_invoice(file_id="a.pdf", invoice_number="INV-A")
    b = good_invoice(file_id="b.pdf", invoice_number="INV-B")
    results = DecisionEngine(rules, master()).decide([record(a), record(b)])
    assert [r["result"] for r in results] == ["NO PAGAR", "NO PAGAR"]


def test_readiness_record_does_not_poison_good_duplicate_group():
    good = good_invoice(file_id="good.pdf", invoice_number="INV-X")
    bad = good_invoice(file_id="bad.pdf", invoice_number="INV-X")
    bad["currency"] = None
    results = engine().decide([
        record(bad),
        record(good),
    ])
    by_id = {r["file_id"]: r for r in results}
    assert by_id["bad.pdf"]["result"] == "ESCALAR"
    assert by_id["good.pdf"]["result"] == "PAGAR"


def test_invoice_id_mismatch_gates_per_file():
    inv = good_invoice()
    rec = {
        "file_id": "other.pdf", "invoice": inv, "status": "completed",
        "source_sha256": None, "error": None, "checks": None,
    }
    results = engine().decide([rec, record(good_invoice(file_id="z.pdf"))])
    by_id = {r["file_id"]: r for r in results}
    assert by_id["other.pdf"]["result"] == "ESCALAR"
    assert by_id["z.pdf"]["result"] == "PAGAR"


@pytest.mark.parametrize("checks", [{"ok": "yes"}, {}, ["ok"], "yes"])
def test_malformed_checks_gate(checks):
    result = decide_one(checks=checks)
    assert result["result"] == "ESCALAR"


def test_false_extraction_check_gates():
    result = decide_one(checks={"interpretation_valid": False})
    assert result["result"] == "ESCALAR"


def test_input_problem_preserved_in_gate_reason():
    rec = {
        "file_id": "x.pdf", "invoice": None, "status": "failed",
        "source_sha256": None, "error": {"code": "input"},
        "checks": None, "input_problem": "artifact hash mismatch: stage3/x.json",
    }
    result = engine().decide([rec])[0]
    assert result["result"] == "ESCALAR"
    assert "hash mismatch" in result["checks"][0]["reason"]


def test_gated_row_keeps_hashes_and_artifacts():
    inv = good_invoice()
    rec = {
        "file_id": "good.pdf", "invoice": inv, "status": "failed",
        "source_sha256": "abc", "error": {"code": "x"},
        "checks": None,
        "artifacts": {"invoice": "stage3/a.json"},
        "artifact_hashes": {"invoice": "h"},
    }
    result = engine().decide([rec])[0]
    assert result["result"] == "ESCALAR"
    assert result["invoice_sha256"]
    assert result["artifacts"] == {"invoice": "stage3/a.json"}
    assert result["artifact_hashes"] == {"invoice": "h"}


def test_ruleset_hash_uses_frozen_input():
    rules = frozen_rules()
    from ingestion.contracts import canonical_bytes, digest

    eng = engine(rules)
    assert eng.ruleset_sha256 == digest(canonical_bytes(rules))


def test_master_hash_recomputed_after_history_change():
    m = master()
    first = DecisionEngine(frozen_rules(), m).master_sha256
    m["seen_invoice_keys"] = {("INV1", "P1")}
    second = DecisionEngine(frozen_rules(), m).master_sha256
    assert first != second


def test_master_today_as_of_disagreement_rejected():
    m = master()
    m["today"] = "2026-01-01"
    with pytest.raises(ValueError):
        DecisionEngine(frozen_rules(), m)


@pytest.mark.parametrize(
    "params",
    [
        {"require_iban_match": "yes"},
        {"tolerance_eur": float("nan")},
        {"tolerance_eur": -1},
        {"tolerance_eur": True},
        {"soft_duplicate_verdict": "PASS"},
        {"allowed_currencies": ["EUR", ""]},
        {"required_fields": "nif"},
    ],
)
def test_invalid_params_fail_config(params):
    rules = frozen_rules(AMOUNT={"params": params})
    with pytest.raises(ValueError):
        engine(rules)


def test_duplicate_enabled_canonical_rejected():
    rules = frozen_rules()
    extra = dict(rules["rules"][0])
    extra["id"] = "R1b"
    rules["rules"].append(extra)
    with pytest.raises(ValueError):
        engine(rules)


def test_ambiguous_nif_excluded_from_vendor_lookup():
    proveedores = copy.deepcopy(PROVEEDORES)
    proveedores["P2"] = dict(proveedores["P1"], id="P2")
    m = master_from_lookups(proveedores, PEDIDOS, erp_snapshot(), as_of=AS_OF)
    assert "B12345678" not in m["proveedores_by_nif"]
    result = DecisionEngine(frozen_rules(), m).decide([record()])[0]
    assert result["result"] == "ESCALAR"


def test_blank_po_identity_escalates():
    pedidos = copy.deepcopy(PEDIDOS)
    pedidos["PO1"]["proveedor_id"] = ""
    m = master_from_lookups(PROVEEDORES, pedidos, erp_snapshot(), as_of=AS_OF)
    result = DecisionEngine(frozen_rules(), m).decide([record()])[0]
    assert result["result"] == "ESCALAR"


def test_blank_erp_identity_never_matches():
    snap = erp_snapshot(proveedor="", nif="")
    result = decide_one(snapshot=snap)
    assert result["result"] == "ESCALAR"


def test_table_key_row_mismatch_rejected():
    pedidos = {"PO1": dict(PEDIDOS["PO1"], pedido="PO9")}
    with pytest.raises(ValueError):
        master_from_lookups(PROVEEDORES, pedidos, erp_snapshot(), as_of=AS_OF)


def test_nonfinite_master_amount_rejected():
    pedidos = {"PO1": dict(PEDIDOS["PO1"], importe_total=Decimal("NaN"))}
    with pytest.raises(ValueError):
        master_from_lookups(PROVEEDORES, pedidos, erp_snapshot(), as_of=AS_OF)


def test_snapshot_fetched_at_requires_utc():
    snap = erp_snapshot()
    snap["fetched_at"] = "2026-09-19T08:00:00+02:00"
    with pytest.raises(ValueError):
        master_from_lookups(PROVEEDORES, PEDIDOS, snap, as_of=AS_OF)


@pytest.mark.parametrize("condiciones", [None, "unknown"])
def test_vendor_terms_unknown_escalates(condiciones):
    proveedores = copy.deepcopy(PROVEEDORES)
    proveedores["P1"]["condiciones"] = condiciones
    m = master_from_lookups(proveedores, PEDIDOS, erp_snapshot(), as_of=AS_OF)
    result = DecisionEngine(frozen_rules(), m).decide([record()])[0]
    assert result["result"] == "ESCALAR"
    assert any(c.get("code") == "vendor_terms_unknown" for c in result["checks"])


def test_vendor_terms_valid_pays():
    assert decide_one()["result"] == "PAGAR"


def test_multiple_canonical_none_rules_review_not_abort():
    rules = frozen_rules()
    for rid in ("NEW_A", "NEW_B"):
        rules["rules"].append({
            "id": rid,
            "origin": "agent",
            "canonical": None,
            "enabled": True,
            "on_fail": "ESCALAR",
            "params": {},
            "source_refs": [],
        })
    result = decide_one(rules=rules)
    assert result["result"] == "ESCALAR"
    rows = [c for c in result["checks"] if c["rule_id"] in ("NEW_A", "NEW_B")]
    assert len(rows) == 2
    assert all(r["verdict"] == "NEEDS_REVIEW" for r in rows)


def test_same_order_distinct_invoices_escalate_both():
    a = good_invoice(file_id="a.pdf", invoice_number="INV-A",
                     issue_date="2026-09-01")
    b = good_invoice(file_id="b.pdf", invoice_number="INV-B",
                     issue_date="2026-09-05")
    results = engine().decide([record(a), record(b)])
    assert [r["result"] for r in results] == ["ESCALAR", "ESCALAR"]
    for r in results:
        assert any("same order" in c["reason"] for c in r["checks"])


def test_distinct_orders_do_not_group():
    pedidos = copy.deepcopy(PEDIDOS)
    pedidos["PO2"] = dict(pedidos["PO1"], pedido="PO2")
    snap = erp_snapshot()
    snap["records"].append(dict(snap["records"][0], id="AS2", pedido="PO2"))
    m = master_from_lookups(PROVEEDORES, pedidos, snap, as_of=AS_OF)
    a = good_invoice(file_id="a.pdf", invoice_number="INV-A")
    b = good_invoice(file_id="b.pdf", invoice_number="INV-B",
                     purchase_order_reference="PO2", issue_date="2026-09-05")
    results = DecisionEngine(frozen_rules(), m).decide([record(a), record(b)])
    assert [r["result"] for r in results] == ["PAGAR", "PAGAR"]
