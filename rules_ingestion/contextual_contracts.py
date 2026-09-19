from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

NAME = {"type": "string", "minLength": 1}
TEXT = {"type": "string"}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
POINTER = {"type": "string", "pattern": r"^(?:/(?:[^~/]|~[01])*)*$"}
BOOL = {"type": "boolean"}
DECISION = {"enum": ["PAGAR", "ESCALAR", "NO_PAGAR"]}


def obj(**properties: Any) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def array(items: dict, *, unique: bool = False) -> dict:
    return {"type": "array", "items": items, "uniqueItems": unique}


def nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


NAMES = array(NAME, unique=True)
SOURCE_REF = obj(source=NAME, pointer=POINTER)
REF = {"oneOf": [obj(field=NAME), SOURCE_REF]}
RULE_RESULT = obj(
    rule_id=NAME, rule_ref=SOURCE_REF, capability_id=nullable(NAME),
    status={"enum": ["PASS", "VIOLATED", "NEEDS_REVIEW", "NOT_APPLICABLE",
                     "BLOCKED", "UNSUPPORTED", "ERROR"]},
    applicability={"enum": ["APPLICABLE", "NOT_APPLICABLE", "UNKNOWN", "NOT_EVALUATED"]},
    compliance={"enum": ["PASS", "FAIL", "NEEDS_REVIEW", "NOT_EVALUATED"]},
    complete=BOOL, on_fail={"enum": ["ESCALAR", "NO_PAGAR", None]},
    applied_consequence=nullable(DECISION), reason_codes=array(NAME), explanation=TEXT,
    inputs=array(obj(field=NAME, fact_pointer=POINTER,
                     state={"enum": ["present", "missing", "ambiguous", "invalid", "unavailable"]},
                     value={})),
    evidence_refs=array(REF),
    trace=array(obj(node=POINTER, operation=NAME, input_refs=array(REF),
                    result={"enum": ["TRUE", "FALSE", "UNKNOWN", "NOT_EVALUATED"]})),
)
EVALUATION_SCHEMA = obj(
    schema_version={"const": "evaluation-result/1"},
    evaluation_id={"type": "string", "pattern": "^ev_[a-f0-9]{64}$"},
    context_id=NAME, context_sha256=HASH, context_schema_version=NAME,
    evaluation_date={"type": "string", "format": "date"},
    ruleset=obj(source=NAME, sha256=HASH, ruleset_version=NAME, policy_id=NAME),
    evaluator=obj(version=NAME, capability_version=NAME, implementation_sha256=HASH),
    policy=obj(precedence={"const": ["NO_PAGAR", "ESCALAR", "PAGAR"]},
               verdict_semantics={"const": {"PASS": "PAGAR", "NEEDS_REVIEW": "ESCALAR", "FAIL": "on_fail"}}),
    rule_results=array(RULE_RESULT),
    completeness=obj(enabled_rule_ids=NAMES, accounted_rule_ids=NAMES,
                     all_enabled_accounted=BOOL, evaluation_complete=BOOL, approval_eligible=BOOL),
    outstanding_findings=array(obj(code=NAME, severity={"enum": ["info", "blocking"]},
                                   rule_ids=NAMES, refs=array(REF))),
    preliminary_decision=DECISION,
)
CITATION = obj(source=NAME, pointer=POINTER, quote=NAME)
RULE_REVIEW = obj(rule_id=NAME, assessment={"enum": ["SUPPORTED", "CHALLENGED", "UNCERTAIN"]},
                  explanation=NAME, evidence=array(CITATION))
FINDING = obj(code=NAME,
              kind={"enum": ["MISSED_DETAIL", "CONTRADICTORY_EVIDENCE", "UNSUPPORTED_CONCLUSION",
                              "AUTHORIZATION_GAP", "REVIEW_LIMITATION"]},
              severity={"enum": ["info", "blocking"]}, rule_ids=NAMES,
              explanation=NAME, evidence=array(CITATION))
RESPONSE_SCHEMA = obj(rule_reviews=array(RULE_REVIEW), findings=array(FINDING),
                      reviewed_sources=NAMES, limitations=array(NAME))
REVIEW_SCHEMA = obj(
    schema_version={"const": "contextual-review/1"},
    review_id={"type": "string", "pattern": "^rv_[a-f0-9]{64}$"},
    evaluation_id=EVALUATION_SCHEMA["properties"]["evaluation_id"],
    context_id=NAME, context_sha256=HASH, context_schema_version=NAME,
    reviewed_at={"type": "string", "format": "date-time"},
    original_preliminary_decision=DECISION, payment_authorized={"const": False},
    status={"enum": ["COMPLETED", "INCOMPLETE", "FAILED"]}, attention_required=BOOL,
    rule_reviews=array(RULE_REVIEW), findings=array(FINDING), limitations=array(NAME),
    coverage=obj(reviewed_rule_ids=NAMES, unreviewed_rule_ids=NAMES, reviewed_sources=NAMES,
                 unreviewed_sources=NAMES, unavailable_sources=NAMES, unreviewable_sources=NAMES),
    reviewer=obj(provider=NAME, requested_model=NAME, resolved_model=nullable(NAME),
                 prompt_version=NAME, prompt_sha256=HASH, request_sha256=nullable(HASH),
                 request_id=nullable(NAME), usage={"type": "object", "additionalProperties": {
                     "type": "integer", "minimum": 0}, "propertyNames": {"enum": [
                         "prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"]}}),
    error=nullable(obj(code=NAME)),
)


class ReviewInputError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ReviewProviderError(RuntimeError):
    def __init__(self, code: str, *, raw: bytes | None = None,
                 detail: str | None = None):
        self.code = code
        self.raw = raw
        self.detail = detail
        super().__init__(code)


FORMATS = FormatChecker()


@FORMATS.checks("date-time", raises=(ValueError, TypeError))
def _date_time(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value):
        return False
    return datetime.fromisoformat(value.upper()).tzinfo is not None


def validate(schema: dict, value: Any, code: str) -> None:
    if next(Draft202012Validator(schema, format_checker=FORMATS).iter_errors(value), None):
        raise ReviewInputError(code)


def strict_json(data: str | bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("nonfinite_json")

    def number(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("nonfinite_json")
        return value

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)


def pointer_get(document: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or not re.fullmatch(POINTER["pattern"], pointer):
        raise ReviewInputError("invalid_pointer")
    node = document
    if not pointer:
        return node
    try:
        for raw in pointer[1:].split("/"):
            key = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(node, list) and re.fullmatch(r"0|[1-9][0-9]*", key):
                node = node[int(key)]
            elif isinstance(node, dict):
                node = node[key]
            else:
                raise KeyError(key)
    except (KeyError, IndexError, ValueError) as exc:
        raise ReviewInputError("unresolved_pointer") from exc
    return node
