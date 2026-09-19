from __future__ import annotations

import operator
import re
from datetime import date
from decimal import Decimal

from . import decision_registry as registry

DECIMAL = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")
MONEY = re.compile(r"^(invoice\.(total|taxable_base|vat_amount|(lines|taxes)\.[0-9]+\.amount)|order\.total)$")
OPS = frozenset(("==", "!=", "<", "<=", ">", ">=", "in", "not_in", "exists", "missing"))


class ConditionError(ValueError):
    pass


def field_id(name: str, fields: dict) -> str:
    resolved = registry.ALIASES.get(name, name) if isinstance(name, str) else None
    if resolved not in fields:
        raise ConditionError("UNKNOWN_FIELD")
    return resolved


def execution_fields(context: dict) -> dict:
    guarded = context.get("input_guards", {})
    return {name: {**fact, "extraction_quality": "uncertain"} if name in guarded else fact
            for name, fact in context["fields"].items()}


def usable(fact: dict) -> bool:
    return fact["state"] == "present" and fact["extraction_quality"] not in ("uncertain", "unlinked")


def field_type(name: str) -> str:
    if MONEY.fullmatch(name):
        return "money"
    if re.fullmatch(r"invoice\.taxes\.[0-9]+\.rate_percent", name):
        return "decimal"
    kind = registry.FIELD_TYPES.get(name)
    if kind == "nonnegative_integer":
        return "integer"
    if kind in ("date", "boolean", "decimal", "decimal_list", "source_ref_list"):
        return kind
    return "string"


def currency_field(name: str) -> str:
    return "order.currency" if name.startswith("order.") else "invoice.currency"


def decimal(value) -> Decimal:
    if not isinstance(value, str) or not DECIMAL.fullmatch(value) or len(value) > 200:
        raise ConditionError("INVALID_DECIMAL")
    return Decimal(value)


def literal(spec: dict) -> tuple[object, str, str | None]:
    if not isinstance(spec, dict):
        raise ConditionError("INVALID_LITERAL")
    kind = spec.get("type")
    expected = {"type", "amount", "currency"} if kind == "money" else {"type", "value"}
    if set(spec) != expected:
        raise ConditionError("INVALID_LITERAL")
    value = spec.get("value")
    if kind == "money":
        currency = spec["currency"]
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ConditionError("INVALID_CURRENCY")
        return decimal(spec["amount"]), kind, currency
    if kind == "decimal":
        return decimal(value), kind, None
    if kind == "integer" and type(value) is int:
        return value, kind, None
    if kind == "boolean" and type(value) is bool:
        return value, kind, None
    if kind == "string" and isinstance(value, str):
        return value, kind, None
    if kind == "string_list" and isinstance(value, list) and value and all(isinstance(x, str) for x in value):
        return value, kind, None
    if kind == "date" and isinstance(value, str):
        try:
            if date.fromisoformat(value).isoformat() == value:
                return value, kind, None
        except ValueError:
            pass
    raise ConditionError("INVALID_LITERAL")


def validate_group(group: dict, fields: dict) -> list[str]:
    if not isinstance(group, dict) or set(group) != {"logic", "clauses"}:
        raise ConditionError("INVALID_CONDITION_GROUP")
    if group["logic"] not in ("AND", "OR"):
        raise ConditionError("INVALID_CONDITION_LOGIC")
    clauses = group["clauses"]
    if not isinstance(clauses, list) or not clauses:
        raise ConditionError("EMPTY_CONDITION")
    if len(clauses) > 100:
        raise ConditionError("CONDITION_TOO_LARGE")
    dependencies = []
    for clause in clauses:
        if not isinstance(clause, dict) or not isinstance(clause.get("op"), str) or clause["op"] not in OPS:
            raise ConditionError("UNSUPPORTED_OPERATOR")
        op = clause["op"]
        expected = {"field", "op"} if op in ("exists", "missing") else {"field", "op", "value"}
        if set(clause) != expected:
            raise ConditionError("INVALID_CLAUSE")
        left = field_id(clause["field"], fields)
        dependencies.append(left)
        if op in ("exists", "missing"):
            continue
        lhs_type = field_type(left)
        if lhs_type == "money":
            dependencies.append(currency_field(left))
        rhs = clause["value"]
        if not isinstance(rhs, dict):
            raise ConditionError("UNTAGGED_OPERAND")
        if set(rhs) == {"field"}:
            right = field_id(rhs["field"], fields)
            rhs_type = field_type(right)
            dependencies.append(right)
            if rhs_type == "money":
                dependencies.append(currency_field(right))
        elif set(rhs) == {"literal"}:
            _, rhs_type, _ = literal(rhs["literal"])
        else:
            raise ConditionError("UNTAGGED_OPERAND")
        if op in ("in", "not_in"):
            if lhs_type != "string" or rhs_type != "string_list":
                raise ConditionError("OPERAND_TYPE_MISMATCH")
        elif lhs_type != rhs_type or lhs_type not in ("money", "decimal", "integer", "boolean", "string", "date"):
            raise ConditionError("OPERAND_TYPE_MISMATCH")
        elif op in ("<", "<=", ">", ">=") and lhs_type not in ("money", "decimal", "integer", "date"):
            raise ConditionError("UNORDERED_OPERAND")
    return list(dict.fromkeys(dependencies))


def validate_condition(condition: dict, fields: dict) -> dict[str, list[str]]:
    if not isinstance(condition, dict) or condition.get("schema_version") != "condition/1":
        raise ConditionError("UNVERSIONED_CONDITION")
    if set(condition) - {"kind", "schema_version", "applies_when", "logic", "clauses", "then", "else"}:
        raise ConditionError("UNKNOWN_CONDITION_PROPERTY")
    if condition.get("then") != "PASS" or condition.get("else") not in ("FAIL", "NEEDS_REVIEW"):
        raise ConditionError("INVALID_CONDITION_BRANCH")
    main = validate_group({"logic": condition.get("logic"), "clauses": condition.get("clauses")}, fields)
    applies = condition.get("applies_when")
    applicability = validate_group(applies, fields) if applies is not None else []
    return {"applicability": applicability, "compliance": main}


def _field_operand(name: str, fields: dict) -> tuple[object, str, str | None] | None:
    fact = fields[name]
    if not usable(fact):
        return None
    kind = field_type(name)
    value = fact["value"]
    currency = None
    if kind == "money":
        currency_fact = fields[currency_field(name)]
        if not usable(currency_fact):
            return None
        currency = currency_fact["value"]
        value = decimal(value)
    elif kind == "decimal":
        value = decimal(value)
    return value, kind, currency


def evaluate_group(group: dict, fields: dict, pointer: str) -> dict:
    traces, truths, clause_fields, codes = [], [], [], []
    for index, clause in enumerate(group["clauses"]):
        name = field_id(clause["field"], fields)
        fact = fields[name]
        op = clause["op"]
        used = [name]
        operands = [fact["value"]]
        truth = "UNKNOWN"
        if op in ("exists", "missing"):
            if fact["extraction_quality"] not in ("uncertain", "unlinked") and fact["state"] in ("present", "missing"):
                present = fact["state"] == "present"
                truth = "TRUE" if present == (op == "exists") else "FALSE"
        else:
            if field_type(name) == "money":
                used.append(currency_field(name))
            left = _field_operand(name, fields)
            rhs = clause["value"]
            if "field" in rhs:
                right_name = field_id(rhs["field"], fields)
                used.append(right_name)
                if field_type(right_name) == "money":
                    used.append(currency_field(right_name))
                right = _field_operand(right_name, fields)
                operands.append(fields[right_name]["value"])
            else:
                right = literal(rhs["literal"])
                operands.append(rhs["literal"])
            if left is not None and right is not None:
                a, _, ca = left
                b, _, cb = right
                if ca != cb:
                    codes.append("CURRENCY_MISMATCH")
                else:
                    compare = {"==": operator.eq, "!=": operator.ne, "<": operator.lt,
                               "<=": operator.le, ">": operator.gt, ">=": operator.ge}
                    passed = (a in b if op == "in" else a not in b) if op in ("in", "not_in") else compare[op](a, b)
                    truth = "TRUE" if passed else "FALSE"
        if truth == "UNKNOWN":
            codes.append("UNUSABLE_CONDITION_INPUT")
        truths.append(truth)
        clause_fields.append(list(dict.fromkeys(used)))
        traces.append({"node": f"{pointer}/clauses/{index}", "operation": op,
                       "input_refs": [{"field": x} for x in dict.fromkeys(used)],
                       "result": truth, "operands": operands, "required": True})
    decisive = "FALSE" if group["logic"] == "AND" else "TRUE"
    if decisive in truths:
        result = decisive
        selected = [i for i, truth in enumerate(truths) if truth == decisive]
    elif "UNKNOWN" in truths:
        result, selected = "UNKNOWN", list(range(len(truths)))
    else:
        result = "TRUE" if group["logic"] == "AND" else "FALSE"
        selected = list(range(len(truths)))
    for i, trace in enumerate(traces):
        trace["required"] = i in selected
    needed = list(dict.fromkeys(name for i in selected for name in clause_fields[i]))
    all_fields = list(dict.fromkeys(name for names in clause_fields for name in names))
    return {"truth": result, "trace": traces, "fields": all_fields, "required_fields": needed,
            "reason_codes": sorted(set(codes)) if result == "UNKNOWN" else [],
            "complete": result != "UNKNOWN"}
