from __future__ import annotations

import copy
import json
import platform
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from ingestion.contracts import canonical_bytes, digest

from . import decision_context as alignment
from .execution_context import rules_document, validate_execution_context
from .execution_rules import (
    CAPABILITY_VERSION,
    RuleFrame,
    enabled_rules,
    execute_rule,
    plan_rule,
)

VERSION = "rule-evaluator/2"
RANK = {"PAGAR": 0, "ESCALAR": 1, "NO_PAGAR": 2}
POLICY = {"precedence": ["NO_PAGAR", "ESCALAR", "PAGAR"],
          "verdict_semantics": {"PASS": "PAGAR", "NEEDS_REVIEW": "ESCALAR", "FAIL": "on_fail"}}
IMPLEMENTATION_FILES = (
    "rules_ingestion/evaluator.py", "rules_ingestion/execution_context.py",
    "rules_ingestion/execution_rules.py", "rules_ingestion/conditions.py",
    "rules_ingestion/evaluation_result.schema.json",
    "rules_ingestion/decision_context.py", "rules_ingestion/decision_context.schema.json",
    "rules_ingestion/decision_registry.py", "rules_ingestion/normalize.py", "rules_ingestion/checks.py",
    "ingestion/contracts.py", "ingestion/validation.py",
    "benchmark/schemas/invoice.json", "benchmark/schemas/reading.json",
)


@dataclass(frozen=True)
class EvaluationResult:
    _payload: bytes

    def to_dict(self) -> dict:
        return json.loads(self._payload)

    def to_json(self) -> bytes:
        return self._payload


def load_schema() -> dict:
    return json.loads(Path(__file__).with_name("evaluation_result.schema.json").read_bytes())


def implementation_identity() -> str:
    root = Path(__file__).resolve().parent.parent
    manifest = {"files": {name: digest((root / name).read_bytes()) for name in IMPLEMENTATION_FILES},
                "python": platform.python_version(),
                "dependencies": {name: version(name) for name in ("jsonschema", "referencing", "jsonschema-specifications", "attrs", "rpds-py")}}
    return digest(canonical_bytes(manifest))


_LOADED_IMPLEMENTATION = implementation_identity()


def _findings(context: dict, results: list[dict]) -> list[dict]:
    findings = []
    for original in context["findings"]:
        severity = original["severity"]
        if (original["code"] == "HISTORY_RECORDS_MALFORMED"
                and original["source_ids"]
                and all(context["history_observations"].get(name, {}).get("coverage") == "complete"
                        for name in original["source_ids"])):
            # The execution adapter has ruled out both keys for incomplete
            # unrelated rows. Retain the source-quality warning without
            # treating the older alignment matcher as a second veto.
            severity = "info"
        refs = list(original["refs"])
        refs += [{"field": name} for name in original["field_ids"] if {"field": name} not in refs]
        refs += [{"source": name, "pointer": ""} for name in original["source_ids"]
                 if context["sources"][name]["availability"] != "unavailable" and {"source": name, "pointer": ""} not in refs]
        findings.append({"code": original["code"], "severity": severity, "rule_ids": original["rule_ids"], "refs": refs})
    for result in results:
        if not result["complete"] or result["status"] in ("BLOCKED", "ERROR", "UNSUPPORTED"):
            codes = [trace["reason_code"] for trace in result["trace"] if trace.get("verdict") == "BLOCKED"] or result["reason_codes"]
            for code in dict.fromkeys(codes):
                findings.append({"code": code, "severity": "blocking", "rule_ids": [result["rule_id"]],
                                 "refs": [{"field": value["field"]} for value in result["inputs"]] + [result["rule_ref"]]})
    if results and all(result["status"] == "NOT_APPLICABLE" for result in results):
        findings.append({"code": "NO_APPLICABLE_RULES", "severity": "blocking", "rule_ids": [], "refs": []})
    if not results and not any(f["code"] == "NO_ENABLED_RULES" for f in findings):
        findings.append({"code": "NO_ENABLED_RULES", "severity": "blocking", "rule_ids": [], "refs": []})
    return findings


def _aggregate(results: list[dict], findings: list[dict]) -> tuple[str, bool]:
    decisions = [result["applied_consequence"] for result in results if result["applied_consequence"]]
    if any(f["severity"] == "blocking" for f in findings) or any(not r["complete"] for r in results) or not results:
        decisions.append("ESCALAR")
    decision = max(decisions, key=RANK.__getitem__) if decisions else "ESCALAR"
    eligible = decision == "PAGAR" and bool(results) and any(r["status"] == "PASS" for r in results)
    return decision, eligible


def _decision_reasons(decision: str, results: list[dict], findings: list[dict]) -> list[dict]:
    reasons = []
    if decision == "PAGAR":
        return [{"code": "ALL_APPLICABLE_RULES_PASSED", "rule_id": None, "consequence": decision,
                 "explanation": "Every configured active rule is accounted for, all applicable requirements pass, and no blocking finding remains. This is a recommendation, not payment authorization.",
                 "refs": [r["rule_ref"] for r in results if r["status"] == "PASS"]}]
    for result in results:
        if result["applied_consequence"] == decision:
            selected = [trace for trace in result["trace"] if trace.get("verdict") in (("FAIL",) if decision == "NO_PAGAR" else ("FAIL", "NEEDS_REVIEW", "BLOCKED"))]
            causes = [(trace["reason_code"], trace["explanation"], trace["input_refs"]) for trace in selected]
            if not causes:
                causes = [(code, result["explanation"], result["evidence_refs"]) for code in result["reason_codes"]]
            for code, explanation, refs in causes:
                reasons.append({"code": code, "rule_id": result["rule_id"], "consequence": decision,
                                "explanation": explanation, "refs": [result["rule_ref"]] + refs})
    if decision == "ESCALAR":
        explanations = {
            "UNREVIEWED_ANNOTATION": "An invoice annotation requires contextual review; its text is not business authorization.",
            "NO_ENABLED_RULES": "No active rule establishes a payment recommendation.",
            "NO_APPLICABLE_RULES": "None of the configured active rules applies to this invoice.",
            "HISTORY_UNAVAILABLE": "History coverage is incomplete or unavailable; absence of duplicates is not established.",
            "ERP_UNAVAILABLE": "The ERP snapshot does not provide a usable authoritative payment state.",
        }
        for finding in findings:
            if finding["severity"] == "blocking":
                reasons.append({"code": finding["code"], "rule_id": finding["rule_ids"][0] if len(finding["rule_ids"]) == 1 else None,
                                "consequence": "ESCALAR", "explanation": explanations.get(finding["code"], "Unresolved evidence or execution finding requires review: " + finding["code"]), "refs": finding["refs"]})
    unique = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    return unique


class CheckExecutionError(RuntimeError):
    pass


def _execute_checked(rule: dict, plan: dict, context: dict) -> dict:
    try:
        result = execute_rule(rule, plan, context)
        Draft202012Validator({"$ref": "#/$defs/ruleResult", "$defs": load_schema()["$defs"]}).validate(result)
        return result
    except Exception as exc:
        raise CheckExecutionError(type(exc).__name__) from exc


def _link_evidence(result: dict, bundle: alignment.ContextBundle) -> None:
    context = bundle.context
    evidence = alignment._load_source_document(context, bundle.artifacts, "evidence")
    reading = alignment._load_source_document(context, bundle.artifacts, "reading")
    if not isinstance(evidence, dict):
        return
    links = evidence.get("pointers", evidence)
    if not isinstance(links, dict):
        return
    prefix = "/pointers" if "pointers" in evidence else ""
    locations = {}

    def locate(value, path):
        if not isinstance(value, dict):
            return
        if isinstance(value.get("id"), str):
            locations[value["id"]] = path
        for key in ("pages", "blocks", "rows", "cells"):
            children = value.get(key)
            if isinstance(children, list):
                for index, child in enumerate(children):
                    locate(child, f"{path}/{key}/{index}")

    locate(reading, "")
    refs = result["evidence_refs"]
    for ref in list(refs):
        if ref.get("source") != "invoice" or ref["pointer"] not in links:
            continue
        pointer = ref["pointer"]
        evidence_ref = {"source": "evidence", "pointer": prefix + "/" + pointer.replace("~", "~0").replace("/", "~1")}
        if evidence_ref not in refs:
            refs.append(evidence_ref)
        entries = links[pointer] if isinstance(links[pointer], list) else [links[pointer]]
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("reference_ids"), list):
                continue
            for reference_id in entry["reference_ids"]:
                if isinstance(reference_id, str) and reference_id in locations:
                    reading_ref = {"source": "reading", "pointer": locations[reference_id]}
                    if reading_ref not in refs:
                        refs.append(reading_ref)


def evaluate(bundle: alignment.ContextBundle) -> EvaluationResult:
    if implementation_identity() != _LOADED_IMPLEMENTATION:
        raise alignment.ContextError("implementation files changed after import; restart with the pinned release")
    frozen = alignment.ContextBundle(context=copy.deepcopy(bundle.context), artifacts=dict(bundle.artifacts))
    validate_execution_context(frozen)
    context = frozen.context
    document = rules_document(frozen)
    if "verdict_semantics" in document and document["verdict_semantics"] != POLICY["verdict_semantics"]:
        raise alignment.ContextError("unsupported frozen verdict semantics")
    results = []
    for rule in enabled_rules(document):
        plan = plan_rule(rule, context)
        try:
            result = _execute_checked(rule, plan, context)
        except CheckExecutionError:
            frame = RuleFrame(rule, plan, context)
            frame.add("BLOCKED", "CHECK_EXECUTION_ERROR", "The rule could not finish because of an internal execution error; no successful conclusion was inferred.")
            result = frame.finish("ERROR", "NOT_EVALUATED")
        _link_evidence(result, frozen)
        results.append(result)
    findings = _findings(context, results)
    decision, eligible = _aggregate(results, findings)
    ruleset = context["ruleset"]
    result = {
        "schema_version": "evaluation-result/1", "context_id": context["context_id"],
        "context_sha256": digest(canonical_bytes(context)), "context_schema_version": context["schema_version"],
        "evaluation_date": context["evaluation_date"],
        "ruleset": {"source": ruleset["source"], "sha256": context["sources"][ruleset["source"]]["sha256"],
                    "ruleset_version": ruleset["ruleset_version"], "policy_id": ruleset["policy_id"]},
        "evaluator": {"version": VERSION, "capability_version": CAPABILITY_VERSION, "implementation_sha256": implementation_identity()},
        "policy": copy.deepcopy(POLICY), "rule_results": results,
        "completeness": {"enabled_rule_ids": [r["id"] for r in enabled_rules(document)],
                         "accounted_rule_ids": [r["rule_id"] for r in results], "all_enabled_accounted": True,
                         "evaluation_complete": bool(results) and all(r["complete"] for r in results), "approval_eligible": eligible},
        "outstanding_findings": findings, "preliminary_decision": decision,
        "decision_reasons": _decision_reasons(decision, results, findings),
    }
    result["evaluation_id"] = "ev_" + digest(canonical_bytes(result))
    validate_evaluation(result, frozen, validate_context=False)
    return EvaluationResult(canonical_bytes(result))


def validate_evaluation(result: dict, bundle: alignment.ContextBundle, *, validate_context: bool = True) -> None:
    if validate_context:
        validate_execution_context(bundle)
    try:
        Draft202012Validator(load_schema(), format_checker=FormatChecker()).validate(result)
    except Exception as exc:
        raise alignment.ContextError("evaluation fails schema validation") from exc
    context = bundle.context
    document = rules_document(bundle)
    rules = enabled_rules(document)
    source = context["ruleset"]["source"]
    expected_ruleset = {"source": source, "sha256": context["sources"][source]["sha256"],
                        "ruleset_version": context["ruleset"]["ruleset_version"], "policy_id": context["ruleset"]["policy_id"]}
    expected_header = {"context_id": context["context_id"], "context_sha256": digest(canonical_bytes(context)),
                       "context_schema_version": context["schema_version"], "evaluation_date": context["evaluation_date"],
                       "ruleset": expected_ruleset, "evaluator": {"version": VERSION, "capability_version": CAPABILITY_VERSION, "implementation_sha256": implementation_identity()}, "policy": POLICY}
    if any(result[key] != value for key, value in expected_header.items()):
        raise alignment.ContextError("evaluation identity does not match frozen inputs or implementation")
    expected_id = "ev_" + digest(canonical_bytes({key: value for key, value in result.items() if key != "evaluation_id"}))
    if expected_id != result["evaluation_id"]:
        raise alignment.ContextError("evaluation content digest mismatch")
    actual_ids = [r["rule_id"] for r in result["rule_results"]]
    if actual_ids != [r["id"] for r in rules]:
        raise alignment.ContextError("evaluation enabled-rule accounting mismatch")
    cache = {}

    def source_document(name):
        if name not in cache:
            cache[name] = alignment._load_source_document(context, bundle.artifacts, name)
        return cache[name]

    def check_ref(ref):
        alignment._check_input_ref(ref, context, context["fields"], source_document)

    for output, rule in zip(result["rule_results"], rules):
        if output["rule_ref"] != {"source": source, "pointer": f"/rules/{rule['_index']}"}:
            raise alignment.ContextError("evaluation rule reference mismatch")
        expected_on_fail = rule.get("on_fail") if rule.get("on_fail") in ("ESCALAR", "NO_PAGAR") else None
        if output["on_fail"] != expected_on_fail or output["status"] == "VIOLATED" and output["applied_consequence"] != expected_on_fail:
            raise alignment.ContextError("evaluation consequence does not match rule policy")
        names = [item["field"] for item in output["inputs"]]
        if len(names) != len(set(names)):
            raise alignment.ContextError("duplicate evaluation input")
        for item in output["inputs"]:
            expected_pointer = "/fields/" + item["field"].replace("~", "~0").replace("/", "~1")
            fact = context["fields"].get(item["field"])
            if fact is None or item["fact_pointer"] != expected_pointer or item["state"] != fact["state"] or item["value"] != fact["value"]:
                raise alignment.ContextError("evaluation input does not match context fact")
        for ref in output["evidence_refs"]:
            check_ref(ref)
        for trace in output["trace"]:
            check_ref({"source": source, "pointer": trace["node"]})
            if not trace["node"].startswith(output["rule_ref"]["pointer"] + "/") and trace["node"] != output["rule_ref"]["pointer"]:
                raise alignment.ContextError("trace points outside its rule")
            for ref in trace["input_refs"]:
                check_ref(ref)
    expected_findings = _findings(context, result["rule_results"])
    if expected_findings != result["outstanding_findings"]:
        raise alignment.ContextError("evaluation findings omit or alter blockers")
    for finding in expected_findings:
        for ref in finding["refs"]:
            check_ref(ref)
    decision, eligible = _aggregate(result["rule_results"], expected_findings)
    expected_completeness = {"enabled_rule_ids": actual_ids, "accounted_rule_ids": actual_ids, "all_enabled_accounted": True,
                             "evaluation_complete": bool(actual_ids) and all(r["complete"] for r in result["rule_results"]), "approval_eligible": eligible}
    if result["completeness"] != expected_completeness or result["preliminary_decision"] != decision:
        raise alignment.ContextError("evaluation aggregation is inconsistent")
    if result.get("decision_reasons") != _decision_reasons(decision, result["rule_results"], expected_findings):
        raise alignment.ContextError("evaluation decision reasons are inconsistent")
