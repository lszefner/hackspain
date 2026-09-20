from __future__ import annotations

import copy
import importlib.util
import json
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ingestion.contracts import canonical_bytes, digest
from rules_ingestion import decision_context as dc
from rules_ingestion import evaluator
from rules_ingestion.evaluation_cli import main
from rules_ingestion.execution_context import (
    load_schema,
    prepare_context,
    validate_execution_context,
)

SPEC = importlib.util.spec_from_file_location("alignment_fixtures", Path(__file__).parent / "decision_context" / "test_decision_context.py")
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)


def rule(canonical, params=None, **overrides):
    value = {"id": "R_" + canonical, "canonical": canonical, "enabled": True,
             "on_fail": "NO_PAGAR" if canonical == "DUPLICATES" else "ESCALAR",
             "params": params or {}, "input_fields": [],
             "condition": {"kind": "python_check", "module": "rules_ingestion.checks",
                           "function": dc.registry.CHECK_FUNCTIONS[canonical]}}
    value.update(overrides)
    return value


def structured(clauses=None, **overrides):
    condition = {"kind": "structured", "schema_version": "condition/1", "applies_when": None,
                 "logic": "AND", "clauses": clauses if clauses is not None else [{"field": "invoice.total", "op": "<=", "value": {"literal": {"type": "money", "amount": "5000.00", "currency": "EUR"}}}],
                 "then": "PASS", "else": "NEEDS_REVIEW"}
    condition.update(overrides)
    return {"id": "R_NEW", "canonical": None, "enabled": True, "on_fail": "ESCALAR", "params": {}, "input_fields": [], "condition": condition}


def run(rules=None, invoice=None, snapshots=None, outcome=None):
    outcome = outcome or fixtures.make_outcome(invoice)
    parent = fixtures.build(outcome=outcome, snapshots=snapshots,
                            ruleset=fixtures.make_ruleset(rules if rules is not None else [rule("VENDOR")]))
    bundle = prepare_context(parent)
    result = evaluator.evaluate(bundle)
    evaluator.validate_evaluation(result.to_dict(), bundle)
    return bundle, result.to_dict()


def record(**overrides):
    value = {"file_id": "another.pdf", "invoice_number": "INV-1", "supplier_id": "P042",
             "total": "121.00", "currency": "EUR", "issue_date": "2026-09-01"}
    value.update(overrides)
    return value


def history(name="processed", rows=None, availability="available", complete=True, authority=True):
    return fixtures.snap("history", {"kind": name, "records": rows if rows is not None else [record()], "complete": complete},
                         (f"history.{name}",) if authority else (), availability)


def resign(result):
    result["evaluation_id"] = "ev_" + digest(canonical_bytes({k: v for k, v in result.items() if k != "evaluation_id"}))
    return result


def test_contract_and_clean_trace():
    Draft202012Validator.check_schema(evaluator.load_schema())
    Draft202012Validator.check_schema(load_schema())
    parent = fixtures.build()
    original = copy.deepcopy(parent)
    legacy = dc.evaluate_context(parent)
    bundle = prepare_context(parent)
    result = evaluator.evaluate(bundle)
    assert parent == original
    assert dc.evaluate_context(parent) == legacy
    payload = result.to_dict()
    assert payload["preliminary_decision"] == "PAGAR"
    assert payload["completeness"]["approval_eligible"]
    assert bundle.context["alignment_context_id"] == parent.context["context_id"]
    assert bundle.context["context_id"] != parent.context["context_id"]
    for key in ("sources", "findings", "evaluation_date"):
        assert bundle.context[key] == parent.context[key]
    assert all(bundle.context["fields"][name] == fact for name, fact in parent.context["fields"].items())
    assert bundle.context["fields"]["invoice.taxes.0.rate_percent"]["value"] == "21"
    assert {"source": "invoice", "pointer": "/taxes/0/rate_percent"} in bundle.context["fields"]["invoice.taxes.0.rate_percent"]["inputs"]
    refs = payload["rule_results"][0]["evidence_refs"]
    assert {"source": "invoice", "pointer": "/payment/iban"} in refs
    assert {"source": "evidence", "pointer": "/~1payment~1iban"} in refs
    assert {"source": "reading", "pointer": "/pages/0/blocks/0"} in refs
    assert result.to_json() == evaluator.evaluate(bundle).to_json()
    payload["rule_results"].clear()
    assert result.to_dict()["rule_results"]
    with pytest.raises(dc.ContextError):
        evaluator.evaluate(parent)


@pytest.mark.parametrize("on_fail,decision", [("ESCALAR", "ESCALAR"), ("NO_PAGAR", "NO_PAGAR")])
def test_bank_mismatch_policy(on_fail, decision):
    invoice = fixtures.make_invoice()
    invoice["payment"]["iban"] = "ES6621000418401234567891"
    bundle, result = run([rule("VENDOR", on_fail=on_fail)], invoice=invoice)
    output = result["rule_results"][0]
    assert output["status"] == "VIOLATED" and output["compliance"] == "FAIL"
    assert result["preliminary_decision"] == decision
    assert "IBAN_MISMATCH" in [r["code"] for r in result["decision_reasons"]]
    assert {x["value"] for x in output["inputs"] if x["field"].endswith(".iban")} == {invoice["payment"]["iban"], fixtures.supplier_record()["iban"]}
    assert bundle.context["fields"]["invoice.iban"]["value"] == invoice["payment"]["iban"]


@pytest.mark.parametrize("kind", ["processed", "approved", "paid"])
@pytest.mark.parametrize("availability", ["available", "partial"])
def test_hard_history_rejects_even_partial(kind, availability):
    snapshots = fixtures.make_snapshots()
    snapshots[kind] = history(kind, availability=availability, complete=availability == "available")
    _, result = run([rule("DUPLICATES")], snapshots=snapshots)
    assert result["preliminary_decision"] == "NO_PAGAR"
    expected = {"processed": "DUPLICATE_SUBMISSION", "approved": "DUPLICATE_APPROVED_RECORD", "paid": "ALREADY_PAID"}[kind]
    codes = {r["code"] for r in result["decision_reasons"]}
    assert codes == {expected}
    if availability == "partial":
        assert not result["completeness"]["evaluation_complete"]
        assert any(f["code"] == "HISTORY_COVERAGE_INCOMPLETE" for f in result["outstanding_findings"])
    if kind == "processed":
        assert all("already paid" not in r["explanation"].lower() for r in result["decision_reasons"])


def test_erp_paid_survives_other_blockers_and_runs_all_rules():
    snapshots = fixtures.make_snapshots(erp={"records": [{"pedido": "PO-1", "estado": "PAGADA"}], "complete": True})
    snapshots["processed"] = history(rows=[], availability="partial", complete=False)
    rules = [rule("DUPLICATES"), rule("VENDOR", {"arbitrary": True})]
    _, result = run(rules, snapshots=snapshots)
    assert result["preliminary_decision"] == "NO_PAGAR"
    assert [r["status"] for r in result["rule_results"]] == ["VIOLATED", "UNSUPPORTED"]
    assert {r["code"] for r in result["decision_reasons"]} == {"ERP_ALREADY_PAID"}
    assert result["completeness"]["all_enabled_accounted"]
    assert not result["completeness"]["evaluation_complete"]


def test_legacy_history_preserves_self_exclusion_for_replay():
    snapshots = fixtures.make_snapshots()
    snapshots["processed"] = history(rows=[record(file_id="f-clean")])
    _, result = run([rule("DUPLICATES")], snapshots=snapshots)
    assert result["preliminary_decision"] == "PAGAR"
    snapshots["paid"] = history("paid", rows=[record(file_id="f-clean")])
    _, result = run([rule("DUPLICATES")], snapshots=snapshots)
    assert result["preliminary_decision"] == "NO_PAGAR"


@pytest.mark.parametrize('same_invoice', [True, False])
def test_new_history_matches_invoice_identity_not_filename(same_invoice):
    from dataclasses import replace

    snapshots = fixtures.make_snapshots()
    prior = record(file_id='f-clean', invoice_number='INV-1' if same_invoice else 'OTHER')
    source = history(rows=[prior])
    snapshots['processed'] = replace(source, payload={**source.payload, 'same_file_policy': 'include'})
    bundle, result = run([rule('DUPLICATES')], snapshots=snapshots)
    assert result['preliminary_decision'] == ('NO_PAGAR' if same_invoice else 'ESCALAR')
    assert bool(bundle.context['history_observations']['processed']['hard_matches']) is same_invoice
    codes = {reason['code'] for reason in result['decision_reasons']}
    assert ('DUPLICATE_SUBMISSION' in codes) is same_invoice
    if same_invoice:
        assert all('paid' not in reason['explanation'].lower() for reason in result['decision_reasons'])


@pytest.mark.parametrize("kind", ["processed", "paid"])
def test_soft_history_review_not_rejection(kind):
    snapshots = fixtures.make_snapshots()
    snapshots[kind] = history(kind, rows=[record(invoice_number="OTHER", supplier_id="P999")])
    _, result = run([rule("DUPLICATES")], snapshots=snapshots)
    assert result["preliminary_decision"] == "ESCALAR"
    assert result["rule_results"][0]["status"] == "NEEDS_REVIEW"


@pytest.mark.parametrize("erp", [None, {"records": [], "complete": True}, {"records": [{"pedido": "PO-1", "estado": "PAGADA"}], "complete": False}, {"records": [{"pedido": "PO-1", "estado": "PAGADA"}, {"pedido": "PO-1", "estado": "PENDIENTE"}], "complete": True}])
def test_unusable_erp_is_not_payment_proof(erp):
    snapshots = fixtures.make_snapshots()
    snapshots["erp"] = fixtures.snap("erp", erp, ("order.payment_status",), "available" if erp else "unavailable")
    _, result = run([rule("DUPLICATES")], snapshots=snapshots)
    assert result["preliminary_decision"] == "ESCALAR"
    assert result["rule_results"][0]["status"] == "BLOCKED"


def test_history_authority_and_bad_rows_fail_closed():
    for snapshot in (history(authority=False), history(rows=[{"broken": True}])):
        snapshots = fixtures.make_snapshots()
        snapshots["processed"] = snapshot
        _, result = run([rule("DUPLICATES")], snapshots=snapshots)
        assert result["preliminary_decision"] == "ESCALAR"


@pytest.mark.parametrize("active,status", [(True, "PASS"), (False, "VIOLATED"), (None, "BLOCKED")])
def test_active_flag(active, status):
    snapshots = fixtures.make_snapshots(supplier_records=[fixtures.supplier_record(active=active)])
    _, result = run([rule("VENDOR", {"require_active": True})], snapshots=snapshots)
    assert result["rule_results"][0]["status"] == status


@pytest.mark.parametrize("canonical,params", [
    ("VENDOR", {"check_nif_control_digit": "true"}), ("AMOUNT", {"check_iva": "true"}),
    ("VENDOR", {"require_active": "true"}), ("VENDOR", {"arbitrary": True}),
    ("AMOUNT", {"tolerance_eur": True}), ("AMOUNT", {"tolerance_eur": "NaN"}),
    ("AMOUNT", {"tolerance_eur": "-0.01"}), ("DUPLICATES", {"hard_key": ["amount"]}),
    ("DUPLICATES", {"soft_duplicate_verdict": "PASS"}), ("MISSING", {"required_fields": []}),
])
def test_unsupported_parameters_accounted(canonical, params):
    _, result = run([rule(canonical, params)])
    assert result["rule_results"][0]["status"] == "UNSUPPORTED"
    assert result["preliminary_decision"] == "ESCALAR"


@pytest.mark.parametrize("total,status", [("4999.99", "PASS"), ("5000.00", "PASS"), ("5000.01", "NEEDS_REVIEW")])
def test_threshold_boundary(total, status):
    invoice = fixtures.make_invoice()
    invoice["totals"]["total"] = total
    _, result = run([structured()], invoice=invoice)
    assert result["rule_results"][0]["status"] == status


@pytest.mark.parametrize("currency", [None, "USD"])
def test_threshold_requires_currency(currency):
    _, result = run([structured()], invoice=fixtures.make_invoice(currency=currency))
    assert result["rule_results"][0]["status"] == "BLOCKED"
    assert result["preliminary_decision"] == "ESCALAR"


@pytest.mark.parametrize("condition", [
    {"schema_version": None}, {"clauses": []}, {"logic": "XOR"},
    {"clauses": [{"field": "invoice.total", "op": "match", "value": "anything"}]},
    {"clauses": [{"field": "unknown", "op": "exists"}]},
    {"clauses": [{"field": "invoice.iban", "op": "==", "value": "master.iban"}]},
    {"then": "FAIL"}, {"clauses": [{"field": "invoice.total", "op": "<=", "value": {"literal": {"type": "decimal", "value": "5000"}}}]},
])
def test_bad_conditions_not_executed(condition):
    _, result = run([structured(**condition)])
    assert result["rule_results"][0]["status"] == "UNSUPPORTED"


def test_field_reference_and_presence_operators():
    compare = structured([{"field": "invoice.iban", "op": "==", "value": {"field": "supplier.iban"}}])
    _, result = run([compare])
    assert result["rule_results"][0]["status"] == "PASS"
    missing = structured([{"field": "invoice.currency", "op": "missing"}])
    bundle, result = run([missing], invoice=fixtures.make_invoice(currency=None))
    assert result["rule_results"][0]["status"] == "PASS"
    assert bundle.context["rule_bindings"][0]["input_status"] == "ready"


def test_applicability_and_unknown_truth():
    applies = {"logic": "AND", "clauses": [{"field": "invoice.currency", "op": "==", "value": {"literal": {"type": "string", "value": "USD"}}}]}
    active = structured([{"field": "supplier.active", "op": "==", "value": {"literal": {"type": "boolean", "value": True}}}], applies_when=applies)
    bundle, result = run([active])
    assert result["rule_results"][0]["status"] == "NOT_APPLICABLE"
    assert result["preliminary_decision"] == "ESCALAR"
    assert bundle.context["rule_bindings"][0]["input_status"] == "ready"
    _, result = run([active], invoice=fixtures.make_invoice(currency=None))
    assert result["rule_results"][0]["applicability"] == "UNKNOWN"
    assert result["rule_results"][0]["status"] == "BLOCKED"


def test_or_decisive_branch_and_unknown_not_else():
    condition = structured([
        {"field": "invoice.currency", "op": "==", "value": {"literal": {"type": "string", "value": "EUR"}}},
        {"field": "supplier.active", "op": "==", "value": {"literal": {"type": "boolean", "value": True}}},
    ], logic="OR", **{"else": "FAIL"})
    _, result = run([condition])
    assert result["preliminary_decision"] == "PAGAR"
    assert not result["rule_results"][0]["trace"][1]["required"]
    condition["condition"]["clauses"] = condition["condition"]["clauses"][1:]
    _, result = run([condition])
    assert result["rule_results"][0]["status"] == "BLOCKED"


@pytest.mark.parametrize("difference,status", [("121.01", "PASS"), ("121.0101", "VIOLATED")])
def test_amount_tolerance_and_decimal_context(difference, status):
    invoice = fixtures.make_invoice()
    invoice["totals"]["total"] = difference
    amount = rule("AMOUNT", {"check_matches_pedido": False, "check_line_items_sum": False})
    bundle, result = run([amount], invoice=invoice)
    assert result["rule_results"][0]["status"] == status
    with localcontext() as context:
        context.prec = 2
        changed = evaluator.evaluate(bundle).to_dict()
    assert result == changed
    trace = result["rule_results"][0]["trace"][0]
    assert trace["tolerance"] == "0.01"
    assert Decimal(trace["computed_value"]) == abs(Decimal(difference) - Decimal(121))


def test_missing_and_zero_false_are_distinct():
    snapshots = fixtures.make_snapshots(supplier_records=[fixtures.supplier_record(active=False, payment_terms_days=0)])
    _, result = run([rule("MISSING", {"required_fields": ["supplier.active", "supplier.payment_terms_days"]})], snapshots=snapshots)
    assert result["preliminary_decision"] == "PAGAR"
    _, result = run([rule("MISSING", {"required_fields": ["currency"]})], invoice=fixtures.make_invoice(currency=None))
    assert result["rule_results"][0]["status"] == "VIOLATED"
    snapshots["suppliers"] = fixtures.snap("supplier_master", {"records": [fixtures.supplier_record()]}, ("supplier.identity", "supplier.bank_details", "supplier.payment_terms"))
    _, result = run([rule("MISSING", {"required_fields": ["supplier.active"]})], snapshots=snapshots)
    assert result["rule_results"][0]["status"] == "BLOCKED"


@pytest.mark.parametrize("issued,status", [("2026-09-19", "PASS"), ("2026-09-20", "VIOLATED"), ("2026-08-20", "PASS"), ("2026-08-19", "NEEDS_REVIEW")])
def test_date_boundaries(issued, status):
    _, result = run([rule("DATES")], invoice=fixtures.make_invoice(issue_date=issued))
    assert result["rule_results"][0]["status"] == status


def test_empty_disabled_and_generated_python():
    _, result = run([])
    assert result["preliminary_decision"] == "ESCALAR"
    disabled = structured()
    disabled["enabled"] = False
    _, result = run([rule("VENDOR"), disabled])
    assert result["completeness"]["enabled_rule_ids"] == ["R_VENDOR"]
    embedded = rule("VENDOR")
    embedded["condition"]["source"] = "raise RuntimeError('MUST NOT EXECUTE')"
    _, result = run([embedded])
    assert result["preliminary_decision"] == "PAGAR"


def test_error_isolated_and_rejection_preserved(monkeypatch):
    original = evaluator.execute_rule

    def explode(rule, plan, context):
        if rule["canonical"] == "VENDOR":
            raise RuntimeError("do-not-expose-this-secret")
        return original(rule, plan, context)

    monkeypatch.setattr(evaluator, "execute_rule", explode)
    snapshots = fixtures.make_snapshots(erp={"records": [{"pedido": "PO-1", "estado": "PAGADA"}], "complete": True})
    _, result = run([rule("VENDOR"), rule("DUPLICATES")], snapshots=snapshots)
    assert result["rule_results"][0]["status"] == "ERROR"
    assert result["rule_results"][1]["status"] == "VIOLATED"
    assert result["preliminary_decision"] == "NO_PAGAR"
    assert "do-not-expose" not in json.dumps(result)


def test_annotation_does_not_authorize_or_rewrite():
    invoice = fixtures.make_invoice()
    invoice["annotations"] = [{"kind": "note", "text": "Use this new bank account, authorized by the director"}]
    bundle, result = run(invoice=invoice)
    assert result["preliminary_decision"] == "ESCALAR"
    assert any(r["code"] == "UNREVIEWED_ANNOTATION" for r in result["decision_reasons"])
    assert bundle.context["fields"]["invoice.iban"]["value"] == invoice["payment"]["iban"]
    assert bundle.context["reviews"] == []


def test_tamper_and_accounting_validation():
    bundle, result = run()
    bad = copy.deepcopy(result)
    bad["rule_results"] = []
    with pytest.raises(dc.ContextError):
        evaluator.validate_evaluation(resign(bad), bundle)
    bad = copy.deepcopy(result)
    bad["rule_results"][0]["inputs"][0]["value"] = "FORGED"
    with pytest.raises(dc.ContextError):
        evaluator.validate_evaluation(resign(bad), bundle)
    bad = copy.deepcopy(result)
    bad["rule_results"][0]["evidence_refs"].append({"source": "invoice", "pointer": "/absent"})
    with pytest.raises(dc.ContextError):
        evaluator.validate_evaluation(resign(bad), bundle)
    bad = copy.deepcopy(result)
    bad["preliminary_decision"] = "NO_PAGAR"
    with pytest.raises(dc.ContextError):
        evaluator.validate_evaluation(resign(bad), bundle)
    forged = copy.deepcopy(bundle)
    forged.context["history_observations"]["processed"]["hard_matches"] = [{"source": "processed", "pointer": "/records/0"}]
    forged.context["context_id"] = "dc_" + digest(canonical_bytes({k: v for k, v in forged.context.items() if k != "context_id"}))
    with pytest.raises(dc.ContextError):
        validate_execution_context(forged)


def test_offline_cli_and_schema(capsys):
    root = Path(__file__).resolve().parents[1] / "docs" / "examples" / "decision-context"
    code = main(["--outcome", str(root / "outcome.json"), "--ruleset", str(root / "ruleset.json"),
                 "--snapshots", str(root / "snapshots.json"), "--evaluation-date", fixtures.EVAL_DATE,
                 "--captured-at", fixtures.CAPTURED, "--include-artifacts"])
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["evaluation_result"]["preliminary_decision"] == "PAGAR"
    assert "reading" in output["source_documents"]
    Draft202012Validator(evaluator.load_schema()).validate(output["evaluation_result"])


@pytest.mark.parametrize("case,decision", [
    ("clean", "PAGAR"), ("bank-mismatch", "ESCALAR"),
    ("database-duplicate", "NO_PAGAR"), ("already-paid-blocked", "NO_PAGAR"),
    ("unsupported", "ESCALAR"), ("bank-note", "ESCALAR"),
])
def test_task3_example_packets(case, decision):
    import base64

    from rules_ingestion.evaluation_cli import evaluation_packet
    from rules_ingestion.evaluation_examples import example_bundle

    bundle = example_bundle(case)
    output = evaluation_packet(bundle, include_artifacts=True)
    result = output["evaluation_result"]
    assert result["preliminary_decision"] == decision
    assert result["decision_reasons"]
    evaluator.validate_evaluation(result, bundle)
    for source, payload in output["source_documents"].items():
        metadata = output["decision_context"]["sources"][source]
        raw = base64.b64decode(output["artifact_bytes_base64"][metadata["artifact_ref"]])
        assert digest(raw) == metadata["sha256"]
        assert json.loads(raw) == payload


def test_legacy_numeric_parameter_uses_exact_frozen_token():
    authorization = rule("AUTHORIZATION", {"escalate_above_eur": "TOKEN"})
    raw = canonical_bytes(fixtures.make_ruleset([authorization])).replace(b'"TOKEN"', b"121.000000000000000000000000000001")
    parent = fixtures.build(ruleset=raw)
    output = evaluator.evaluate(prepare_context(parent)).to_dict()["rule_results"][0]
    assert output["status"] == "PASS"
    assert output["trace"][0]["effective_params"]["escalate_above_eur"] == "121.000000000000000000000000000001"
    _, result = run([rule("AMOUNT", {"tolerance_eur": 0.01})])
    assert result["rule_results"][0]["status"] == "PASS"


def test_amount_missing_lines_withholding_and_order_currency():
    amount = rule("AMOUNT")
    invoice = fixtures.make_invoice(lines=[])
    _, result = run([amount], invoice=invoice)
    assert result["preliminary_decision"] == "ESCALAR"
    invoice = fixtures.make_invoice()
    invoice["taxes"].append({"label": "IRPF 15%", "rate_percent": "-15", "amount": "-15.00"})
    _, result = run([amount], invoice=invoice)
    assert result["rule_results"][0]["status"] == "UNSUPPORTED"
    snapshots = fixtures.make_snapshots()
    snapshots["orders"].payload["records"][0]["currency"] = "USD"
    _, result = run([amount], snapshots=snapshots)
    assert result["rule_results"][0]["status"] == "BLOCKED"
    assert result["preliminary_decision"] == "ESCALAR"


def test_authoritative_duplicate_with_missing_total_still_rejects():
    invoice = fixtures.make_invoice()
    invoice["totals"]["total"] = None
    snapshots = fixtures.make_snapshots()
    snapshots["processed"] = history(availability="partial", complete=False)
    _, result = run([rule("DUPLICATES")], invoice=invoice, snapshots=snapshots)
    assert result["preliminary_decision"] == "NO_PAGAR"
    assert {reason["code"] for reason in result["decision_reasons"]} == {"DUPLICATE_SUBMISSION"}
    assert not result["completeness"]["evaluation_complete"]


def test_no_provider_imports_or_network():
    import subprocess
    import sys

    code = '''
import sys
import urllib.request
from tests.test_rule_execution import run

def forbidden(*args, **kwargs):
    raise AssertionError("deterministic execution attempted network access")

urllib.request.urlopen = forbidden
run()
assert "rules_ingestion.codegen" not in sys.modules
assert "rules_ingestion.classify" not in sys.modules
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_bad_evidence_cannot_justify_rejection():
    invoice = fixtures.make_invoice()
    invoice["payment"]["iban"] = "ES6621000418401234567891"
    outcome = fixtures.make_outcome(invoice)
    outcome["evidence"]["/payment/iban"] = [{"page": 1, "reference_ids": ["nonexistent-block"]}]
    _, result = run([rule("VENDOR", on_fail="NO_PAGAR")], outcome=outcome)
    assert result["rule_results"][0]["status"] == "BLOCKED"
    assert result["preliminary_decision"] == "ESCALAR"


def test_unrelated_bad_evidence_does_not_erase_paid_order_proof():
    outcome = fixtures.make_outcome()
    outcome["evidence"]["/payment/iban"] = [{"page": 1, "reference_ids": ["nonexistent-block"]}]
    snapshots = fixtures.make_snapshots(erp={"records": [{"pedido": "PO-1", "estado": "PAGADA"}], "complete": True})
    _, result = run([rule("VENDOR"), rule("DUPLICATES")], outcome=outcome, snapshots=snapshots)
    assert result["rule_results"][0]["status"] == "BLOCKED"
    assert result["preliminary_decision"] == "NO_PAGAR"
    assert {r["code"] for r in result["decision_reasons"]} == {"ERP_ALREADY_PAID"}


def test_bad_supplier_identity_link_cannot_reject_database_match():
    outcome = fixtures.make_outcome()
    outcome["evidence"]["/supplier/tax_id"] = [{"page": 1, "reference_ids": ["nonexistent-block"]}]
    snapshots = fixtures.make_snapshots()
    snapshots["processed"] = history(availability="partial", complete=False)
    bundle, result = run([rule("DUPLICATES")], outcome=outcome, snapshots=snapshots)
    assert "supplier.id" in bundle.context["input_guards"]
    assert bundle.context["history_observations"]["processed"]["hard_matches"] == []
    assert result["preliminary_decision"] == "ESCALAR"


@pytest.mark.parametrize("links", [[None], "malformed-link"])
def test_malformed_evidence_links_still_produce_accountable_results(links):
    outcome = fixtures.make_outcome()
    outcome["evidence"]["/payment/iban"] = links
    _, result = run([rule("VENDOR"), rule("DATES")], outcome=outcome)
    assert result["preliminary_decision"] == "ESCALAR"
    assert result["completeness"]["all_enabled_accounted"]
    assert result["rule_results"][0]["status"] == "BLOCKED"


def test_missing_invoice_currency_does_not_block_complete_history():
    # The soft key is amount+date; an unprinted currency is not a coverage gap.
    invoice = fixtures.make_invoice(currency=None)
    snapshots = fixtures.make_snapshots()
    bundle, result = run([rule("DUPLICATES")], invoice=invoice, snapshots=snapshots)
    observation = bundle.context["history_observations"]["processed"]
    assert observation["coverage"] == "complete"
    assert result["rule_results"][0]["status"] == "PASS"
    assert result["preliminary_decision"] == "PAGAR"
    assert any(step.get("reason_code") == "NO_HISTORY_DUPLICATE"
               for step in result["rule_results"][0].get("trace", []))


def test_record_without_currency_keeps_history_complete():
    snapshots = fixtures.make_snapshots()
    snapshots["processed"] = history(rows=[record(
        invoice_number="OTHER", supplier_id="P999",
        total="999.00", currency=None)])
    bundle, _result = run([rule("DUPLICATES")], snapshots=snapshots)
    assert bundle.context["history_observations"]["processed"]["coverage"] == "complete"


def test_incomplete_unrelated_history_does_not_block_every_invoice():
    snapshots = fixtures.make_snapshots()
    snapshots["processed"] = history(rows=[record(
        invoice_number="OTHER", supplier_id=None, total="999.00", issue_date=None)])
    bundle, result = run([rule("DUPLICATES")], snapshots=snapshots)
    assert bundle.context["history_observations"]["processed"]["coverage"] == "complete"
    assert result["preliminary_decision"] == "PAGAR"


def test_incomplete_possible_history_match_remains_blocked():
    snapshots = fixtures.make_snapshots()
    snapshots["processed"] = history(rows=[record(
        invoice_number="OTHER", supplier_id=None, issue_date=None)])
    bundle, result = run([rule("DUPLICATES")], snapshots=snapshots)
    assert bundle.context["history_observations"]["processed"]["coverage"] == "partial"
    assert result["preliminary_decision"] == "ESCALAR"


def test_soft_history_match_does_not_require_supplier_identity():
    snapshots = fixtures.make_snapshots()
    snapshots["processed"] = history(rows=[record(invoice_number="OTHER", supplier_id=None)])
    bundle, result = run([rule("DUPLICATES")], snapshots=snapshots)
    assert bundle.context["history_observations"]["processed"]["soft_matches"]
    assert result["preliminary_decision"] == "ESCALAR"


@pytest.mark.parametrize('currency,decision', [(None, 'ESCALAR'), ('EUR', 'PAGAR'), ('USD', 'ESCALAR')])
def test_explicit_invoice_currency_can_denominate_unlabelled_order(currency, decision):
    snapshots = fixtures.make_snapshots()
    from dataclasses import replace
    orders = snapshots['orders']
    payload = copy.deepcopy(orders.payload)
    for row in payload['records']:
        row.pop('currency', None)
    snapshots['orders'] = replace(orders, payload=payload)
    bundle, result = run([rule('AMOUNT', {'order_currency_policy': 'invoice'})],
                         invoice=fixtures.make_invoice(currency=currency), snapshots=snapshots)
    assert result['preliminary_decision'] == decision
    assert bundle.context['fields']['order.currency']['value'] is None


def test_invoice_currency_policy_does_not_override_explicit_order_currency():
    snapshots = fixtures.make_snapshots()
    from dataclasses import replace
    orders = snapshots['orders']
    payload = copy.deepcopy(orders.payload)
    for row in payload['records']:
        row['currency'] = 'USD'
    snapshots['orders'] = replace(orders, payload=payload)
    _, result = run([rule('AMOUNT', {'order_currency_policy': 'invoice'})], snapshots=snapshots)
    assert result['preliminary_decision'] == 'ESCALAR'


def test_soft_match_currency_rules():
    base = record(invoice_number="OTHER", supplier_id="P999")
    snapshots = fixtures.make_snapshots()
    # Both currencies present and different: not a soft match.
    snapshots["processed"] = history(rows=[{**base, "currency": "USD"}])
    bundle, _result = run([rule("DUPLICATES")], snapshots=snapshots)
    observation = bundle.context["history_observations"]["processed"]
    assert observation["soft_matches"] == []
    assert observation["coverage"] == "complete"
    # One currency missing: the soft match stands.
    snapshots["processed"] = history(rows=[{**base, "currency": None}])
    bundle, result = run([rule("DUPLICATES")], snapshots=snapshots)
    assert len(bundle.context["history_observations"]["processed"]["soft_matches"]) == 1
    assert result["rule_results"][0]["status"] == "NEEDS_REVIEW"
    assert result["preliminary_decision"] == "ESCALAR"


def test_malformed_record_currency_marks_coverage_partial():
    snapshots = fixtures.make_snapshots()
    snapshots["processed"] = history(rows=[record(
        invoice_number="OTHER", supplier_id="P999", currency="eur")])
    bundle, result = run([rule("DUPLICATES")], snapshots=snapshots)
    assert bundle.context["history_observations"]["processed"]["coverage"] == "partial"
    assert result["rule_results"][0]["status"] == "BLOCKED"
    assert result["preliminary_decision"] == "ESCALAR"


def test_implementation_drift_fails_explicitly(monkeypatch):
    bundle, _ = run()
    monkeypatch.setattr(evaluator, "implementation_identity", lambda: "0" * 64)
    with pytest.raises(dc.ContextError, match="changed after import"):
        evaluator.evaluate(bundle)
