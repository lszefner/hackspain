from __future__ import annotations

import copy
import json
import re
from datetime import date
from decimal import Context, Decimal, localcontext

from jsonschema import Draft202012Validator, FormatChecker

from ingestion.contracts import Contracts, canonical_bytes, digest
from ingestion.validation import validate_interpretation

from . import decision_context as alignment
from .conditions import ConditionError, decimal, execution_fields, usable
from .execution_rules import CAPABILITY_VERSION, bind_rule, enabled_rules

SCHEMA_VERSION = "decision-context/2"
ADAPTER_VERSION = "decision-adapter/3"


def load_schema() -> dict:
    schema = copy.deepcopy(alignment.load_schema())
    schema["title"] = "Traceable rule execution context"
    schema["properties"]["schema_version"] = {"const": SCHEMA_VERSION}
    schema["properties"]["adapter_version"] = {"const": ADAPTER_VERSION}
    schema["properties"]["fields"]["patternProperties"][r"^invoice\.taxes\.(0|[1-9][0-9]*)\.rate_percent$"] = {"$ref": "#/$defs/decimalFact"}
    schema["required"] += ["alignment_context_id", "capability_version", "history_observations", "input_guards"]
    schema["properties"]["input_guards"] = {"type": "object", "additionalProperties": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}}}
    schema["properties"]["alignment_context_id"] = {"type": "string", "pattern": "^dc_[a-f0-9]{64}$"}
    schema["properties"]["capability_version"] = {"const": CAPABILITY_VERSION}
    dependency = schema["$defs"]["binding"]["properties"]["dependencies"]["items"]
    dependency["required"] += ["phase", "required"]
    dependency["properties"].update({"phase": {"enum": ["applicability", "compliance", "unconditional"]}, "required": {"type": "boolean"}})
    observation = {
        "type": "object", "additionalProperties": False,
        "required": ["hard_matches", "soft_matches", "coverage", "reason_codes", "inputs"],
        "properties": {
            "hard_matches": {"type": "array", "items": {"$ref": "#/$defs/sourceRef"}},
            "soft_matches": {"type": "array", "items": {"$ref": "#/$defs/sourceRef"}},
            "coverage": {"enum": ["complete", "partial", "unavailable"]},
            "reason_codes": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
            "inputs": {"type": "array", "items": {"$ref": "#/$defs/inputRef"}},
        },
    }
    schema["properties"]["history_observations"] = {
        "type": "object", "additionalProperties": False,
        "required": ["processed", "approved", "paid"],
        "properties": {name: copy.deepcopy(observation) for name in ("processed", "approved", "paid")},
    }
    return schema


def rules_document(bundle: alignment.ContextBundle) -> dict:
    source = bundle.context["sources"][bundle.context["ruleset"]["source"]]
    raw = bundle.artifacts[source["artifact_ref"]]
    alignment._parse_ruleset(raw)
    document = json.loads(raw, parse_float=Decimal)
    for index, rule in enumerate(document["rules"]):
        rule["_index"] = index
    return document


def _tax_rate_fields(bundle: alignment.ContextBundle) -> dict:
    context = bundle.context
    invoice = alignment._load_source_document(context, bundle.artifacts, "invoice")
    reading = alignment._load_source_document(context, bundle.artifacts, "reading")
    evidence = alignment._load_source_document(context, bundle.artifacts, "evidence")
    raw_links = evidence.get("pointers", evidence) if isinstance(evidence, dict) else {}
    links = {key: [entry for entry in (value if isinstance(value, list) else [value]) if isinstance(entry, dict)]
             for key, value in raw_links.items()} if isinstance(raw_links, dict) else {}
    uncertain_refs = set()
    if isinstance(reading, dict):
        for page in reading.get("pages", []):
            for block in page.get("blocks", []):
                if not block.get("uncertainties"):
                    continue
                uncertain_refs.add(block.get("id"))
                for row in block.get("rows", []):
                    uncertain_refs.add(row.get("id"))
                    uncertain_refs.update(cell.get("id") for cell in row.get("cells", []))
    return {f"invoice.taxes.{index}.rate_percent": alignment._invoice_scalar(
                invoice, f"/taxes/{index}/rate_percent", tax.get("rate_percent"), "decimal", links, uncertain_refs)
            for index, tax in enumerate(invoice.get("taxes") or [])}


def _input_guards(bundle: alignment.ContextBundle) -> dict:
    context = bundle.context
    fields = context["fields"]
    invoice = alignment._load_source_document(context, bundle.artifacts, "invoice")
    reading = alignment._load_source_document(context, bundle.artifacts, "reading")
    evidence = alignment._load_source_document(context, bundle.artifacts, "evidence")
    pointers = evidence.get("pointers", evidence) if isinstance(evidence, dict) else {}
    contracts = Contracts()
    good_pointers = {}
    for fact in fields.values():
        for ref in fact["inputs"]:
            if ref.get("source") != "invoice":
                continue
            pointer = ref["pointer"]
            if pointer in good_pointers:
                continue
            value = alignment._pointer_get(invoice, pointer)
            if value is None or isinstance(value, (dict, list)) or pointer.startswith("/issues/"):
                good_pointers[pointer] = True
                continue
            if not isinstance(pointers, dict) or pointer not in pointers or not isinstance(reading, dict):
                good_pointers[pointer] = False
                continue
            with localcontext(Context(prec=28)):
                checks = validate_interpretation(invoice, {pointer: pointers[pointer]}, reading, contracts)["checks"]
            good_pointers[pointer] = all(checks[key] for key in ("reading_schema", "evidence_references", "foreground_evidence_only"))
    guarded = {}

    def blocked(name):
        if name not in guarded:
            guarded[name] = any(blocked(ref["field"]) if "field" in ref else ref["source"] == "invoice" and not good_pointers.get(ref["pointer"], True) for ref in fields[name]["inputs"])
        return guarded[name]

    return {name: ["EVIDENCE_LINK_UNUSABLE"] for name in fields if blocked(name)}


def _history_observation(name: str, bundle: alignment.ContextBundle) -> dict:
    context = bundle.context
    fields = execution_fields(context)
    hard_fields = ("invoice.number", "supplier.id")
    soft_fields = ("invoice.total", "invoice.issue_date", "invoice.currency")
    out = {"hard_matches": [], "soft_matches": [], "coverage": "unavailable",
           "reason_codes": [], "inputs": [{"field": key} for key in hard_fields + soft_fields]}
    source = context["sources"].get(name)
    if not source or source["availability"] == "unavailable":
        out["reason_codes"] = ["HISTORY_UNAVAILABLE"]
        return out
    if source["kind"] != "history" or f"history.{name}" not in source["authoritative_for"]:
        out["reason_codes"] = ["HISTORY_AUTHORITY_MISSING"]
        return out
    payload = json.loads(bundle.artifacts[source["artifact_ref"]])
    if not isinstance(payload, dict) or payload.get("kind") != name or not isinstance(payload.get("records"), list):
        out["reason_codes"] = ["HISTORY_SHAPE_INVALID"]
        return out
    out["inputs"].append({"source": name, "pointer": "/records"})
    hard_ready = all(usable(fields[key]) for key in hard_fields)
    soft_ready = all(usable(fields[key]) for key in soft_fields)
    complete = source["availability"] == "available" and payload.get("complete") is True and hard_ready and soft_ready
    for index, row in enumerate(payload["records"]):
        if not isinstance(row, dict):
            complete = False
            continue
        if name == "processed" and row.get("file_id") == context["file_id"]:
            continue
        number, supplier = row.get("invoice_number"), row.get("supplier_id")
        key_valid = isinstance(number, str) and bool(number) and isinstance(supplier, str) and bool(supplier)
        raw_total = row.get("total")
        total = None
        try:
            total = decimal(str(raw_total) if type(raw_total) is int else raw_total)
            issued = row.get("issue_date")
            currency = row.get("currency")
            soft_valid = isinstance(issued, str) and date.fromisoformat(issued).isoformat() == issued and isinstance(currency, str) and bool(re.fullmatch(r"[A-Z]{3}", currency))
        except (ConditionError, ValueError, TypeError):
            soft_valid = False
        if not key_valid or not soft_valid:
            complete = False
        hard = key_valid and hard_ready and number == fields["invoice.number"]["value"] and supplier == fields["supplier.id"]["value"]
        soft = key_valid and soft_valid and soft_ready and total == decimal(fields["invoice.total"]["value"]) and row["issue_date"] == fields["invoice.issue_date"]["value"] and row["currency"] == fields["invoice.currency"]["value"]
        ref = {"source": name, "pointer": f"/records/{index}"}
        if hard:
            out["hard_matches"].append(ref)
        elif soft:
            out["soft_matches"].append(ref)
    out["coverage"] = "complete" if complete else "partial"
    if not complete:
        out["reason_codes"] = ["HISTORY_COVERAGE_INCOMPLETE"]
    return out


def _preflight(context: dict) -> dict:
    return {
        "data_status": "blocked" if any(f["severity"] == "blocking" for f in context["findings"]) or any(b["input_status"] == "blocked" for b in context["rule_bindings"]) else "ready",
        "execution_status": "blocked" if any(b["execution_status"] == "unsupported" for b in context["rule_bindings"]) else "supported",
    }


def _promote(bundle: alignment.ContextBundle) -> alignment.ContextBundle:
    context = copy.deepcopy(bundle.context)
    context["fields"].update(_tax_rate_fields(bundle))
    context.update({"schema_version": SCHEMA_VERSION, "adapter_version": ADAPTER_VERSION,
                    "alignment_context_id": bundle.context["context_id"], "capability_version": CAPABILITY_VERSION})
    guarded_bundle = alignment.ContextBundle(context=context, artifacts=bundle.artifacts)
    context["input_guards"] = _input_guards(guarded_bundle)
    context["history_observations"] = {name: _history_observation(name, guarded_bundle) for name in ("processed", "approved", "paid")}
    context["rule_bindings"] = [bind_rule(rule, context) for rule in enabled_rules(rules_document(bundle))]
    context["preflight"] = _preflight(context)
    context["context_id"] = "dc_" + digest(canonical_bytes({k: v for k, v in context.items() if k != "context_id"}))
    return alignment.ContextBundle(context=context, artifacts=dict(bundle.artifacts))


def prepare_context(bundle: alignment.ContextBundle) -> alignment.ContextBundle:
    frozen = alignment.ContextBundle(context=copy.deepcopy(bundle.context), artifacts=dict(bundle.artifacts))
    with localcontext(Context(prec=28)):
        alignment.validate_context(frozen.context, frozen.artifacts)
    result = _promote(frozen)
    Draft202012Validator(load_schema(), format_checker=FormatChecker()).validate(result.context)
    return result


def validate_execution_context(bundle: alignment.ContextBundle) -> None:
    errors = list(Draft202012Validator(load_schema(), format_checker=FormatChecker()).iter_errors(bundle.context))
    if errors:
        raise alignment.ContextError("execution context fails schema validation")
    parent = copy.deepcopy(bundle.context)
    parent_id = parent.pop("alignment_context_id")
    parent.pop("capability_version")
    parent.pop("history_observations")
    parent.pop("input_guards")
    parent["fields"] = {name: fact for name, fact in parent["fields"].items()
                        if not re.fullmatch(r"invoice\.taxes\.[0-9]+\.rate_percent", name)}
    parent["schema_version"] = alignment.SCHEMA_VERSION
    parent["adapter_version"] = alignment.registry.ADAPTER_VERSION
    parent["rule_bindings"], _ = alignment._bind_rules(rules_document(bundle), parent["fields"])
    parent["preflight"] = _preflight(parent)
    parent["context_id"] = parent_id
    parent_bundle = alignment.ContextBundle(context=parent, artifacts=bundle.artifacts)
    with localcontext(Context(prec=28)):
        alignment.validate_context(parent, bundle.artifacts)
    expected = _promote(parent_bundle)
    if expected.context != bundle.context:
        raise alignment.ContextError("execution context does not match frozen alignment and capability reconstruction")
