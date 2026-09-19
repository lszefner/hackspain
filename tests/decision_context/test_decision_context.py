from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest

from ingestion.contracts import blank_invoice, canonical_bytes, digest
from rules_ingestion import decision_context as dc

CAPTURED = "2026-09-19T10:00:00Z"
EVAL_DATE = "2026-09-19"


def make_reading(file_id, block_ids=("b1",)):
    return {
        "schema_version": "0.1",
        "file_id": file_id,
        "capabilities": {"block_kinds": True, "tables": True, "layout": False},
        "pages": [{
            "page": 1,
            "blocks": [
                {"id": bid, "kind": "paragraph", "text": f"text {bid}",
                 "rows": [], "uncertainties": []}
                for bid in block_ids
            ],
            "non_text_elements": [],
        }],
    }


def _leaves(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _leaves(child, f"{prefix}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _leaves(child, f"{prefix}/{index}")
    else:
        yield prefix, value


def make_evidence(invoice, block="b1"):
    links = {}
    for pointer, value in _leaves(invoice):
        if value is None:
            continue
        if pointer in ("/schema_version", "/file_id") or pointer.startswith("/issues"):
            continue
        if pointer.startswith("/lines/") and pointer.endswith("/position"):
            continue
        links[pointer] = [{"page": 1, "reference_ids": [block]}]
    return links


def make_invoice(file_id="f-clean", **overrides):
    invoice = blank_invoice(file_id)
    invoice.update({
        "document_type": "invoice",
        "invoice_number": "INV-1",
        "issue_date": "2026-09-01",
        "purchase_order_reference": "PO-1",
        "currency": "EUR",
        "lines": [{"position": 1, "description": "item", "quantity": "1",
                   "amount": "100.00"}],
        "taxes": [{"label": "IVA 21%", "rate_percent": "21", "amount": "21.00"}],
        "totals": {"taxable_base": "100.00", "total": "121.00"},
    })
    invoice["supplier"].update({"name": "Proveedor", "tax_id": "B12345678"})
    invoice["payment"]["iban"] = "ES9121000418450200051332"
    for key, value in overrides.items():
        invoice[key] = value
    return invoice


def make_outcome(invoice=None, reading=True, evidence=True, file_id="f-clean"):
    invoice = invoice if invoice is not None else make_invoice(file_id)
    reading_doc = make_reading(file_id) if reading else None
    evidence_doc = make_evidence(invoice) if evidence else None
    return {
        "invoice": invoice,
        "reading": reading_doc,
        "evidence": evidence_doc,
        "status": "completed",
        "checks": {},
        "file_id": file_id,
    }


def snap(kind, payload, scopes=(), availability="available"):
    return dc.SourceSnapshot(
        kind=kind, payload=payload, captured_at=CAPTURED,
        asserted_by="configured-source", authoritative_for=scopes,
        scope="test snapshot", availability=availability)


SUPPLIER_SCOPES = ("supplier.identity", "supplier.bank_details",
                   "supplier.payment_terms", "supplier.active")
ORDER_SCOPES = ("order.identity", "order.amount", "order.currency")


def supplier_record(nif="B12345678", iban="ES9121000418450200051332", **kw):
    record = {"id": "P042", "nif": nif, "iban": iban, "condiciones": "30 dias"}
    record.update(kw)
    return record


def make_snapshots(supplier_records=None, order_records=None, erp=None,
                   histories=True):
    snapshots = {
        "suppliers": snap("supplier_master", {
            "records": supplier_records if supplier_records is not None
            else [supplier_record()]}, SUPPLIER_SCOPES),
        "orders": snap("order_master", {
            "records": order_records if order_records is not None else [{
                "pedido": "PO-1", "proveedor_id": "P042",
                "nif": "B12345678", "importe_total": "121.00",
                "currency": "EUR"}]}, ORDER_SCOPES),
        "erp": snap("erp", erp if erp is not None else {
            "records": [{"pedido": "PO-1", "estado": "PENDIENTE"}],
            "complete": True}, ("order.payment_status",)),
    }
    if histories:
        for name in ("processed", "approved", "paid"):
            snapshots[name] = snap(
                "history",
                {"kind": name, "records": [], "complete": True},
                (f"history.{name}",))
    return snapshots


def vendor_rule(**params):
    merged = {"require_nif_in_master": True, "require_iban_match": True}
    merged.update(params)
    return {
        "id": "R_VENDOR", "canonical": "VENDOR", "enabled": True,
        "on_fail": "ESCALAR", "input_fields": [], "params": merged,
        "condition": {"kind": "python_check",
                      "module": "rules_ingestion.checks",
                      "function": "check_vendor"},
    }


def make_ruleset(rules=None):
    return {
        "schema_version": "2.0",
        "ruleset_version": "v-test",
        "policy_id": "balanced",
        "precedence": ["NO_PAGAR", "ESCALAR", "PAGAR"],
        "rules": rules if rules is not None else [vendor_rule()],
    }


def build(outcome=None, snapshots=None, ruleset=None):
    return dc.build_context(
        outcome if outcome is not None else make_outcome(),
        snapshots=snapshots if snapshots is not None else make_snapshots(),
        ruleset=ruleset if ruleset is not None else make_ruleset(),
        evaluation_date=EVAL_DATE, captured_at=CAPTURED)


def test_clean_supported_rule_ready_and_pagar():
    bundle = build()
    ctx = bundle.context
    assert ctx["preflight"] == {"data_status": "ready",
                              "execution_status": "supported"}
    assert not [f for f in ctx["findings"] if f["severity"] == "blocking"]
    result = dc.evaluate_context(bundle)
    assert result["decision"] == "PAGAR"
    assert result["context_id"] == ctx["context_id"]
    assert result["checks"][0]["verdict"] == "PASS"
    assert result["checks"][0]["rule_id"] == "R_VENDOR"


def test_iban_mismatch_fails_and_escalates():
    invoice = make_invoice()
    invoice["payment"]["iban"] = "ES6621000418401234567891"
    bundle = build(outcome=make_outcome(invoice))
    codes = {f["code"] for f in bundle.context["findings"]}
    assert "INVOICE_MASTER_IBAN_DIFFER" in codes
    result = dc.evaluate_context(bundle)
    assert result["checks"][0]["verdict"] == "FAIL"
    assert result["decision"] == "ESCALAR"


def test_artifacts_rehash_and_pointers_resolve():
    bundle = build()
    ctx = bundle.context
    for source in ctx["sources"].values():
        if source["availability"] == "unavailable":
            assert source["artifact_ref"] is None and source["sha256"] is None
            continue
        ref = source["artifact_ref"]
        assert digest(bundle.artifacts[ref]) == source["sha256"]
    docs = {}
    for source_id, source in ctx["sources"].items():
        ref = source["artifact_ref"]
        docs[source_id] = json.loads(bundle.artifacts[ref]) if ref else None

    def check(ref):
        if "field" in ref:
            assert ref["field"] in ctx["fields"]
        else:
            dc._pointer_get(docs[ref["source"]], ref["pointer"])

    for fact in ctx["fields"].values():
        assert fact["transform"].count("/") == 1
        for ref in fact["inputs"]:
            check(ref)
    for finding in ctx["findings"]:
        for ref in finding["refs"]:
            check(ref)


def test_inputs_not_mutated_and_stable_id():
    outcome = make_outcome()
    snapshots = make_snapshots()
    ruleset = make_ruleset()
    originals = copy.deepcopy((outcome, snapshots, ruleset))
    first = build(outcome, snapshots, ruleset)
    second = build(copy.deepcopy(outcome), copy.deepcopy(snapshots),
                   copy.deepcopy(ruleset))
    assert first.context["context_id"] == second.context["context_id"]
    assert (outcome, snapshots, ruleset) == originals


def test_missing_currency_stays_null():
    invoice = make_invoice(currency=None)
    bundle = build(outcome=make_outcome(invoice))
    fact = bundle.context["fields"]["invoice.currency"]
    assert fact["value"] is None and fact["state"] == "missing"
    inv, _master = dc.project_legacy(bundle.context)
    assert inv["currency"] is None


def test_missing_line_not_filtered():
    invoice = make_invoice()
    invoice["lines"] = [
        {"position": 1, "description": "a", "quantity": "1", "amount": "100.00"},
        {"position": 2, "description": "b", "quantity": "1", "amount": None},
    ]
    bundle = build(outcome=make_outcome(invoice))
    fields = bundle.context["fields"]
    assert fields["invoice.lines.0.amount"]["state"] == "present"
    assert fields["invoice.lines.1.amount"]["state"] == "missing"
    assert fields["invoice.line_amounts"]["state"] == "missing"
    assert fields["invoice.line_amounts"]["value"] is None


def test_mixed_vat_withholding_blocks_reconciliation():
    invoice = make_invoice()
    invoice["taxes"] = [
        {"label": "IVA 21%", "rate_percent": "21", "amount": "21.00"},
        {"label": "IRPF 15%", "rate_percent": "-15", "amount": "-15.00"},
    ]
    invoice["totals"] = {"taxable_base": "100.00", "total": "106.00"}
    rule = {
        "id": "R_AMOUNT", "canonical": "AMOUNT", "enabled": True,
        "on_fail": "ESCALAR", "input_fields": [],
        "params": {"tolerance_eur": "0.01", "check_line_items_sum": False,
                   "check_total_is_base_plus_iva": True,
                   "check_matches_pedido": False},
        "condition": {"kind": "python_check",
                      "module": "rules_ingestion.checks",
                      "function": "check_amount"},
    }
    bundle = build(outcome=make_outcome(invoice),
                   ruleset=make_ruleset([rule]))
    fields = bundle.context["fields"]
    assert fields["invoice.taxes.0.kind"]["value"] == "vat"
    assert fields["invoice.taxes.1.kind"]["value"] == "withholding"
    assert fields["invoice.vat_amount"]["value"] == "21.00"
    binding = bundle.context["rule_bindings"][0]
    assert binding["execution_status"] == "unsupported"
    assert "UNSUPPORTED_TAX_RECONCILIATION" in binding["reason_codes"]
    assert dc.evaluate_context(bundle)["decision"] == "ESCALAR"


def test_unknown_and_absent_taxes_are_not_zero():
    unknown = make_invoice()
    unknown["taxes"] = [{"label": "IMPUESTO ESPECIAL", "rate_percent": "5",
                         "amount": "5.00"}]
    bundle = build(outcome=make_outcome(unknown))
    fields = bundle.context["fields"]
    assert fields["invoice.taxes.0.kind"]["value"] == "unknown"
    assert fields["invoice.vat_amount"]["state"] == "ambiguous"
    assert fields["invoice.vat_amount"]["value"] is None

    none = make_invoice(taxes=[])
    bundle = build(outcome=make_outcome(none))
    assert bundle.context["fields"]["invoice.vat_amount"]["state"] == "missing"


def test_duplicate_supplier_conflicts_null_identity():
    dup_ids = [supplier_record(), supplier_record(id="P999")]
    bundle = build(snapshots=make_snapshots(supplier_records=dup_ids))
    assert bundle.context["fields"]["supplier.id"]["state"] == "ambiguous"
    assert bundle.context["fields"]["supplier.id"]["value"] is None

    other_nif = [supplier_record(), supplier_record(nif="A11111111")]
    other_nif[1]["id"] = "P042"
    bundle = build(snapshots=make_snapshots(supplier_records=other_nif))
    assert bundle.context["fields"]["supplier.id"]["state"] == "ambiguous"

    identical = [supplier_record(), dict(supplier_record())]
    bundle = build(snapshots=make_snapshots(supplier_records=identical))
    assert bundle.context["fields"]["supplier.id"]["value"] == "P042"


def test_order_owner_mismatch_blocks():
    orders = [{"pedido": "PO-1", "proveedor_id": "OTHER", "nif": "X",
               "importe_total": "121.00", "currency": "EUR"}]
    bundle = build(snapshots=make_snapshots(order_records=orders))
    assert bundle.context["fields"]["order.supplier_id"]["value"] == "OTHER"
    codes = {f["code"] for f in bundle.context["findings"]}
    assert "ORDER_SUPPLIER_MISMATCH" in codes


def test_ambiguous_issue_date_remains_null():
    invoice = make_invoice()
    invoice["issues"] = [{"field": "/issue_date", "kind": "ambiguous",
                          "raw_text": "03/04/2026",
                          "candidates": ["2026-04-03", "2026-03-04"]}]
    bundle = build(outcome=make_outcome(invoice))
    fact = bundle.context["fields"]["invoice.issue_date"]
    assert fact["value"] is None and fact["state"] == "ambiguous"
    assert fact["extraction_quality"] == "uncertain"


def test_missing_or_bad_evidence_blocks():
    bundle = build(outcome=make_outcome(evidence=False))
    codes = {f["code"] for f in bundle.context["findings"]}
    assert "EVIDENCE_UNAVAILABLE" in codes
    assert "MISSING_EVIDENCE_LINK" in codes
    assert bundle.context["preflight"]["data_status"] == "blocked"

    outcome = make_outcome()
    outcome["evidence"]["/invoice_number"] = [
        {"page": 1, "reference_ids": ["ghost"]}]
    bundle = build(outcome=outcome)
    codes = {f["code"] for f in bundle.context["findings"]}
    assert "EXTRACTION_CHECK_FAILED_EVIDENCE_REFERENCES" in codes


def test_missing_reading_blocks():
    bundle = build(outcome=make_outcome(reading=False))
    codes = {f["code"] for f in bundle.context["findings"]}
    assert "READING_UNAVAILABLE" in codes
    assert bundle.context["sources"]["reading"]["availability"] == "unavailable"


def test_erp_unavailable_partial_absent_not_pending():
    for erp in (
        snap("erp", None, ("order.payment_status",), "unavailable"),
        snap("erp", {"records": [{"pedido": "PO-1", "estado": "PENDIENTE"}],
                     "complete": True}, ("order.payment_status",), "partial"),
        snap("erp", {"records": [{"pedido": "PO-1", "estado": "PENDIENTE"}],
                     "complete": False}, ("order.payment_status",)),
        snap("erp", {"records": [], "complete": True},
             ("order.payment_status",)),
    ):
        snapshots = make_snapshots()
        snapshots["erp"] = erp
        bundle = build(snapshots=snapshots)
        fact = bundle.context["fields"]["erp.order_payment_status"]
        assert fact["value"] != "PENDIENTE"
        assert fact["state"] != "present"


def test_history_excludes_self_and_never_paid():
    records = [{
        "file_id": "f-clean", "invoice_number": "INV-1",
        "supplier_id": "P042", "total": "121.00", "currency": "EUR",
        "issue_date": "2026-09-01",
    }, {
        "file_id": "f-other", "invoice_number": "INV-1",
        "supplier_id": "P042", "total": "121.00", "currency": "EUR",
        "issue_date": "2026-09-01",
    }]
    snapshots = make_snapshots()
    snapshots["processed"] = snap(
        "history",
        {"kind": "processed", "records": records, "complete": True},
        ("history.processed",))
    bundle = build(snapshots=snapshots)
    fields = bundle.context["fields"]
    processed = fields["history.processed_matches"]["value"]
    assert processed == [{"source": "processed", "pointer": "/records/1"}]
    assert fields["history.paid_matches"]["value"] == []

    snapshots["processed"] = snap(
        "history",
        {"kind": "processed",
         "records": [{"file_id": "x", "supplier_id": None, "currency": None}],
         "complete": True},
        ("history.processed",))
    bundle = build(snapshots=snapshots)
    assert bundle.context["fields"]["history.processed_matches"]["state"] == "ambiguous"


def test_history_kind_mismatch_and_unusable_identity():
    snapshots = make_snapshots()
    snapshots["paid"] = snap(
        "history",
        {"kind": "processed", "records": [], "complete": True},
        ("history.paid",))
    bundle = build(snapshots=snapshots)
    assert bundle.context["fields"]["history.paid_matches"]["state"] == "unavailable"
    codes = {f["code"] for f in bundle.context["findings"]}
    assert "HISTORY_KIND_MISMATCH" in codes

    paid_record = {
        "file_id": "f-clean", "invoice_number": "INV-1",
        "supplier_id": "P042", "total": "121.00", "currency": "EUR",
        "issue_date": "2026-09-01",
    }
    snapshots = make_snapshots()
    snapshots["paid"] = snap(
        "history",
        {"kind": "paid", "records": [paid_record], "complete": True},
        ("history.paid",))
    bundle = build(snapshots=snapshots)
    assert bundle.context["fields"]["history.paid_matches"]["value"] == [
        {"source": "paid", "pointer": "/records/0"}]

    snapshots = make_snapshots()
    snapshots["processed"] = snap(
        "history",
        {"kind": "processed", "records": [], "complete": True},
        ("history.processed",))
    bundle = build(outcome=make_outcome(make_invoice(currency=None)),
                   snapshots=snapshots)
    fact = bundle.context["fields"]["history.processed_matches"]
    assert fact["state"] == "missing" and fact["value"] is None

    snapshots = make_snapshots()
    snapshots["processed"] = snap(
        "history",
        {"kind": "processed",
         "records": [{"file_id": "x", "invoice_number": "INV-9",
                      "supplier_id": "P042", "total": None,
                      "currency": "EUR", "issue_date": None}],
         "complete": True},
        ("history.processed",))
    bundle = build(snapshots=snapshots)
    assert bundle.context["fields"]["history.processed_matches"]["state"] == "ambiguous"


def test_credit_note_blocked():
    bundle = build(outcome=make_outcome(make_invoice(document_type="credit_note")))
    codes = {f["code"] for f in bundle.context["findings"]}
    assert "DOCUMENT_TYPE_UNSUPPORTED" in codes
    assert dc.evaluate_context(bundle)["decision"] != "PAGAR"


def test_unknown_rule_and_empty_rules_cannot_approve():
    unknown = vendor_rule()
    unknown["id"] = "R_NEW"
    unknown["canonical"] = None
    unknown["condition"] = {"kind": "structured", "clauses": [
        {"field": "total", "op": ">", "value": 5}]}
    bundle = build(ruleset=make_ruleset([unknown]))
    binding = bundle.context["rule_bindings"][0]
    assert binding["execution_status"] == "unsupported"
    assert dc.evaluate_context(bundle)["decision"] == "ESCALAR"

    bundle = build(ruleset=make_ruleset([]))
    codes = {f["code"] for f in bundle.context["findings"]}
    assert "NO_ENABLED_RULES" in codes
    assert dc.evaluate_context(bundle)["decision"] == "ESCALAR"


def test_unimplemented_flags_prevent_pass():
    bundle = build(ruleset=make_ruleset([vendor_rule(require_active=True)]))
    binding = bundle.context["rule_bindings"][0]
    assert binding["execution_status"] == "unsupported"
    assert "UNIMPLEMENTED_FLAG" in binding["reason_codes"]
    assert dc.evaluate_context(bundle)["decision"] == "ESCALAR"

    amount = {
        "id": "R_AMOUNT", "canonical": "AMOUNT", "enabled": True,
        "on_fail": "ESCALAR", "input_fields": [],
        "params": {"check_iva": True, "check_line_items_sum": False,
                   "check_total_is_base_plus_iva": False,
                   "check_matches_pedido": False},
        "condition": {"kind": "python_check",
                      "module": "rules_ingestion.checks",
                      "function": "check_amount"},
    }
    bundle = build(ruleset=make_ruleset([amount]))
    assert bundle.context["rule_bindings"][0]["execution_status"] == "unsupported"


def test_unauthorized_scope_rejected():
    snapshots = make_snapshots()
    snapshots["suppliers"] = snap("supplier_master", {"records": []},
                                  ("order.payment_status",))
    with pytest.raises(dc.ContextError):
        build(snapshots=snapshots)
    snapshots = make_snapshots()
    snapshots["invoice"] = snap("invoice", {})
    with pytest.raises(dc.ContextError):
        build(snapshots=snapshots)


def test_malformed_rules_and_params():
    with pytest.raises(dc.ContextError):
        build(ruleset=make_ruleset([{"canonical": "VENDOR"}]))
    dup = [vendor_rule(), vendor_rule()]
    with pytest.raises(dc.ContextError):
        build(ruleset=make_ruleset(dup))

    bad = vendor_rule(tolerance_eur=-1)
    bad["params"]["bogus_param"] = True
    bundle = build(ruleset=make_ruleset([bad]))
    codes = bundle.context["rule_bindings"][0]["reason_codes"]
    assert "UNKNOWN_PARAMETER" in codes

    missing = {
        "id": "R_MISSING", "canonical": "MISSING", "enabled": True,
        "on_fail": "ESCALAR", "input_fields": [],
        "params": {"required_fields": "nif"},
        "condition": {"kind": "python_check",
                      "module": "rules_ingestion.checks",
                      "function": "check_missing"},
    }
    bundle = build(ruleset=make_ruleset([missing]))
    assert bundle.context["rule_bindings"][0]["execution_status"] == "unsupported"


def test_tamper_rejected():
    bundle = build()
    ctx = copy.deepcopy(bundle.context)
    ctx["sources"]["invoice"]["sha256"] = "0" * 64
    with pytest.raises(dc.ContextError):
        dc.validate_context(ctx, bundle.artifacts)

    ctx = copy.deepcopy(bundle.context)
    ctx["fields"]["invoice.total"]["inputs"] = [
        {"source": "invoice", "pointer": "/totals/nope"}]
    ctx["context_id"] = "dc_" + digest(canonical_bytes(
        {k: v for k, v in ctx.items() if k != "context_id"}))
    with pytest.raises(dc.ContextError):
        dc.validate_context(ctx, bundle.artifacts)

    ctx = copy.deepcopy(bundle.context)
    ctx["fields"]["invoice.number"]["inputs"] = [
        {"field": "invoice.number"}]
    ctx["context_id"] = "dc_" + digest(canonical_bytes(
        {k: v for k, v in ctx.items() if k != "context_id"}))
    with pytest.raises(dc.ContextError):
        dc.validate_context(ctx, bundle.artifacts)

    ctx = copy.deepcopy(bundle.context)
    ctx["preflight"]["data_status"] = "ready" if ctx["preflight"]["data_status"] == "blocked" else "blocked"
    ctx["context_id"] = "dc_" + digest(canonical_bytes(
        {k: v for k, v in ctx.items() if k != "context_id"}))
    with pytest.raises(dc.ContextError):
        dc.validate_context(ctx, bundle.artifacts)

    ctx = copy.deepcopy(bundle.context)
    ctx["context_id"] = "dc_" + "1" * 64
    with pytest.raises(dc.ContextError):
        dc.validate_context(ctx, bundle.artifacts)


def test_exact_decimal_and_no_floats():
    invoice = make_invoice()
    invoice["taxes"] = [
        {"label": "IVA 21%", "rate_percent": "21", "amount": "0.10"},
        {"label": "IVA 10%", "rate_percent": "10", "amount": "0.20"},
    ]
    invoice["totals"] = {"taxable_base": "100.00", "total": "100.30"}
    bundle = build(outcome=make_outcome(invoice))
    assert bundle.context["fields"]["invoice.vat_amount"]["value"] == "0.3" \
        or bundle.context["fields"]["invoice.vat_amount"]["value"] == "0.30"
    assert Decimal(bundle.context["fields"]["invoice.vat_amount"]["value"]) == Decimal("0.30")

    snapshots = make_snapshots()
    snapshots["suppliers"] = snap("supplier_master",
                                  {"records": [{"id": "P042",
                                                "nif": "B12345678",
                                                "iban": "ES9121000418450200051332",
                                                "importe": Decimal("12.50")}]},
                                  SUPPLIER_SCOPES)
    bundle = build(snapshots=snapshots)
    ref = bundle.context["sources"]["suppliers"]["artifact_ref"]
    assert b"12.50" in bundle.artifacts[ref]

    ctx = copy.deepcopy(bundle.context)
    ctx["fields"]["invoice.total"]["value"] = 1.5
    ctx["context_id"] = "dc_" + digest(canonical_bytes(
        {k: v for k, v in ctx.items() if k != "context_id"}))
    with pytest.raises(dc.ContextError):
        dc.validate_context(ctx, bundle.artifacts)


def test_duplicates_rule_binds_but_never_executes():
    dup = {
        "id": "R_DUP", "canonical": "DUPLICATES", "enabled": True,
        "on_fail": "NO_PAGAR", "input_fields": ["invoice_number"],
        "params": {"hard_key": ["invoice_number", "vendor_id"],
                   "soft_key": ["amount", "date"],
                   "require_erp_pending": True},
        "condition": {"kind": "python_check",
                      "module": "rules_ingestion.checks",
                      "function": "check_duplicates"},
    }
    bundle = build(ruleset=make_ruleset([dup]))
    binding = bundle.context["rule_bindings"][0]
    assert binding["execution_status"] == "unsupported"
    assert "PROCESSED_HISTORY_POLICY_UNRESOLVED" in binding["reason_codes"]
    dep_fields = {d["field_id"] for d in binding["dependencies"]}
    assert "erp.order_payment_status" in dep_fields
    assert "history.processed_matches" in dep_fields
    assert dc.evaluate_context(bundle)["decision"] == "ESCALAR"


def test_missing_check_runs_on_known_missing():
    invoice = make_invoice(currency=None)
    missing = {
        "id": "R_MISSING", "canonical": "MISSING", "enabled": True,
        "on_fail": "ESCALAR", "input_fields": [],
        "params": {"required_fields": ["nif", "currency"]},
        "condition": {"kind": "python_check",
                      "module": "rules_ingestion.checks",
                      "function": "check_missing"},
    }
    bundle = build(outcome=make_outcome(invoice),
                   ruleset=make_ruleset([missing]))
    binding = bundle.context["rule_bindings"][0]
    assert binding["input_status"] == "ready"
    result = dc.evaluate_context(bundle)
    assert result["checks"][0]["verdict"] == "FAIL"


def test_replay_rejects_semantic_tampering():
    bundle = build()

    def recompute(ctx):
        ctx["context_id"] = "dc_" + digest(canonical_bytes(
            {k: v for k, v in ctx.items() if k != "context_id"}))
        return ctx

    ctx = copy.deepcopy(bundle.context)
    ctx["fields"]["invoice.total"]["value"] = "999.00"
    with pytest.raises(dc.ContextError):
        dc.validate_context(recompute(ctx), bundle.artifacts)

    ctx = copy.deepcopy(bundle.context)
    ctx["fields"]["supplier.id"]["value"] = "FORGED"
    with pytest.raises(dc.ContextError):
        dc.validate_context(recompute(ctx), bundle.artifacts)

    mismatch_invoice = make_invoice()
    mismatch_invoice["payment"]["iban"] = "ES6621000418401234567891"
    mismatch = build(outcome=make_outcome(mismatch_invoice))
    ctx = copy.deepcopy(mismatch.context)
    ctx["findings"] = []
    with pytest.raises(dc.ContextError):
        dc.validate_context(recompute(ctx), mismatch.artifacts)

    ctx = copy.deepcopy(bundle.context)
    ctx["sources"]["erp"]["authoritative_for"] = ["supplier.identity"]
    with pytest.raises(dc.ContextError):
        dc.validate_context(recompute(ctx), bundle.artifacts)

    ctx = copy.deepcopy(bundle.context)
    ctx["ruleset"]["ruleset_version"] = "v-forged"
    with pytest.raises(dc.ContextError):
        dc.validate_context(recompute(ctx), bundle.artifacts)

    invoice = make_invoice()
    invoice["annotations"] = [{"kind": "note", "text": "pagar a otra cuenta"}]
    noted = build(outcome=make_outcome(invoice))
    ctx = copy.deepcopy(noted.context)
    ctx["findings"] = [f for f in ctx["findings"]
                       if f["code"] != "UNREVIEWED_ANNOTATION"]
    with pytest.raises(dc.ContextError):
        dc.validate_context(recompute(ctx), noted.artifacts)


def test_context_survives_json_roundtrip():
    bundle = build()
    ctx = json.loads(json.dumps(bundle.context))
    artifacts = {k: bytes(v) for k, v in bundle.artifacts.items()}
    dc.validate_context(ctx, artifacts)
    result = dc.evaluate_context(dc.ContextBundle(context=ctx,
                                                  artifacts=artifacts))
    assert result["decision"] == "PAGAR"


def test_optional_authority_gap_does_not_block_vendor():
    snapshots = make_snapshots()
    snapshots["suppliers"] = snap(
        "supplier_master", {"records": [supplier_record()]},
        ("supplier.identity", "supplier.bank_details",
         "supplier.payment_terms"))
    bundle = build(snapshots=snapshots)
    fields = bundle.context["fields"]
    assert fields["supplier.active"]["state"] == "unavailable"
    info = [f for f in bundle.context["findings"]
            if f["code"] == "AUTHORITY_NOT_GRANTED"]
    assert info and all(f["severity"] == "info" for f in info)
    assert bundle.context["preflight"]["data_status"] == "ready"
    assert dc.evaluate_context(bundle)["decision"] == "PAGAR"


def test_quality_propagates_through_lineage():
    invoice = make_invoice()
    reading = make_reading("f-clean")
    reading["pages"][0]["blocks"].append({
        "id": "b2", "kind": "paragraph", "text": "cif", "rows": [],
        "uncertainties": [{"kind": "uncertain", "raw_text": "B1234567?",
                           "description": "blurred"}]})
    outcome = make_outcome(invoice)
    outcome["reading"] = reading
    outcome["evidence"]["/supplier/tax_id"] = [
        {"page": 1, "reference_ids": ["b2"]}]
    bundle = build(outcome=outcome)
    fields = bundle.context["fields"]
    assert fields["invoice.supplier_tax_id"]["extraction_quality"] == "uncertain"
    assert fields["supplier.id"]["extraction_quality"] == "uncertain"
    assert fields["supplier.iban"]["extraction_quality"] == "uncertain"

    invoice = make_invoice()
    invoice["issues"] = [{"field": "/taxes/0/label", "kind": "ambiguous",
                          "raw_text": "IVA?", "candidates": ["IVA", "IVA 0%"]}]
    bundle = build(outcome=make_outcome(invoice))
    assert bundle.context["fields"]["invoice.vat_amount"]["state"] == "ambiguous"
    assert bundle.context["fields"]["invoice.vat_amount"]["value"] is None

    invoice = make_invoice()
    reading = make_reading("f-clean")
    reading["pages"][0]["blocks"].append({
        "id": "b3", "kind": "paragraph", "text": "21.00", "rows": [],
        "uncertainties": [{"kind": "uncertain", "raw_text": "21.0?",
                           "description": "blurred"}]})
    outcome = make_outcome(invoice)
    outcome["reading"] = reading
    outcome["evidence"]["/taxes/0/amount"] = [
        {"page": 1, "reference_ids": ["b3"]}]
    bundle = build(outcome=outcome)
    vat = bundle.context["fields"]["invoice.vat_amount"]
    assert vat["state"] == "present"
    assert vat["extraction_quality"] == "uncertain"


def test_param_shape_edge_cases():
    for bad_params in ("nope", ["x"], None):
        rule = vendor_rule()
        rule["params"] = bad_params
        bundle = build(ruleset=make_ruleset([rule]))
        binding = bundle.context["rule_bindings"][0]
        if bad_params is None:
            assert "INVALID_PARAMETERS" in binding["reason_codes"]
        else:
            assert "INVALID_PARAMETERS" in binding["reason_codes"]
        assert binding["execution_status"] == "unsupported"
        assert dc.evaluate_context(bundle)["decision"] == "ESCALAR"

    rule = vendor_rule()
    rule["input_fields"] = ""
    bundle = build(ruleset=make_ruleset([rule]))
    assert "INVALID_INPUT_FIELDS" in bundle.context["rule_bindings"][0]["reason_codes"]

    rule = vendor_rule()
    rule["canonical"] = ["VENDOR"]
    bundle = build(ruleset=make_ruleset([rule]))
    binding = bundle.context["rule_bindings"][0]
    assert binding["execution_status"] == "unsupported"
    assert "UNKNOWN_CANONICAL" in binding["reason_codes"]


def test_missing_default_fields_and_ambiguous_date():
    invoice = make_invoice()
    invoice["issues"] = [{"field": "/issue_date", "kind": "ambiguous",
                          "raw_text": "03/04/2026",
                          "candidates": ["2026-04-03", "2026-03-04"]}]
    missing = {
        "id": "R_MISSING", "canonical": "MISSING", "enabled": True,
        "on_fail": "ESCALAR",
        "condition": {"kind": "python_check",
                      "module": "rules_ingestion.checks",
                      "function": "check_missing"},
    }
    bundle = build(outcome=make_outcome(invoice),
                   ruleset=make_ruleset([missing]))
    binding = bundle.context["rule_bindings"][0]
    assert binding["input_status"] == "blocked"
    assert dc.evaluate_context(bundle)["checks"][0]["verdict"] == "NEEDS_REVIEW"


def test_missing_shim_uses_canonical_field_ids():
    missing = {
        "id": "R_MISSING", "canonical": "MISSING", "enabled": True,
        "on_fail": "ESCALAR",
        "params": {"required_fields": ["invoice.currency"]},
        "condition": {"kind": "python_check",
                      "module": "rules_ingestion.checks",
                      "function": "check_missing"},
    }
    bundle = build(ruleset=make_ruleset([missing]))
    assert dc.evaluate_context(bundle)["checks"][0]["verdict"] == "PASS"

    bundle = build(outcome=make_outcome(make_invoice(currency=None)),
                   ruleset=make_ruleset([missing]))
    assert dc.evaluate_context(bundle)["checks"][0]["verdict"] == "FAIL"

    active_rule = copy.deepcopy(missing)
    active_rule["params"] = {"required_fields": ["supplier.active"]}
    snapshots = make_snapshots(supplier_records=[supplier_record(active=True)])
    bundle = build(snapshots=snapshots, ruleset=make_ruleset([active_rule]))
    assert dc.evaluate_context(bundle)["checks"][0]["verdict"] == "PASS"

    snapshots = make_snapshots()
    snapshots["suppliers"] = snap(
        "supplier_master", {"records": [supplier_record()]},
        ("supplier.identity", "supplier.bank_details",
         "supplier.payment_terms"))
    bundle = build(snapshots=snapshots, ruleset=make_ruleset([active_rule]))
    binding = bundle.context["rule_bindings"][0]
    assert binding["input_status"] == "blocked"
    assert dc.evaluate_context(bundle)["checks"][0]["verdict"] == "NEEDS_REVIEW"


def test_missing_empty_required_fields_passes():
    missing = {
        "id": "R_MISSING", "canonical": "MISSING", "enabled": True,
        "on_fail": "ESCALAR",
        "params": {"required_fields": []},
        "condition": {"kind": "python_check",
                      "module": "rules_ingestion.checks",
                      "function": "check_missing"},
    }
    bundle = build(ruleset=make_ruleset([missing]))
    assert dc.evaluate_context(bundle)["checks"][0]["verdict"] == "PASS"

    no_params = {
        "id": "R_MISSING", "canonical": "MISSING", "enabled": True,
        "on_fail": "ESCALAR",
        "condition": {"kind": "python_check",
                      "module": "rules_ingestion.checks",
                      "function": "check_missing"},
    }
    bundle = build(ruleset=make_ruleset([no_params]))
    dep_names = {d["requested_name"]
                 for d in bundle.context["rule_bindings"][0]["dependencies"]}
    assert set(dc.registry.DEFAULT_REQUIRED_FIELDS) <= dep_names
    assert dc.evaluate_context(bundle)["checks"][0]["verdict"] == "PASS"


def test_malformed_on_fail_and_supplier_id():
    rule = vendor_rule()
    rule["on_fail"] = ["ESCALAR"]
    bundle = build(ruleset=make_ruleset([rule]))
    binding = bundle.context["rule_bindings"][0]
    assert binding["execution_status"] == "unsupported"
    assert "INVALID_ON_FAIL" in binding["reason_codes"]

    record = supplier_record()
    record["id"] = {"nested": "P042"}
    bundle = build(snapshots=make_snapshots(supplier_records=[record]))
    assert bundle.context["fields"]["supplier.id"]["state"] == "ambiguous"


def test_missing_ruleset_source_name_rejected():
    bundle = build()
    ctx = copy.deepcopy(bundle.context)
    ctx["ruleset"]["source"] = "nonexistent"
    ctx["context_id"] = "dc_" + digest(canonical_bytes(
        {k: v for k, v in ctx.items() if k != "context_id"}))
    with pytest.raises(dc.ContextError):
        dc.validate_context(ctx, bundle.artifacts)
