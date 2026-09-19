"""A rule written on the norm sheet must reach the deterministic executor.

These tests guard the seam that silently broke once: the authoring side
(codegen) emitting one condition shape while the execution side
(conditions/execution_rules) accepted another. Every rule compiled here is run
through the real evaluator, so a schema drift fails the build instead of
turning every generated rule into an UNSUPPORTED result at evaluation time.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from rules_ingestion import codegen, evaluator, rule_params, store
from rules_ingestion import condition_authoring as ca
from rules_ingestion import decision_registry as dc_registry
from rules_ingestion.classify import classify_lines
from rules_ingestion.execution_context import prepare_context
from rules_ingestion.loader import LoadResult

SPEC = importlib.util.spec_from_file_location(
    "alignment_fixtures", Path(__file__).parent / "decision_context" / "test_decision_context.py")
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)


def compiled_rule(text, rule_id="R_NEW"):
    result = codegen.compile_new_rule(text, use_llm=False, gen_python=False)
    return result, {"id": rule_id, "canonical": None, "enabled": True,
                    "on_fail": result["on_fail"], "params": {}, "input_fields": [],
                    "condition": result["condition"]}


def run(rules, invoice=None, snapshots=None):
    parent = fixtures.build(outcome=fixtures.make_outcome(invoice), snapshots=snapshots,
                            ruleset=fixtures.make_ruleset(rules))
    bundle = prepare_context(parent)
    result = evaluator.evaluate(bundle).to_dict()
    evaluator.validate_evaluation(result, bundle)
    return result


EXECUTABLE = [
    ("Las facturas que superen los 5.000 euros requieren aprobacion del responsable.",
     "invoice.total", "ESCALAR"),
    ("El IBAN de la factura debe coincidir con el del maestro de proveedores.",
     "invoice.iban", "ESCALAR"),
    ("No se paga ningun pedido que el ERP no marque como PENDIENTE.",
     "erp.order_payment_status", "NO_PAGAR"),
    ("Toda factura debe indicar el numero de pedido.",
     "invoice.order_reference", "ESCALAR"),
    ("El plazo de pago es de 60 dias desde la fecha de factura.",
     "supplier.payment_terms_days", "ESCALAR"),
    ("Solo se pagan facturas en euros.", "invoice.currency", "ESCALAR"),
]


@pytest.mark.parametrize("text,field,on_fail", EXECUTABLE)
def test_sheet_rule_compiles_and_executes(text, field, on_fail):
    result, rule = compiled_rule(text)
    assert result["runnable"] is True
    assert result["on_fail"] == on_fail
    assert rule["condition"]["schema_version"] == ca.SCHEMA_VERSION
    assert any(clause["field"] == field for clause in rule["condition"]["clauses"])

    evaluated = run([rule])["rule_results"][0]
    # The point of the test: the executor ran it. UNSUPPORTED means the
    # authoring and execution schemas have drifted apart again.
    assert evaluated["status"] != "UNSUPPORTED"
    assert evaluated["capability_id"] == "condition/1"


def test_compiled_conditions_carry_only_schema_properties():
    # An extra key on a condition is refused as UNKNOWN_CONDITION_PROPERTY,
    # which is how `runnable` inside the condition disabled every rule.
    allowed = {"kind", "schema_version", "applies_when", "logic", "clauses", "then", "else"}
    for text, _field, _on_fail in EXECUTABLE:
        result, _rule = compiled_rule(text)
        assert set(result["condition"]) <= allowed
        assert result["params"] == {}


def test_rule_needing_an_unavailable_fact_is_refused_with_a_reason():
    for text, missing in (
        ("Queda prohibido pagar a proveedores en paraisos fiscales.", "country"),
        ("No pagar a quien no este al corriente con la Seguridad Social.", "social-security"),
        ("Retencion del 15% de IRPF en autonomos.", "withholding"),
    ):
        result, _rule = compiled_rule(text)
        assert result["runnable"] is False
        assert result["condition"]["kind"] == "pending"
        assert missing in result["condition"]["note"]


def test_authoring_refuses_fields_outside_the_registry():
    for field in ("vendor_type", "vendor.country", "vendor.ss_current", "withholding_rate"):
        with pytest.raises(ca.AuthoringError, match="UNKNOWN_FIELD"):
            ca.clause(field, "==", "x")


def test_spanish_thousands_are_not_read_as_decimals():
    # "5.000" on a Spanish norm sheet is five thousand. Reading it as 5.00
    # would escalate every invoice.
    assert ca.literal_for("invoice.total", "5.000")["amount"] == "5000"
    assert ca.literal_for("invoice.total", "12.874,40")["amount"] == "12874.40"
    assert ca.literal_for("invoice.total", "5.00")["amount"] == "5.00"


def test_enrich_marks_unexecutable_rules_instead_of_enabling_them():
    merged = {"rules": [
        {"id": "NEW_001", "origin": "agent", "text": "Solo se pagan facturas en euros.",
         "condition": {"kind": "pending"}, "params": {"stray": 1}},
        {"id": "NEW_002", "origin": "agent", "text": "Retencion del 15% de IRPF.",
         "condition": {"kind": "pending"}},
        # The shape the old generator produced: must not survive as structured.
        {"id": "NEW_003", "origin": "agent", "text": "No pagar en paraisos fiscales.",
         "condition": {"kind": "structured", "logic": "AND", "on_fail": "NO_PAGAR",
                       "clauses": [{"field": "vendor.country", "op": "not_in", "value": "params.denylist"}]}},
    ]}
    codegen.enrich_new_rules(merged, use_llm=False, gen_python=False)
    by_id = {rule["id"]: rule for rule in merged["rules"]}

    assert by_id["NEW_001"]["runnable"] is True
    assert by_id["NEW_001"]["params"] == {}
    assert "runnable" not in by_id["NEW_001"]["condition"]

    for rule_id in ("NEW_002", "NEW_003"):
        assert by_id[rule_id]["runnable"] is False
        assert by_id[rule_id]["compile_error"]

    assert merged["stats"]["new_compiled"] == 1
    assert merged["stats"]["new_unexecutable"] == 2
    assert {row["id"] for row in merged["stats"]["unexecutable_rules"]} == {"NEW_002", "NEW_003"}


# --------------------------------------------------------------------------- #
# The sheet as authority: a norm line sets the parameters of its rule          #
# --------------------------------------------------------------------------- #
PROFILES = Path(__file__).resolve().parents[1] / "rules_ingestion" / "profiles"


def norm_lines(*texts):
    return [{"text": text, "cell": f"A{index + 2}", "row": index + 2,
             "declared": True, "source_sheet": "Norma_Pagos_v3"}
            for index, text in enumerate(texts)]


def build(texts, policy="balanced"):
    profile = yaml.safe_load((PROFILES / f"{policy}.yaml").read_text())
    lines = norm_lines(*texts)
    classified = classify_lines(lines, prefer_jev=False, use_llm=False)
    load = LoadResult(schema={"norma": {"sheet": "Norma_Pagos_v3"}})
    document = store.build_ruleset(load, classified, profile, generated_at="2026-09-19T00:00:00Z")
    return {rule["canonical"]: rule for rule in document["rules"]}, document


def test_written_threshold_sets_the_parameter_and_enables_the_rule():
    rules, document = build([
        "Las facturas que superen los 5.000 euros requieren aprobacion del director financiero.",
    ])
    authorization = rules["AUTHORIZATION"]

    # balanced.yaml ships AUTHORIZATION disabled with a 10.000 default. The
    # sheet says 5.000, so the sheet wins -- and says so in the trace.
    assert authorization["enabled"] is True
    assert authorization["trace"]["enabled_by_sheet"] is True
    assert authorization["params"]["escalate_above_eur"] == "5000"
    setting = authorization["trace"]["params_from_sheet"][0]
    assert setting == {"param": "escalate_above_eur", "value": "5000", "cell": "A2",
                       "sheet": "Norma_Pagos_v3",
                       "text": "Las facturas que superen los 5.000 euros requieren "
                               "aprobacion del director financiero."}
    assert document["stats"]["params_from_sheet"] == 1


def test_written_tolerance_overrides_the_profile_default():
    rules, _document = build([
        "Se admite una tolerancia de 0,50 euros en el cuadre de importes.",
    ])
    assert rules["AMOUNT"]["params"]["tolerance_eur"] == "0.50"


def test_contradictory_lines_change_nothing_and_are_recorded():
    rules, document = build([
        "Las facturas que superen los 5.000 euros requieren aprobacion del director.",
        "Las facturas que superen los 20.000 euros requieren aprobacion del director.",
    ])
    authorization = rules["AUTHORIZATION"]

    # Picking one reading of a self-contradictory norm would move a payment
    # limit without anyone deciding to move it.
    assert authorization["params"]["escalate_above_eur"] == 10000.0
    assert authorization["enabled"] is False
    conflict = authorization["trace"]["param_conflicts"][0]
    assert conflict["param"] == "escalate_above_eur"
    assert {reading["value"] for reading in conflict["readings"]} == {"5000", "20000"}
    assert document["stats"]["param_conflicts"] == 1


def test_activo_means_present_in_the_master_not_a_status_column():
    # No supplier source carries an "active" flag, so "el proveedor debe estar
    # activo" is read as "must be in the master" -- the interpretation is
    # recorded so nobody mistakes the sheet's wording for a status check.
    rules, document = build([
        ("1. Pagar solo si el NIF esta en el maestro y el IBAN de la factura "
         "coincide con el maestro."),
        "El proveedor debe estar activo en el maestro.",
    ])
    vendor = rules["VENDOR"]

    assert vendor["params"].get("require_active") in (False, None)
    assert vendor["params"]["require_nif_in_master"] is True
    note = ("activo interpreted as presence in the supplier master; "
            "the master has no status column")
    assert note in vendor["trace"]["interpretations"]
    assert any(setting.get("note") == note
               for setting in vendor["trace"]["params_from_sheet"])

    from rules_ingestion.merge import build_merged
    merged = next(rule for rule in build_merged(document, None)["rules"]
                  if rule.get("canonical") == "VENDOR")
    assert note in merged["interpretations"]


def test_a_policy_can_keep_parameter_authority():
    # conservative.yaml does not grant the sheet parameter authority.
    rules, _document = build([
        "Las facturas que superen los 5.000 euros requieren aprobacion del director.",
    ], policy="conservative")
    authorization = rules["AUTHORIZATION"]
    assert authorization["trace"]["params_from_sheet"] == []
    assert authorization["params"].get("escalate_above_eur") != "5000"


def test_only_parameters_the_executor_implements_can_come_from_a_sheet():
    for canonical, settable in rule_params.SHEET_SETTABLE.items():
        allowed = set(dc_registry.OTHER_PARAMETERS[canonical]) | set(
            dc_registry.FLAG_DEPENDENCIES[canonical])
        assert set(settable) <= allowed, canonical
    # The planner pins these, so a sheet must not be able to move them.
    assert "hard_key" not in rule_params.SHEET_SETTABLE["DUPLICATES"]
    assert "soft_duplicate_verdict" not in rule_params.SHEET_SETTABLE["DUPLICATES"]


def test_sheet_values_are_type_checked():
    assert rule_params.extract("AUTHORIZATION", "aprobacion por encima de 5.000 euros") \
        == {"escalate_above_eur": "5000"}
    # A family match with no number states nothing.
    assert rule_params.extract("AUTHORIZATION", "requiere aprobacion del director") == {}
    assert rule_params.extract("MISSING", "Toda factura debe indicar el NIF y el pedido.") \
        == {"required_fields": ["nif", "pedido"]}


def test_a_new_registry_field_is_authorable_and_executable():
    # supplier.city was added with a real source (the `ciudad` column declared
    # in sources.yaml), so a norm line about where a supplier sits can now be
    # executed rather than recorded as unexecutable. A field without an
    # authoritative source would only add a new way to escalate.
    assert ca.resolve("ciudad") == "supplier.city"
    condition = ca.build(
        [ca.clause("supplier.city", "not_in", ["MADRID", "BARCELONA"])],
        on_fail="NO_PAGAR")
    rule = {"id": "R_CITY", "canonical": None, "enabled": True, "on_fail": "NO_PAGAR",
            "params": {}, "input_fields": [], "condition": condition}

    evaluated = run([rule])["rule_results"][0]
    assert evaluated["status"] != "UNSUPPORTED"
    assert evaluated["capability_id"] == "condition/1"


def test_the_prompt_offers_only_registry_fields():
    catalogue = ca.catalogue_text()
    for field in dc_registry.FIELD_TYPES:
        assert field in catalogue
    prompt = codegen._structured_prompt("cualquier norma")
    assert "supplier.city" in prompt
    # The names the old generator invented must not be advertised.
    for invented in ("vendor_type", "vendor.country", "vendor.ss_current", "withholding_rate"):
        assert invented not in prompt


def test_merged_ruleset_keeps_the_source_of_every_parameter():
    # The merged ruleset is what the evaluator and the contextual reviewer
    # read. A threshold whose provenance is dropped here is a threshold nobody
    # can trace back to the norm that set it.
    from rules_ingestion.merge import build_merged

    _rules, document = build([
        "Las facturas que superen los 5.000 euros requieren aprobacion del director.",
    ])
    merged = build_merged(document, None)
    authorization = next(rule for rule in merged["rules"]
                         if rule.get("canonical") == "AUTHORIZATION")

    assert authorization["enabled_by_sheet"] is True
    assert authorization["sheet_params"] == [
        {"param": "escalate_above_eur", "value": "5000", "cell": "A2",
         "sheet": "Norma_Pagos_v3",
         "text": "Las facturas que superen los 5.000 euros requieren aprobacion del director."}]
    assert merged["stats"]["params_from_sheet"] == 1


def test_a_norm_line_outside_the_six_families_still_becomes_a_rule():
    # Routing a line to a family makes it evidence for that family's check.
    # A line that matches no family used to land in `flags` and stop there, so
    # a rule plainly written on the sheet never became a rule at all.
    from rules_ingestion.build_rules import declared_new_rules
    from rules_ingestion.merge import build_merged

    texts = ["Solo se pagan facturas en euros.",
             "Queda prohibido pagar a proveedores radicados en paraisos fiscales."]
    lines = norm_lines(*texts)
    classified = classify_lines(lines, prefer_jev=False, use_llm=False)
    load = LoadResult(schema={"norma": {"sheet": "Norma_Pagos_v3"}})
    profile = yaml.safe_load((PROFILES / "balanced.yaml").read_text())
    document = store.build_ruleset(load, classified, profile, generated_at="2026-09-19T00:00:00Z")

    merged = build_merged(document, declared_new_rules(load, classified))
    codegen.enrich_new_rules(merged, use_llm=False, gen_python=False)
    new_rules = [rule for rule in merged["rules"] if not rule.get("canonical")]
    assert len(new_rules) == 2

    currency = next(r for r in new_rules if "euros" in (r["text"] or ""))
    assert currency["runnable"] is True
    assert currency["enabled"] is True          # balanced grants new rules
    assert currency["source_refs"][0]["cell"] == "A2"

    haven = next(r for r in new_rules if "paraisos" in (r["text"] or ""))
    assert haven["runnable"] is False
    assert haven["enabled"] is False
    assert "country" in haven["compile_error"]
