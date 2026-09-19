"""Author `condition/1` objects the deterministic executor actually accepts.

A rule read off a spreadsheet only changes what the engine does if it compiles
to the schema `conditions.validate_condition` accepts and `execution_rules`
runs. This module is the one place that knows how to build one, so the
authoring side (codegen, discovery) and the execution side cannot drift apart:
everything returned here has been validated against the real field registry
before it leaves the function.

Two invariants worth stating, because breaking either produced rules that were
silently UNSUPPORTED at evaluation time:

  * a condition carries ONLY {kind, schema_version, applies_when, logic,
    clauses, then, else}. `on_fail`, `params`, `runnable` and provenance live
    on the RULE, never inside the condition.
  * every operand is tagged -- {"literal": {...}} or {"field": "..."} -- and
    every literal is typed to match the field it is compared against.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from . import conditions
from . import decision_registry as registry
from .normalize import norm_importe

SCHEMA_VERSION = "condition/1"
DEFAULT_CURRENCY = "EUR"

# Fields that can only be tested for presence: there is no meaningful literal
# to compare a list of amounts or a list of source references against.
PRESENCE_ONLY = ("decimal_list", "source_ref_list")

_UNSET = object()


class AuthoringError(ValueError):
    """A condition could not be authored. The message is a stable code."""


def authoring_fields() -> dict[str, dict]:
    """A synthetic fact map so conditions validate without a live context.

    Validation only resolves names and types; the values are never read.
    """
    return {name: {"value": None, "state": "present", "extraction_quality": "exact"}
            for name in registry.FIELD_TYPES}


def resolve(name: object) -> str | None:
    """Canonical field id for a name or alias; None when it is not a field."""
    if not isinstance(name, str):
        return None
    resolved = registry.ALIASES.get(name, name)
    return resolved if resolved in registry.FIELD_TYPES else None


def kind_of(field: str) -> str:
    resolved = resolve(field)
    if resolved is None:
        raise AuthoringError("UNKNOWN_FIELD")
    return conditions.field_type(resolved)


# "5.000" in a Spanish norm sentence is five thousand, not five. norm_importe
# reads a dot-only amount as a decimal point (right for a workbook cell like
# "5.00"), so a group of exactly three digits is resolved here, before
# delegating, rather than by changing the shared parser.
_ES_THOUSANDS = re.compile(r"^-?\d{1,3}(?:\.\d{3})+$")


def decimal_text(value: object) -> str:
    """Canonical decimal text for a literal, or AuthoringError."""
    if isinstance(value, bool):
        raise AuthoringError("INVALID_LITERAL")
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d,.\-]", "", value.strip())
        if _ES_THOUSANDS.fullmatch(cleaned):
            cleaned = cleaned.replace(".", "")
        parsed = norm_importe(cleaned)
    elif isinstance(value, (int, float, Decimal)):
        parsed = norm_importe(value)
    else:
        raise AuthoringError("INVALID_LITERAL")
    if parsed is None:
        raise AuthoringError("INVALID_LITERAL")
    text = format(parsed, "f")
    if not conditions.DECIMAL.fullmatch(text):
        raise AuthoringError("INVALID_LITERAL")
    return text


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise AuthoringError("INVALID_LITERAL")
    try:
        return int(value)
    except ValueError as exc:
        raise AuthoringError("INVALID_LITERAL") from exc


def _boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise AuthoringError("INVALID_LITERAL")


def _date_text(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            if date.fromisoformat(value).isoformat() == value:
                return value
        except ValueError:
            pass
    raise AuthoringError("INVALID_LITERAL")


def _currency(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuthoringError("INVALID_CURRENCY")
    code = value.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise AuthoringError("INVALID_CURRENCY")
    return code


def _string(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise AuthoringError("INVALID_LITERAL")


def _string_list(value: object) -> list[str]:
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)) or not values:
        raise AuthoringError("INVALID_LITERAL")
    return [_string(item) for item in values]


def literal_for(field: str, value: object, *, currency: str = DEFAULT_CURRENCY) -> dict:
    """A tagged literal typed to match `field`."""
    kind = kind_of(field)
    if kind == "money":
        return {"type": "money", "amount": decimal_text(value),
                "currency": _currency(currency)}
    if kind == "decimal":
        return {"type": "decimal", "value": decimal_text(value)}
    if kind == "integer":
        return {"type": "integer", "value": _integer(value)}
    if kind == "boolean":
        return {"type": "boolean", "value": _boolean(value)}
    if kind == "date":
        return {"type": "date", "value": _date_text(value)}
    if kind == "string":
        return {"type": "string", "value": _string(value)}
    raise AuthoringError("UNCOMPARABLE_FIELD")


def clause(field: str, op: str, value: object = _UNSET, *,
           field_ref: str | None = None,
           currency: str = DEFAULT_CURRENCY) -> dict:
    """One validated clause. `field_ref` compares against another field."""
    resolved = resolve(field)
    if resolved is None:
        raise AuthoringError("UNKNOWN_FIELD")
    if op not in conditions.OPS:
        raise AuthoringError("UNSUPPORTED_OPERATOR")
    if op in ("exists", "missing"):
        return {"field": resolved, "op": op}
    if field_ref is not None:
        right = resolve(field_ref)
        if right is None:
            raise AuthoringError("UNKNOWN_FIELD")
        if conditions.field_type(right) != conditions.field_type(resolved):
            raise AuthoringError("OPERAND_TYPE_MISMATCH")
        return {"field": resolved, "op": op, "value": {"field": right}}
    if value is _UNSET:
        raise AuthoringError("INVALID_CLAUSE")
    if op in ("in", "not_in"):
        if conditions.field_type(resolved) != "string":
            raise AuthoringError("OPERAND_TYPE_MISMATCH")
        return {"field": resolved, "op": op,
                "value": {"literal": {"type": "string_list",
                                      "value": _string_list(value)}}}
    return {"field": resolved, "op": op,
            "value": {"literal": literal_for(resolved, value, currency=currency)}}


def build(clauses, *, logic: str = "AND", on_fail: str = "ESCALAR",
          applies_when: dict | None = None) -> dict:
    """Assemble and validate a condition. Raises AuthoringError with a code.

    `on_fail` is the rule's consequence and selects the `else` branch; it is
    not stored inside the condition.
    """
    if on_fail not in ("ESCALAR", "NO_PAGAR"):
        raise AuthoringError("INVALID_ON_FAIL")
    condition = {
        "kind": "structured",
        "schema_version": SCHEMA_VERSION,
        "applies_when": applies_when,
        "logic": logic if logic in ("AND", "OR") else "AND",
        "clauses": list(clauses),
        "then": "PASS",
        "else": "FAIL" if on_fail == "NO_PAGAR" else "NEEDS_REVIEW",
    }
    error = validate(condition)
    if error is not None:
        raise AuthoringError(error)
    return condition


def validate(condition: object) -> str | None:
    """None when the executor will accept this condition, else a reason code."""
    if not isinstance(condition, dict):
        return "INVALID_CONDITION"
    if condition.get("kind") != "structured":
        return "NOT_STRUCTURED"
    try:
        conditions.validate_condition(condition, authoring_fields())
    except conditions.ConditionError as exc:
        return str(exc) or "INVALID_CONDITION"
    except (TypeError, ValueError, KeyError):
        return "INVALID_CONDITION"
    return None


def comparable_fields() -> list[tuple[str, str]]:
    """(field, type) pairs a clause can compare against a literal."""
    return [(name, conditions.field_type(name))
            for name in sorted(registry.FIELD_TYPES)
            if conditions.field_type(name) not in PRESENCE_ONLY]


def catalogue_text() -> str:
    """The field vocabulary, formatted for a generation prompt."""
    aliases: dict[str, list[str]] = {}
    for alias, target in registry.ALIASES.items():
        aliases.setdefault(target, []).append(alias)
    lines = []
    for name in sorted(registry.FIELD_TYPES):
        kind = conditions.field_type(name)
        note = " [presence only: exists/missing]" if kind in PRESENCE_ONLY else ""
        also = aliases.get(name)
        alias_text = f" (aliases: {', '.join(sorted(also))})" if also else ""
        lines.append(f"  {name}: {kind}{alias_text}{note}")
    return "\n".join(lines)
