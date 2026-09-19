from __future__ import annotations

import asyncio
import logging
import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from ingestion.contextual_prompt import PROMPT_VERSION, SYSTEM_PROMPT
from ingestion.contracts import canonical_bytes, digest

from .contextual_contracts import (
    EVALUATION_SCHEMA,
    RESPONSE_SCHEMA,
    REVIEW_SCHEMA,
    ReviewInputError,
    ReviewProviderError,
    pointer_get,
    strict_json,
    validate,
)

logger = logging.getLogger(__name__)
PROJECTION_LIMITATION = "Contextual review used bounded evidence excerpts; unselected source content was not reviewed."
UNRESOLVED = {"BLOCKED", "UNSUPPORTED", "ERROR", "NEEDS_REVIEW"}
USAGE_KEYS = {"prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"}
SAFE_PROVIDER_ERRORS = {"authentication_error", "rate_limited", "http_error", "transport_error",
                        "timeout", "invalid_response", "output_too_large"}


@dataclass(frozen=True)
class ContextView:
    context_id: str
    schema_version: str
    evaluation_date: str
    sources: dict
    fields: dict
    field_pointers: dict[str, str]
    ruleset: dict
    rule_bindings: list[dict]


class ContextAdapter(Protocol):
    def inspect(self, context: dict, artifacts: dict[str, bytes]) -> ContextView: ...


class DecisionContextV1Adapter:
    def inspect(self, context: dict, artifacts: dict[str, bytes]) -> ContextView:
        from .decision_context import validate_context

        if context.get("schema_version") != "decision-context/1":
            raise ReviewInputError("unsupported_context_version")
        try:
            validate_context(context, artifacts)
        except (ValueError, KeyError, TypeError, IndexError, RecursionError) as exc:
            raise ReviewInputError("invalid_context") from exc
        return ContextView(
            context["context_id"], context["schema_version"], context["evaluation_date"],
            context["sources"], context["fields"],
            {field: "/fields/" + field.replace("~", "~0").replace("/", "~1")
             for field in context["fields"]}, context["ruleset"], context["rule_bindings"],
        )


class DecisionContextV2Adapter:
    def inspect(self, context: dict, artifacts: dict[str, bytes]) -> ContextView:
        from .decision_context import ContextBundle
        from .execution_context import validate_execution_context

        if context.get("schema_version") != "decision-context/2":
            raise ReviewInputError("unsupported_context_version")
        try:
            validate_execution_context(ContextBundle(context=context, artifacts=artifacts))
        except (ValueError, KeyError, TypeError, IndexError, RecursionError) as exc:
            raise ReviewInputError("invalid_context") from exc
        return ContextView(
            context["context_id"], context["schema_version"], context["evaluation_date"],
            context["sources"], context["fields"],
            {field: "/fields/" + field.replace("~", "~0").replace("/", "~1")
             for field in context["fields"]}, context["ruleset"], context["rule_bindings"],
        )


@dataclass(frozen=True)
class ProviderReply:
    payload: dict
    model: str
    request_id: str | None = None
    usage: dict | None = None


class ReviewProvider(Protocol):
    provider: str
    model: str

    async def review(self, request: dict) -> ProviderReply: ...


@dataclass(frozen=True)
class ReviewLimits:
    timeout_seconds: float = 90.0
    max_input_bytes: int = 200_000
    max_output_bytes: int = 100_000

    def __post_init__(self):
        if (isinstance(self.timeout_seconds, bool)
                or not isinstance(self.timeout_seconds, (int, float))
                or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
            raise ReviewInputError("invalid_limits")
        for value in (self.max_input_bytes, self.max_output_bytes):
            if type(value) is not int or value <= 0:
                raise ReviewInputError("invalid_limits")


def _canonical(value) -> bytes:
    try:
        return canonical_bytes(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ReviewInputError("invalid_json") from exc


def _source_documents(view: ContextView, artifacts: dict) -> tuple[dict, list, list, list]:
    documents, unavailable, binary, partial = {}, [], [], []
    for sid, source in view.sources.items():
        if source["availability"] == "unavailable":
            if source["artifact_ref"] is not None or source["sha256"] is not None:
                raise ReviewInputError("invalid_source")
            unavailable.append(sid)
            continue
        raw = artifacts.get(source["artifact_ref"])
        if not isinstance(raw, bytes) or digest(raw) != source["sha256"]:
            raise ReviewInputError("artifact_integrity_error")
        if source["availability"] == "partial":
            partial.append(sid)
        try:
            document = strict_json(raw)
            _canonical(document)
            documents[sid] = document
        except (ValueError, UnicodeError, RecursionError) as exc:
            if source["kind"] not in {"master_workbook", "source_mapping"}:
                raise ReviewInputError("invalid_source_json") from exc
            binary.append(sid)
    return documents, unavailable, binary, partial


def _retain_pointer(projected, document, pointer):
    if pointer == "":
        return deepcopy(document)
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    result = deepcopy(projected)
    target, original = result, document
    for index, part in enumerate(parts):
        key = int(part) if isinstance(original, list) else part
        child = original[key]
        if isinstance(target, list):
            target.extend([None] * max(0, key + 1 - len(target)))
        if index == len(parts) - 1:
            target[key] = deepcopy(child)
        else:
            current = target[key] if isinstance(target, list) else target.get(key)
            if current is None:
                current = [] if isinstance(child, list) else {}
                target[key] = current
            target = current
        original = child
    return result


def build_review_request(evaluation, context, view, documents, unavailable, binary, limits,
                         *, system_prompt=SYSTEM_PROMPT, response_schema=RESPONSE_SCHEMA):
    request = {"system_prompt": system_prompt, "response_schema": deepcopy(response_schema),
               "evaluation": evaluation, "context": context, "sources": documents,
               "unavailable_sources": unavailable, "unreviewable_sources": binary}
    if len(_canonical(request)) <= limits.max_input_bytes:
        return request, False
    pointers = {sid: set() for sid in documents}

    def collect(value):
        if isinstance(value, dict):
            if (isinstance(value.get("source"), str) and value["source"] in pointers
                    and isinstance(value.get("pointer"), str)):
                pointers[value["source"]].add(value["pointer"])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(evaluation)
    collect(context["fields"])
    source_budget = limits.max_input_bytes // max(2, 2 * len(documents))
    projected, coverage = {}, {}
    for sid, document in documents.items():
        if len(_canonical(document)) <= source_budget:
            projected[sid] = document
            coverage[sid] = {"complete": True, "included_pointers": [""],
                             "original_sha256": view.sources[sid]["sha256"]}
            continue
        kept = []
        partial = [] if isinstance(document, list) else {} if isinstance(document, dict) else None
        for pointer in sorted(pointers[sid], key=lambda path: (-path.count("/"), path)):
            pointer_get(document, pointer)
            candidate = _retain_pointer(partial, document, pointer)
            if len(_canonical(candidate)) <= source_budget:
                partial = candidate
                kept.append(pointer)
        projected[sid] = partial
        coverage[sid] = {"complete": False, "included_pointers": kept,
                         "original_sha256": view.sources[sid]["sha256"]}
    request = {**request, "sources": projected, "source_projections": coverage}
    return request, any(not item["complete"] for item in coverage.values())


def _validate_evaluation(evaluation: dict, context: dict, view: ContextView, documents: dict):
    if (evaluation["context_id"] != view.context_id
            or evaluation["context_schema_version"] != view.schema_version
            or evaluation["evaluation_date"] != view.evaluation_date
            or evaluation["context_sha256"] != digest(_canonical(context))):
        raise ReviewInputError("context_identity_mismatch")
    binding = evaluation["ruleset"]
    sid = binding["source"]
    if (sid != view.ruleset["source"] or sid not in documents
            or binding["sha256"] != view.sources[sid]["sha256"]
            or any(binding[key] != view.ruleset[key] for key in ("ruleset_version", "policy_id"))):
        raise ReviewInputError("ruleset_identity_mismatch")
    ruleset = documents[sid]
    if (not isinstance(ruleset, dict)
            or any(binding[key] != ruleset.get(key) for key in ("ruleset_version", "policy_id"))
            or not isinstance(ruleset.get("rules"), list)):
        raise ReviewInputError("ruleset_identity_mismatch")
    enabled = [(i, rule) for i, rule in enumerate(ruleset["rules"]) if rule.get("enabled", True) is not False]
    ids = [rule["id"] for _, rule in enabled]
    results = evaluation["rule_results"]
    completeness = evaluation["completeness"]
    if (len(set(ids)) != len(ids) or [r["rule_id"] for r in results] != ids
            or completeness["enabled_rule_ids"] != ids
            or completeness["accounted_rule_ids"] != ids or not completeness["all_enabled_accounted"]):
        raise ReviewInputError("rule_accounting_mismatch")

    def reference(ref):
        if "field" in ref:
            if ref["field"] not in view.fields:
                raise ReviewInputError("unknown_field")
        else:
            if ref["source"] not in documents:
                source = view.sources.get(ref["source"], {})
                if (source.get("kind") in {"master_workbook", "source_mapping"}
                        and source.get("availability") in {"available", "partial"}
                        and ref["pointer"] == ""):
                    return
                raise ReviewInputError("unknown_source")
            pointer_get(documents[ref["source"]], ref["pointer"])

    for result, (index, rule) in zip(results, enabled):
        expected = {"source": sid, "pointer": f"/rules/{index}"}
        if result["rule_ref"] != expected or (
                result["on_fail"] is not None and result["on_fail"] != rule.get("on_fail")):
            raise ReviewInputError("rule_binding_mismatch")
        for item in result["inputs"]:
            field = item["field"]
            if field not in view.fields or item["fact_pointer"] != view.field_pointers.get(field):
                raise ReviewInputError("fact_binding_mismatch")
            fact = pointer_get(context, item["fact_pointer"])
            if (_canonical(fact) != _canonical(view.fields[field])
                    or fact["state"] != item["state"]
                    or _canonical(fact["value"]) != _canonical(item["value"])):
                raise ReviewInputError("fact_binding_mismatch")
        for ref in result["evidence_refs"]:
            reference(ref)
        for node in result["trace"]:
            prefix = expected["pointer"]
            if node["node"] != prefix and not node["node"].startswith(prefix + "/"):
                raise ReviewInputError("trace_binding_mismatch")
            pointer_get(ruleset, node["node"])
            for ref in node["input_refs"]:
                reference(ref)
        if result["status"] in {"BLOCKED", "UNSUPPORTED", "ERROR"} and result["complete"]:
            raise ReviewInputError("invalid_rule_completeness")
    for finding in evaluation["outstanding_findings"]:
        if not set(finding["rule_ids"]) <= set(ids):
            raise ReviewInputError("unknown_rule")
        for ref in finding["refs"]:
            reference(ref)
    if completeness["evaluation_complete"] and not all(rule["complete"] for rule in results):
        raise ReviewInputError("invalid_evaluation_completeness")
    if completeness["approval_eligible"] and (
            not completeness["evaluation_complete"] or evaluation["preliminary_decision"] != "PAGAR"
            or any(rule["status"] in UNRESOLVED for rule in results)
            or any(f["severity"] == "blocking" for f in evaluation["outstanding_findings"])):
        raise ReviewInputError("invalid_approval_eligibility")


def _validate_response(payload: dict, evaluation: dict, documents: dict, *, source_projections: dict | None = None):
    validate(RESPONSE_SCHEMA, payload, "invalid_response")
    ids = [rule["rule_id"] for rule in evaluation["rule_results"]]
    if [r["rule_id"] for r in payload["rule_reviews"]] != ids:
        raise ReviewInputError("invalid_response")
    if not set(payload["reviewed_sources"]) <= set(documents):
        raise ReviewInputError("invalid_response")

    def citations(items):
        for citation in items:
            sid = citation["source"]
            if sid not in payload["reviewed_sources"]:
                raise ReviewInputError("invalid_response")
            projection = (source_projections or {}).get(sid)
            if projection and not projection["complete"] and not any(
                    kept == "" or citation["pointer"] == kept or citation["pointer"].startswith(kept + "/")
                    for kept in projection["included_pointers"]):
                raise ReviewInputError("invalid_response")
            value = pointer_get(documents[sid], citation["pointer"])
            quote = citation["quote"]
            if (isinstance(value, str) and quote not in value) or (
                    not isinstance(value, str) and quote != _canonical(value).decode("utf-8")):
                raise ReviewInputError("invalid_response")

    for finding in payload["findings"]:
        if (finding["code"] == "CONFIRMED_DATABASE_DUPLICATE" or finding["kind"] == "AUTHORIZATION_GAP") and finding["severity"] != "blocking":
            raise ReviewInputError("invalid_response")
        if not set(finding["rule_ids"]) <= set(ids):
            raise ReviewInputError("invalid_response")
        if not finding["evidence"] and finding["kind"] != "REVIEW_LIMITATION":
            raise ReviewInputError("invalid_response")
        citations(finding["evidence"])
    for original, review in zip(evaluation["rule_results"], payload["rule_reviews"]):
        if (original["status"] in UNRESOLVED or not original["complete"]) and review["assessment"] != "UNCERTAIN":
            raise ReviewInputError("invalid_response")
        if review["assessment"] != "UNCERTAIN" and not review["evidence"]:
            raise ReviewInputError("invalid_response")
        if review["assessment"] != "SUPPORTED" and not any(
                review["rule_id"] in finding["rule_ids"] and finding["severity"] == "blocking"
                for finding in payload["findings"]):
            raise ReviewInputError("invalid_response")
        citations(review["evidence"])


def _finish(result: dict) -> dict:
    result["review_id"] = "rv_" + digest(_canonical(result))
    validate(REVIEW_SCHEMA, result, "invalid_review")
    logger.info("contextual_review_finished", extra={
        "event": "contextual_review_finished", "review_id": result["review_id"],
        "evaluation_id": result["evaluation_id"], "context_id": result["context_id"],
        "status": result["status"], "error_code": result["error"]["code"] if result["error"] else None,
    })
    return result


async def review_evaluation(evaluation: dict, context: dict, artifacts: dict[str, bytes],
                            provider: ReviewProvider, *, reviewed_at: str,
                            adapter: ContextAdapter | None = None,
                            limits: ReviewLimits | None = None) -> dict:
    limits = limits or ReviewLimits()
    evaluation, context, artifacts = deepcopy((evaluation, context, artifacts))
    if context.get("schema_version") == "decision-context/2":
        from .decision_context import ContextBundle
        from .evaluator import load_schema, validate_evaluation

        validate(load_schema(), evaluation, "invalid_evaluation")
        try:
            validate_evaluation(evaluation, ContextBundle(context=context, artifacts=artifacts))
        except (ValueError, KeyError, TypeError, IndexError, RecursionError) as exc:
            raise ReviewInputError("invalid_evaluation") from exc
        adapter = adapter or DecisionContextV2Adapter()
    else:
        validate(EVALUATION_SCHEMA, evaluation, "invalid_evaluation")
    validate({"type": "string", "format": "date-time"}, reviewed_at, "invalid_reviewed_at")
    validate({"type": "string", "minLength": 1}, provider.provider, "invalid_provider")
    validate({"type": "string", "minLength": 1}, provider.model, "invalid_provider")
    unsigned = {key: value for key, value in evaluation.items() if key != "evaluation_id"}
    if evaluation["evaluation_id"] != "ev_" + digest(_canonical(unsigned)):
        raise ReviewInputError("evaluation_identity_mismatch")
    view = (adapter or DecisionContextV1Adapter()).inspect(deepcopy(context), deepcopy(artifacts))
    documents, unavailable, binary, partial = _source_documents(view, artifacts)
    _validate_evaluation(evaluation, context, view, documents)
    ids = [rule["rule_id"] for rule in evaluation["rule_results"]]
    request, projected = build_review_request(evaluation, context, view, documents, unavailable, binary, limits)
    request_bytes = _canonical(request)
    result = {
        "schema_version": "contextual-review/1", "evaluation_id": evaluation["evaluation_id"],
        "context_id": evaluation["context_id"], "context_sha256": evaluation["context_sha256"],
        "context_schema_version": evaluation["context_schema_version"], "reviewed_at": reviewed_at,
        "original_preliminary_decision": evaluation["preliminary_decision"], "payment_authorized": False,
        "status": "FAILED", "attention_required": True, "rule_reviews": [], "findings": [],
        "limitations": [PROJECTION_LIMITATION] if projected else [],
        "coverage": {"reviewed_rule_ids": [], "unreviewed_rule_ids": ids,
                     "reviewed_sources": [], "unreviewed_sources": list(documents),
                     "unavailable_sources": unavailable, "unreviewable_sources": binary},
        "reviewer": {"provider": provider.provider, "requested_model": provider.model,
                     "resolved_model": None, "prompt_version": PROMPT_VERSION,
                     "prompt_sha256": digest(SYSTEM_PROMPT.encode("utf-8")),
                     "request_sha256": digest(request_bytes), "request_id": None, "usage": {}},
        "error": None,
    }
    if len(request_bytes) > limits.max_input_bytes:
        result["error"] = {"code": "input_too_large"}
        result["limitations"].append("The complete input exceeds the configured budget; no provider call was made.")
        return _finish(result)
    try:
        reply = await asyncio.wait_for(provider.review(deepcopy(request)), timeout=limits.timeout_seconds)
        if not isinstance(reply, ProviderReply):
            raise ReviewProviderError("invalid_response")
        if len(_canonical(reply.payload)) > limits.max_output_bytes:
            raise ReviewProviderError("output_too_large")
        _validate_response(reply.payload, evaluation, documents)
        if projected:
            _validate_response(reply.payload, evaluation, request["sources"],
                               source_projections=request["source_projections"])
        validate({"type": "string", "minLength": 1}, reply.model, "invalid_response")
        if reply.request_id is not None:
            validate({"type": "string", "minLength": 1}, reply.request_id, "invalid_response")
        if reply.usage is not None and not isinstance(reply.usage, dict):
            raise ReviewProviderError("invalid_response")
        payload = deepcopy(reply.payload)
    except TimeoutError:
        result["error"] = {"code": "timeout"}
    except ReviewInputError:
        result["error"] = {"code": "invalid_response"}
    except ReviewProviderError as exc:
        result["error"] = {"code": exc.code if exc.code in SAFE_PROVIDER_ERRORS else "provider_error"}
    except (RuntimeError, ValueError, TypeError, KeyError, IndexError, OSError, AttributeError):
        result["error"] = {"code": "provider_error"}
    if result["error"]:
        result["limitations"].append("No validated contextual review is available; the original result is unchanged.")
        return _finish(result)
    result["rule_reviews"], result["findings"] = payload["rule_reviews"], payload["findings"]
    result["limitations"] = list(dict.fromkeys([
        *result["limitations"],
        *payload["limitations"],
        *(finding["explanation"] for finding in payload["findings"] if finding["kind"] == "REVIEW_LIMITATION"),
    ]))
    for label, sources in (("Unavailable", unavailable), ("Unreviewable non-JSON", binary), ("Partial", partial)):
        if sources:
            result["limitations"].append(f"{label} sources: {', '.join(sources)}")
    missing = [sid for sid in documents if sid not in payload["reviewed_sources"]]
    if missing:
        result["limitations"].append("Sources not reviewed: " + ", ".join(missing))
    unresolved = any(r["assessment"] == "UNCERTAIN" for r in payload["rule_reviews"])
    if not evaluation["completeness"]["evaluation_complete"]:
        result["limitations"].append("Deterministic evaluation is incomplete; contextual review cannot complete it.")
    result["status"] = "INCOMPLETE" if result["limitations"] or unresolved else "COMPLETED"
    result["attention_required"] = (
        result["status"] != "COMPLETED" or evaluation["preliminary_decision"] != "PAGAR"
        or not evaluation["completeness"]["approval_eligible"]
        or any(r["assessment"] != "SUPPORTED" for r in payload["rule_reviews"])
        or any(f["severity"] == "blocking" for f in [*evaluation["outstanding_findings"], *payload["findings"]])
    )
    result["coverage"].update(reviewed_rule_ids=ids, unreviewed_rule_ids=[],
                              reviewed_sources=payload["reviewed_sources"], unreviewed_sources=missing)
    result["reviewer"].update(
        resolved_model=reply.model, request_id=reply.request_id,
        usage={key: value for key, value in (reply.usage or {}).items()
               if key in USAGE_KEYS and type(value) is int and value >= 0},
    )
    return _finish(result)
