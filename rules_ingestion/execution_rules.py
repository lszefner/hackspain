from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext

from . import decision_registry as registry
from .conditions import (
    ConditionError,
    decimal,
    evaluate_group,
    execution_fields,
    field_id,
    usable,
    validate_condition,
)
from .normalize import nif_control_ok, norm_nif

CAPABILITY_VERSION = "decision-capabilities/3"
IMPLEMENTED_FLAGS = {"VENDOR": {"require_active", "check_nif_control_digit"}, "AMOUNT": {"check_iva"}}
DEFAULTS = {
    "VENDOR": {"require_nif_in_master": True, "require_iban_match": True, "require_active": False, "check_nif_control_digit": False},
    "DUPLICATES": {"require_erp_pending": True, "hard_key": ["invoice_number", "vendor_id"], "soft_key": ["amount", "date"], "soft_duplicate_verdict": "NEEDS_REVIEW"},
    "AMOUNT": {"tolerance_eur": "0.01", "check_line_items_sum": True, "check_total_is_base_plus_iva": True, "check_matches_pedido": True, "check_iva": False},
    "AUTHORIZATION": {"escalate_above_eur": "10000"},
    "DATES": {"allow_future": False, "enforce_payment_terms": True},
    "MISSING": {"required_fields": list(registry.DEFAULT_REQUIRED_FIELDS)},
}


def enabled_rules(document: dict) -> list[dict]:
    return [{**rule, "_index": i} for i, rule in enumerate(document["rules"]) if rule.get("enabled", True) is not False]


def plan_rule(rule: dict, context: dict) -> dict:
    fields = execution_fields(context)
    errors, dependencies = [], []
    canonical = rule.get("canonical")
    condition = rule.get("condition")
    params = rule.get("params", {})
    inputs = rule.get("input_fields", [])
    kind = condition.get("kind") if isinstance(condition, dict) else None
    if type(rule.get("enabled", True)) is not bool:
        errors.append("INVALID_ENABLED")
    if rule.get("on_fail") not in ("ESCALAR", "NO_PAGAR"):
        errors.append("INVALID_ON_FAIL")
    if not isinstance(params, dict):
        errors.append("INVALID_PARAMETERS")
        params = {}
    if not isinstance(inputs, list) or any(not isinstance(x, str) for x in inputs):
        errors.append("INVALID_INPUT_FIELDS")
        inputs = []

    def depend(names, phase="compliance"):
        for name in names:
            try:
                resolved = field_id(name, fields)
            except (ConditionError, TypeError):
                resolved = None
                errors.append("UNKNOWN_FIELD")
            dependency = {"requested_name": str(name), "field_id": resolved, "phase": phase}
            if dependency not in dependencies:
                dependencies.append(dependency)

    depend(inputs, "unconditional")
    effective = dict(params)
    if kind == "structured":
        if params:
            errors.append("UNSUPPORTED_STRUCTURED_PARAMETERS")
        try:
            phases = validate_condition(condition, fields)
            for phase, names in phases.items():
                depend(names, phase)
        except (ConditionError, TypeError, ValueError) as exc:
            errors.append(str(exc) if isinstance(exc, ConditionError) else "INVALID_CONDITION")
    elif kind == "python_check" and isinstance(canonical, str) and canonical in registry.CHECK_FUNCTIONS:
        if condition.get("module") != "rules_ingestion.checks" or condition.get("function") != registry.CHECK_FUNCTIONS[canonical]:
            errors.append("CONDITION_FUNCTION_MISMATCH")
        allowed = set(registry.FLAG_DEPENDENCIES[canonical]) | set(registry.OTHER_PARAMETERS[canonical])
        if set(params) - allowed:
            errors.append("UNKNOWN_PARAMETER")
        effective = {**DEFAULTS[canonical], **params}
        for flag in registry.FLAG_DEPENDENCIES[canonical]:
            if flag in effective and type(effective[flag]) is not bool:
                errors.append("INVALID_FLAG")
        for flag in registry.UNIMPLEMENTED_FLAGS.get(canonical, ()):
            if flag not in IMPLEMENTED_FLAGS.get(canonical, set()) and effective.get(flag) is True:
                errors.append("UNIMPLEMENTED_FLAG")
        for key in ("tolerance_eur", "escalate_above_eur"):
            if key in effective:
                value = effective[key]
                try:
                    parsed = decimal(format(value, "f") if isinstance(value, Decimal) else str(value) if type(value) is int else value)
                    if parsed < 0:
                        raise ConditionError("INVALID_PARAMETER")
                    effective[key] = format(parsed, "f")
                except ConditionError:
                    errors.append("INVALID_PARAMETER")
        for key in ("hard_key", "soft_key", "required_fields"):
            if key in effective:
                names = effective[key]
                if not isinstance(names, list) or not names or any(not isinstance(x, str) for x in names):
                    errors.append("INVALID_PARAMETER")
                else:
                    depend(names)
        if "allowed_currencies" in effective:
            allowed_currencies = effective["allowed_currencies"]
            if not isinstance(allowed_currencies, list) or not allowed_currencies or any(not isinstance(c, str) or not re.fullmatch(r"[A-Z]{3}", c) for c in allowed_currencies):
                errors.append("INVALID_PARAMETER")
        if canonical == "DUPLICATES":
            for key, expected in (("hard_key", ["invoice.number", "supplier.id"]), ("soft_key", ["invoice.total", "invoice.issue_date"])):
                try:
                    actual = [field_id(x, fields) for x in effective[key]]
                    if actual != expected:
                        errors.append("UNSUPPORTED_DUPLICATE_KEY")
                except (ConditionError, TypeError):
                    errors.append("UNSUPPORTED_DUPLICATE_KEY")
            if effective["soft_duplicate_verdict"] != "NEEDS_REVIEW":
                errors.append("UNSUPPORTED_SOFT_DUPLICATE_VERDICT")
        depend(registry.BASE_DEPENDENCIES[canonical])
        for flag, (_, names) in registry.FLAG_DEPENDENCIES[canonical].items():
            if effective.get(flag) is True:
                depend(names)
        if canonical == "AMOUNT" and effective.get("check_iva") is True:
            depend(("invoice.taxable_base", "invoice.currency"))
            depend(sorted(name for name in fields if re.fullmatch(r"invoice\.taxes\.[0-9]+\.(kind|amount|rate_percent)", name)))
        if canonical == "AMOUNT" and effective.get("check_total_is_base_plus_iva") is True:
            kinds = [f for key, f in fields.items() if re.fullmatch(r"invoice\.taxes\.[0-9]+\.kind", key)]
            if any(f["value"] in ("withholding", "other", "unknown") or f["state"] != "present" for f in kinds):
                errors.append("UNSUPPORTED_TAX_RECONCILIATION")
    else:
        errors.append("UNSUPPORTED_CONDITION")
        if kind == "python_check":
            errors.append("UNKNOWN_CANONICAL")
    return {"kind": kind, "canonical": canonical, "params": effective,
            "dependencies": dependencies, "errors": sorted(set(errors))}


def bind_rule(rule: dict, context: dict) -> dict:
    plan = plan_rule(rule, context)
    fields = execution_fields(context)
    needed = None
    known_truth = True
    if not plan["errors"] and plan["kind"] == "structured":
        condition = rule["condition"]
        applies = condition.get("applies_when")
        app = evaluate_group(applies, fields, f"/rules/{rule['_index']}/condition/applies_when") if applies else None
        needed = {"applicability": set(app["required_fields"]) if app else set(), "compliance": set()}
        known_truth = app is None or app["truth"] != "UNKNOWN"
        if app is None or app["truth"] == "TRUE":
            result = evaluate_group({"logic": condition["logic"], "clauses": condition["clauses"]}, fields, f"/rules/{rule['_index']}/condition")
            needed["compliance"] = set(result["required_fields"])
            known_truth = result["truth"] != "UNKNOWN"
    dependencies, reasons = [], list(plan["errors"])
    blocked = not known_truth
    if not known_truth:
        reasons.append("CONDITION_UNKNOWN")
    for dep in plan["dependencies"]:
        name = dep["field_id"]
        required = dep["phase"] == "unconditional" or needed is None or name in needed[dep["phase"]]
        fact = fields.get(name) if name else None
        state = "bound" if fact and fact["state"] == "present" else fact["state"] if fact else "unknown_field"
        dependencies.append({**dep, "state": state, "required": required})
        presence_allowed = plan["canonical"] == "MISSING" or plan["kind"] == "structured" and known_truth and dep["phase"] != "unconditional"
        allowed = ("bound", "missing") if presence_allowed else ("bound",)
        if required and (state not in allowed or fact and fact["extraction_quality"] in ("uncertain", "unlinked")):
            blocked = True
            reasons.append("UNUSABLE_INPUT")
    return {"rule_id": rule["id"], "rule_pointer": f"/rules/{rule['_index']}",
            "dependencies": dependencies, "input_status": "blocked" if blocked else "ready",
            "execution_status": "unsupported" if plan["errors"] else "supported",
            "reason_codes": sorted(set(reasons))}


class RuleFrame:
    def __init__(self, rule: dict, plan: dict, context: dict):
        self.rule, self.plan, self.context = rule, plan, context
        self.fields = execution_fields(context)
        self.pointer = f"/rules/{rule['_index']}"
        self.names, self.refs, self.traces, self.steps = [], [], [], []

    def touch(self, names):
        for name in names:
            if name not in self.names:
                self.names.append(name)

    def value(self, name):
        self.touch([name])
        fact = self.fields[name]
        return fact["value"] if usable(fact) else None

    def add(self, verdict, code, explanation, names=(), operation="check", operands=None, computed=None, refs=()):
        self.touch(names)
        self.refs.extend(refs)
        trace = {"node": self.pointer, "operation": operation,
                 "input_refs": [{"field": x} for x in names] + list(refs),
                 "result": "UNKNOWN" if verdict == "BLOCKED" else "TRUE" if verdict == "PASS" else "FALSE",
                 "required": True, "reason_code": code, "explanation": explanation, "verdict": verdict}
        if operands is not None:
            trace["operands"] = operands
        if computed is not None:
            trace["computed_value"] = computed
        self.traces.append(trace)
        self.steps.append((verdict, code, explanation))

    def check(self, names, predicate, failure_code, explanation, operation="==", failure="FAIL", allow_missing=False):
        self.touch(names)
        facts = [self.fields[name] for name in names]
        good = all(usable(f) or allow_missing and f["state"] == "missing" and f["extraction_quality"] not in ("uncertain", "unlinked") for f in facts)
        values = [f["value"] for f in facts]
        if not good:
            code = "EVIDENCE_LINK_UNUSABLE" if any(name in self.context.get("input_guards", {}) for name in names) else "UNUSABLE_INPUT"
            self.add("BLOCKED", code, "Required evidence is missing, unavailable, invalid, ambiguous, or unverified: " + ", ".join(names), names, operation, values)
            return
        passed = predicate(*values)
        self.add("PASS" if passed else failure, "REQUIREMENT_SATISFIED" if passed else failure_code,
                 "Requirement satisfied: " + ", ".join(names) if passed else explanation,
                 names, operation, values)

    def finish(self, status=None, applicability="APPLICABLE"):
        verdicts = [x[0] for x in self.steps]
        if status is None:
            status = "VIOLATED" if "FAIL" in verdicts else "BLOCKED" if "BLOCKED" in verdicts else "NEEDS_REVIEW" if "NEEDS_REVIEW" in verdicts else "PASS"
        chosen = [x for x in self.steps if x[0] != "PASS"] or self.steps
        codes = sorted({x[1] for x in chosen})
        explanation = "; ".join(dict.fromkeys(x[2] for x in chosen)) or "The rule does not apply to this invoice."
        complete = "BLOCKED" not in verdicts and status not in ("UNSUPPORTED", "ERROR", "BLOCKED")
        if status == "NOT_APPLICABLE":
            complete = True
        compliance = {"PASS": "PASS", "VIOLATED": "FAIL", "NEEDS_REVIEW": "NEEDS_REVIEW"}.get(status, "NOT_EVALUATED")
        on_fail = self.rule.get("on_fail")
        on_fail = on_fail if on_fail in ("ESCALAR", "NO_PAGAR") else None
        consequence = None if status == "NOT_APPLICABLE" else on_fail if status == "VIOLATED" else "PAGAR" if status == "PASS" else "ESCALAR"
        visited = set()
        refs = list(self.refs)

        def lineage(name):
            if name in visited:
                return
            visited.add(name)
            for ref in self.fields[name]["inputs"]:
                if "field" in ref:
                    lineage(ref["field"])
                else:
                    refs.append(ref)

        for name in self.names:
            lineage(name)
        unique_refs = []
        for ref in refs:
            if ref not in unique_refs:
                unique_refs.append(ref)
        capability = "condition/1" if self.plan["kind"] == "structured" else f"canonical/{self.plan['canonical']}/3"
        return {"rule_id": self.rule["id"], "rule_ref": {"source": self.context["ruleset"]["source"], "pointer": self.pointer},
                "capability_id": None if status == "UNSUPPORTED" else capability,
                "status": status, "applicability": applicability, "compliance": compliance,
                "complete": complete, "on_fail": on_fail, "applied_consequence": consequence,
                "reason_codes": codes, "explanation": explanation,
                "inputs": [{"field": name, "fact_pointer": "/fields/" + name.replace("~", "~0").replace("/", "~1"),
                            "state": self.fields[name]["state"], "value": self.fields[name]["value"]} for name in self.names],
                "evidence_refs": unique_refs, "trace": self.traces}


def _nif_checksum(value):
    nif = (norm_nif(value) or "").removeprefix("ES")
    if not re.fullmatch(r"(?:[0-9]{8}[A-Z]|[XYZKLM][0-9]{7}[A-Z]|[ABCDEFGHJNPQRSUVW][0-9]{7}[0-9A-J])", nif):
        return False
    if nif[0] in "KLM":
        return nif[-1] == "TRWAGMYFPDXBNJZSQVHLCKE"[int(nif[1:8]) % 23]
    return nif_control_ok(nif)


def _vat(frame):
    kind_names = sorted(name for name in frame.fields if re.fullmatch(r"invoice\.taxes\.[0-9]+\.kind", name))
    kinds = [frame.value(name) for name in kind_names]
    if len(kind_names) != 1 or kinds != ["vat"]:
        frame.add("BLOCKED", "VAT_BASE_ALLOCATION_UNAVAILABLE",
                  "VAT arithmetic requires one unambiguous VAT row; per-rate bases and allocation/rounding rules are not available for other tax shapes.",
                  kind_names, "vat_arithmetic", kinds, refs=[{"source": "invoice", "pointer": "/taxes"}])
        return
    prefix = kind_names[0].removesuffix(".kind")
    names = ["invoice.taxable_base", prefix + ".rate_percent", prefix + ".amount", "invoice.currency"]
    values = [frame.value(name) for name in names]
    if any(value is None for value in values):
        code = "EVIDENCE_LINK_UNUSABLE" if any(name in frame.context.get("input_guards", {}) for name in names) else "VAT_INPUT_UNUSABLE"
        frame.add("BLOCKED", code, "VAT arithmetic requires an evidenced taxable base, stated rate, tax amount and currency.", names, "vat_arithmetic", values)
        return
    base, rate, amount, currency = values
    if currency != "EUR":
        frame.add("BLOCKED", "UNSUPPORTED_CURRENCY_COMPARISON", "VAT arithmetic currently requires explicit EUR amounts; no currency conversion was performed.", names, "vat_arithmetic", values)
        return
    parsed_rate = decimal(rate)
    if not Decimal(0) <= parsed_rate <= Decimal(100):
        frame.add("FAIL", "VAT_RATE_INVALID", "The stated VAT percentage is outside 0 through 100.", names, "vat_rate_range", values)
        return
    with localcontext(Context(prec=500)):
        expected = (decimal(base) * parsed_rate / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        difference = abs(decimal(amount) - expected)
    tolerance = decimal(frame.plan["params"]["tolerance_eur"])
    passed = difference <= tolerance
    frame.add("PASS" if passed else "FAIL", "VAT_AMOUNT_RECONCILED" if passed else "VAT_AMOUNT_MISMATCH",
              f"Stated VAT {amount} EUR; expected {format(expected, 'f')} EUR from base {base} at {rate}% rounded half-up to cents; difference {format(difference, 'f')} EUR; tolerance {format(tolerance, 'f')} EUR. This verifies arithmetic, not legal rate eligibility.",
              names, "vat_absolute_difference_lte", values, format(difference, "f"))
    frame.traces[-1]["tolerance"] = format(tolerance, "f")


def _monetary(frame, names, calculate, code, description):
    currency_names = list(dict.fromkeys("order.currency" if name.startswith("order.") else "invoice.currency" for name in names))
    values = [frame.value(name) for name in names]
    currencies = [frame.value(name) for name in currency_names]
    used = names + currency_names
    if any(value is None for value in values + currencies):
        frame.add("BLOCKED", "UNUSABLE_AMOUNT_INPUT", "Amount comparison requires usable amounts and explicit currency.", used, "decimal_difference")
        return
    if any(currency != "EUR" for currency in currencies):
        frame.add("BLOCKED", "UNSUPPORTED_CURRENCY_COMPARISON", "This arithmetic capability requires EUR for both sides; no currency conversion was performed.", used, "decimal_difference")
        return
    if any(isinstance(value, list) and not value for value in values):
        frame.add("BLOCKED", "EMPTY_LINE_ITEMS", "An empty line list cannot establish the line-sum requirement.", used, "decimal_difference")
        return
    parsed = [[decimal(v) for v in value] if isinstance(value, list) else decimal(value) for value in values]
    with localcontext(Context(prec=500)):
        difference = abs(calculate(*parsed))
    tolerance = decimal(frame.plan["params"]["tolerance_eur"])
    frame.add("PASS" if difference <= tolerance else "FAIL", "AMOUNT_RECONCILED" if difference <= tolerance else code,
              f"{description}: difference {format(difference, 'f')} EUR; tolerance {format(tolerance, 'f')} EUR.",
              used, "absolute_difference_lte", values, format(difference, "f"))
    frame.traces[-1]["tolerance"] = format(tolerance, "f")


def _duplicates(frame):
    context, params = frame.context, frame.plan["params"]
    if params["require_erp_pending"]:
        names = ["invoice.order_reference", "order.id", "order.supplier_id", "supplier.id", "erp.order_payment_status"]
        values = [frame.value(name) for name in names]
        if any(value is None for value in values) or values[0] != values[1] or values[2] != values[3]:
            frame.add("BLOCKED", "ERP_PAYMENT_STATE_UNUSABLE", "ERP rejection requires an authoritative payment state for the resolved supplier's order.", names, "erp_pending", values)
        else:
            paid = values[4] == "PAGADA"
            frame.add("FAIL" if paid else "PASS", "ERP_ALREADY_PAID" if paid else "ERP_PENDING", "ERP marks the resolved order PAGADA." if paid else "The resolved order is PENDIENTE; its existence is not a duplicate.", names, "erp_pending", values)
    for name, observation in context["history_observations"].items():
        if name != "processed" and name not in context["sources"]:
            continue
        hard = observation["hard_matches"]
        soft = observation["soft_matches"]
        names = ["invoice.number", "supplier.id"]
        if hard:
            code = {"processed": "DUPLICATE_SUBMISSION", "approved": "DUPLICATE_APPROVED_RECORD", "paid": "ALREADY_PAID"}[name]
            wording = {"processed": "submitted", "approved": "approved", "paid": "paid"}[name]
            frame.add("FAIL", code, f"The same invoice number and supplier already has a {wording} record.", names, "hard_duplicate", refs=hard)
        if soft:
            frame.add("NEEDS_REVIEW", "SOFT_DUPLICATE_MATCH", "Another record has the same amount and date; this alone does not prove a duplicate invoice.", ["invoice.total", "invoice.issue_date"], "soft_duplicate", refs=soft)
        if observation["coverage"] != "complete":
            frame.add("BLOCKED", "HISTORY_COVERAGE_INCOMPLETE", f"The {name} history cannot establish absence of other duplicates.", names, "history_coverage", refs=[r for r in observation["inputs"] if "source" in r])
        elif not hard and not soft:
            frame.add("PASS", "NO_HISTORY_DUPLICATE", f"No hard or soft match in complete {name} history.", ["invoice.number", "supplier.id", "invoice.total", "invoice.issue_date"], "history_match", refs=[r for r in observation["inputs"] if "source" in r])


def execute_rule(rule: dict, plan: dict, context: dict) -> dict:
    frame = RuleFrame(rule, plan, context)
    if plan["errors"]:
        for code in plan["errors"]:
            frame.add("BLOCKED", code, "Rule semantics are not executable: " + code)
        frame.touch([d["field_id"] for d in plan["dependencies"] if d["field_id"]])
        return frame.finish("UNSUPPORTED", "NOT_EVALUATED")
    for dep in plan["dependencies"]:
        if dep["phase"] == "unconditional":
            name = dep["field_id"]
            frame.touch([name])
            fact = frame.fields[name]
            if not usable(fact) and not (plan["canonical"] == "MISSING" and fact["state"] == "missing" and fact["extraction_quality"] not in ("uncertain", "unlinked")):
                frame.add("BLOCKED", "UNUSABLE_DECLARED_INPUT", f"Unconditional declared input is unusable: {name}.", [name], "declared_input")
    if plan["kind"] == "structured":
        condition = rule["condition"]
        applies = condition.get("applies_when")
        if applies is not None:
            app = evaluate_group(applies, frame.fields, frame.pointer + "/condition/applies_when")
            frame.touch(app["fields"])
            frame.traces.extend(app["trace"])
            if app["truth"] == "FALSE":
                if frame.steps:
                    return frame.finish("BLOCKED", "NOT_APPLICABLE")
                frame.steps.append(("PASS", "RULE_NOT_APPLICABLE", "The applicability condition is false; compliance inputs were not required."))
                return frame.finish("NOT_APPLICABLE", "NOT_APPLICABLE")
            if app["truth"] == "UNKNOWN":
                frame.steps.append(("BLOCKED", "APPLICABILITY_UNKNOWN", "Available evidence cannot establish whether the rule applies."))
                return frame.finish("BLOCKED", "UNKNOWN")
        result = evaluate_group({"logic": condition["logic"], "clauses": condition["clauses"]}, frame.fields, frame.pointer + "/condition")
        frame.touch(result["fields"])
        frame.traces.extend(result["trace"])
        verdict = "PASS" if result["truth"] == "TRUE" else condition["else"] if result["truth"] == "FALSE" else "BLOCKED"
        code = {"PASS": "CONDITION_SATISFIED", "FAIL": "CONDITION_VIOLATED", "NEEDS_REVIEW": "POLICY_REQUIRES_REVIEW", "BLOCKED": "CONDITION_UNKNOWN"}[verdict]
        explanations = {"PASS": "The compliance condition is true.", "FAIL": "The compliance condition is false.", "NEEDS_REVIEW": "The frozen policy requires review when this condition is false.", "BLOCKED": "Required condition evidence is unusable or currencies are incompatible."}
        frame.add(verdict, code, explanations[verdict], result["required_fields"], condition["logic"])
        frame.traces[-1]["node"] = frame.pointer + "/condition"
        return frame.finish()
    params, canonical = plan["params"], plan["canonical"]
    if canonical == "VENDOR":
        frame.check(["supplier.id"], lambda value: bool(value), "SUPPLIER_UNRESOLVED", "The supplier could not be resolved.", "resolved_supplier")
        if params["require_nif_in_master"]:
            frame.check(["invoice.supplier_tax_id", "supplier.tax_id"], lambda a, b: a == b, "TAX_ID_MISMATCH", "The invoice supplier tax ID differs from the master.")
        if params["check_nif_control_digit"]:
            frame.check(["invoice.supplier_tax_id"], _nif_checksum, "NIF_CONTROL_MISMATCH",
                        "The Spanish tax ID has an invalid format or control character; this is not a registry lookup.",
                        "spanish_tax_id_checksum")
        if params["require_iban_match"]:
            frame.check(["invoice.iban", "supplier.iban"], lambda a, b: a == b, "IBAN_MISMATCH", "The invoice account differs from the registered supplier account; no payment destination was selected.")
        if params["require_active"]:
            frame.check(["supplier.active"], lambda value: value is True, "SUPPLIER_INACTIVE", "The authoritative supplier status is inactive.")
    elif canonical == "DUPLICATES":
        _duplicates(frame)
    elif canonical == "AMOUNT":
        if params.get("allowed_currencies"):
            frame.check(["invoice.currency"], lambda value: value in params["allowed_currencies"], "CURRENCY_NOT_ALLOWED", "The invoice currency is outside the configured allowlist.", "in")
        if params["check_iva"]:
            _vat(frame)
        if params["check_line_items_sum"]:
            _monetary(frame, ["invoice.line_amounts", "invoice.taxable_base"], lambda lines, base: sum(lines, Decimal(0)) - base, "LINE_SUM_MISMATCH", "Line sum versus taxable base")
        if params["check_total_is_base_plus_iva"]:
            _monetary(frame, ["invoice.taxable_base", "invoice.vat_amount", "invoice.total"], lambda base, vat, total: base + vat - total, "TOTAL_MISMATCH", "Base plus VAT versus total")
        if params["check_matches_pedido"]:
            names = ["supplier.id", "order.supplier_id", "invoice.order_reference", "order.id"]
            values = [frame.value(name) for name in names]
            if any(value is None for value in values) or values[0] != values[1] or values[2] != values[3]:
                frame.add("BLOCKED", "ORDER_IDENTITY_UNUSABLE", "Order comparison requires an unambiguous order owned by the invoice supplier.", names, "order_identity", values)
            else:
                _monetary(frame, ["invoice.total", "order.total"], lambda total, order: total - order, "ORDER_AMOUNT_MISMATCH", "Invoice versus order total")
    elif canonical == "AUTHORIZATION":
        total, currency = frame.value("invoice.total"), frame.value("invoice.currency")
        threshold = decimal(params["escalate_above_eur"])
        if total is None or currency != "EUR":
            frame.add("BLOCKED", "AUTHORIZATION_INPUT_UNUSABLE", "Authorization threshold requires a usable total denominated in EUR.", ["invoice.total", "invoice.currency"], "threshold")
        else:
            above = decimal(total) > threshold
            frame.add("NEEDS_REVIEW" if above else "PASS", "AUTHORIZATION_REQUIRED" if above else "WITHIN_AUTHORIZATION_THRESHOLD", f"Invoice total {total} EUR is {'above' if above else 'at or below'} the {threshold} EUR threshold.", ["invoice.total", "invoice.currency"], "<=", [total, format(threshold, "f")])
    elif canonical == "DATES":
        if not params["allow_future"]:
            frame.check(["invoice.issue_date"], lambda value: value <= context["evaluation_date"], "FUTURE_INVOICE_DATE", "The invoice issue date is after the frozen evaluation date.", "date_lte_evaluation_date")
            frame.traces[-1]["operands"].append(context["evaluation_date"])
        if params["enforce_payment_terms"]:
            frame.check(["invoice.issue_date", "supplier.payment_terms_days"], lambda issued, days: (date.fromisoformat(context["evaluation_date"]) - date.fromisoformat(issued)).days <= days, "PAYMENT_TERMS_EXCEEDED", "Elapsed days since issue exceed the supplier's payment terms.", "elapsed_days_lte_terms", "NEEDS_REVIEW")
            frame.traces[-1]["operands"].append(context["evaluation_date"])
            issued = frame.value("invoice.issue_date")
            if issued is not None:
                frame.traces[-1]["computed_value"] = (date.fromisoformat(context["evaluation_date"]) - date.fromisoformat(issued)).days
    elif canonical == "MISSING":
        for requested in params["required_fields"]:
            name = field_id(requested, frame.fields)
            frame.check([name], lambda value: value is not None, "REQUIRED_FIELD_MISSING", f"Required field is known missing: {name}.", "exists", allow_missing=True)
    if not frame.steps:
        frame.add("BLOCKED", "NO_EXECUTABLE_ASSERTIONS", "This rule enables no executable assertions.")
    result = frame.finish()
    if result["trace"]:
        result["trace"][0]["effective_params"] = params
    return result
