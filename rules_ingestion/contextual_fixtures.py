from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from ingestion.contracts import canonical_bytes, digest

from .contextual_review import ProviderReply
from .decision_context import SourceSnapshot, build_context

FIXTURES = ("clean-approval", "bank-mismatch", "database-duplicate",
            "erp-paid-with-blockers", "unsupported-rule", "new-bank-account")
REVIEWED_AT = "2026-09-19T12:00:00Z"
CAPTURED_AT = "2026-09-19T10:00:00Z"
NOTE = "Please use our new bank account ES6621000418401234567891."
EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples" / "decision-context"


@dataclass(frozen=True)
class ReviewFixture:
    evaluation: dict
    context: dict
    artifacts: dict[str, bytes]
    response: dict


class FixtureProvider:
    provider = "synthetic-fixture"
    model = "scripted-review-not-an-llm"

    def __init__(self, response: dict):
        self.response = deepcopy(response)
        self.calls = 0

    async def review(self, request: dict) -> ProviderReply:
        self.calls += 1
        return ProviderReply(deepcopy(self.response), self.model)


def evaluation_id(value: dict) -> dict:
    value["evaluation_id"] = "ev_" + digest(canonical_bytes({
        key: item for key, item in value.items() if key != "evaluation_id"}))
    return value


def _citation(source, pointer, value):
    return {"source": source, "pointer": pointer,
            "quote": value if isinstance(value, str) else canonical_bytes(value).decode("utf-8")}


def _finding(code, kind, rule_ids, explanation, evidence):
    return {"code": code, "kind": kind, "severity": "blocking", "rule_ids": rule_ids,
            "explanation": explanation, "evidence": evidence}


def build_fixture(name: str, *, duplicate_status: str = "processed",
                  source_overrides: dict[str, SourceSnapshot] | None = None) -> ReviewFixture:
    if name not in FIXTURES or duplicate_status not in {"submitted", "processed"}:
        raise ValueError("unknown_synthetic_fixture")
    outcome = json.loads((EXAMPLES / "outcome.json").read_bytes())
    ruleset = json.loads((EXAMPLES / "ruleset.json").read_bytes())
    specs = json.loads((EXAMPLES / "snapshots.json").read_bytes())
    invoice = outcome["invoice"]
    if name == "bank-mismatch":
        invoice["payment"]["iban"] = "ES6621000418401234567891"
    if name == "new-bank-account":
        invoice["annotations"] = [{"kind": "note", "text": NOTE}]
        outcome["evidence"].update({
            "/annotations/0/kind": [{"page": 1, "reference_ids": ["b2"]}],
            "/annotations/0/text": [{"page": 1, "reference_ids": ["b2"]}],
        })
    outcome["reading"]["pages"][0]["blocks"][0]["text"] = json.dumps(invoice, ensure_ascii=False)
    if name == "new-bank-account":
        outcome["reading"]["pages"][0]["blocks"].append({
            "id": "b2", "kind": "paragraph", "text": NOTE, "rows": [], "uncertainties": []})
    duplicate_rule = {
        "id": "R_DUPLICATES", "canonical": "DUPLICATES", "enabled": True,
        "on_fail": "NO_PAGAR", "input_fields": [], "params": {"require_erp_pending": False},
        "condition": {"kind": "python_check", "module": "rules_ingestion.checks", "function": "check_duplicates"},
    }
    unsupported = {"id": "R_UNSUPPORTED", "canonical": None, "enabled": True,
                   "on_fail": "ESCALAR", "input_fields": [], "params": {},
                   "condition": {"kind": "synthetic_unimplemented"}}
    if name == "database-duplicate":
        ruleset["rules"].append(duplicate_rule)
        specs["processed"]["payload"]["records"] = [{
            "file_id": "prior", "invoice_number": "INV-1", "supplier_id": "P042",
            "total": "121.00", "currency": "EUR", "issue_date": "2026-09-01",
            "status": duplicate_status,
        }]
    if name == "erp-paid-with-blockers":
        erp_rule = deepcopy(duplicate_rule)
        erp_rule.update(id="R_ERP", params={"require_erp_pending": True})
        ruleset["rules"] = [erp_rule, unsupported]
        specs["erp"]["payload"]["records"][0]["estado"] = "PAGADA"
    if name == "unsupported-rule":
        ruleset["rules"] = [unsupported]
    snapshots = {sid: SourceSnapshot(**{**spec, "authoritative_for": tuple(spec["authoritative_for"])})
                 for sid, spec in specs.items()}
    snapshots.update(source_overrides or {})
    bundle = build_context(outcome, snapshots=snapshots, ruleset=canonical_bytes(ruleset),
                           evaluation_date="2026-09-19", captured_at=CAPTURED_AT)
    context = bundle.context
    results, reviews, findings, outstanding = [], [], [], []
    documents = {sid: json.loads(bundle.artifacts[source["artifact_ref"]])
                 for sid, source in context["sources"].items()
                 if source["artifact_ref"] and source["kind"] not in {"master_workbook", "source_mapping"}}
    for index, rule in enumerate(ruleset["rules"]):
        rid = rule["id"]
        if rid == "R_VENDOR":
            status = "VIOLATED" if name == "bank-mismatch" else "PASS"
            fields = ["invoice.iban", "supplier.iban"]
            refs = [_citation("invoice", "/payment/iban", invoice["payment"]["iban"]),
                    _citation("suppliers", "/records/0/iban", specs["suppliers"]["payload"]["records"][0]["iban"])]
            reason = "BANK_MISMATCH" if status == "VIOLATED" else "VENDOR_MATCH"
            explanation = "Invoice and master bank accounts differ." if status == "VIOLATED" else "Invoice and master bank accounts match."
        elif rid == "R_DUPLICATES":
            status, fields, reason = "VIOLATED", ["invoice.number", "supplier.id", "history.processed_matches"], "DATABASE_DUPLICATE"
            refs = [_citation("processed", "/records/0", specs["processed"]["payload"]["records"][0]),
                    _citation("processed", "/kind", "processed"), _citation("invoice", "/invoice_number", "INV-1"),
                    _citation("suppliers", "/records/0/id", "P042")]
            explanation = "Confirmed submitted/processed duplicate; no payment evidence is asserted."
        elif rid == "R_ERP":
            status, fields, reason = "VIOLATED", ["erp.order_payment_status"], "ERP_ALREADY_PAID"
            refs = [_citation("erp", "/records/0/estado", "PAGADA")]
            explanation = "The frozen ERP record explicitly reports PAGADA."
        else:
            status, fields, reason = "UNSUPPORTED", [], "UNSUPPORTED_RULE"
            refs = [_citation("ruleset", f"/rules/{index}/condition/kind", "synthetic_unimplemented")]
            explanation = "The deterministic evaluator has no capability for this rule."
        complete = status != "UNSUPPORTED"
        rule_ref = {"source": "ruleset", "pointer": f"/rules/{index}"}
        results.append({
            "rule_id": rid, "rule_ref": rule_ref,
            "capability_id": "synthetic/" + rid if complete else None,
            "status": status, "applicability": "APPLICABLE" if complete else "NOT_EVALUATED",
            "compliance": ("FAIL" if status == "VIOLATED" else "PASS") if complete else "NOT_EVALUATED",
            "complete": complete, "on_fail": rule["on_fail"],
            "applied_consequence": (rule["on_fail"] if status == "VIOLATED" else "PAGAR") if complete else None,
            "reason_codes": [reason], "explanation": explanation,
            "inputs": [{"field": f, "fact_pointer": "/fields/" + f,
                        "state": context["fields"][f]["state"], "value": deepcopy(context["fields"][f]["value"])} for f in fields],
            "evidence_refs": [{"source": r["source"], "pointer": r["pointer"]} for r in refs],
            "trace": [{"node": f"/rules/{index}/condition", "operation": "synthetic_fixture_result",
                       "input_refs": [{"field": f} for f in fields],
                       "result": ("FALSE" if status == "VIOLATED" else "TRUE") if complete else "NOT_EVALUATED"}],
        })
        assessment = "SUPPORTED" if complete else "UNCERTAIN"
        if name == "bank-mismatch":
            findings.append(_finding("BANK_MISMATCH", "CONTRADICTORY_EVIDENCE", [rid], explanation, refs))
        if rid == "R_DUPLICATES":
            findings.append(_finding("CONFIRMED_DATABASE_DUPLICATE", "MISSED_DETAIL", [rid], explanation, refs))
        if not complete:
            findings.append(_finding("UNSUPPORTED_RULE", "REVIEW_LIMITATION", [rid], explanation, refs))
            outstanding.append({"code": "UNSUPPORTED_RULE", "severity": "blocking", "rule_ids": [rid], "refs": [rule_ref]})
        if name == "new-bank-account":
            assessment = "CHALLENGED"
            refs = [_citation("invoice", "/annotations/0/text", NOTE),
                    _citation("reading", "/pages/0/blocks/1/text", NOTE)]
            explanation = "The new-account annotation was missed. It is evidence, not permission to change payment details."
            findings.append(_finding("NEW_ACCOUNT_UNAUTHORIZED", "AUTHORIZATION_GAP", [rid], explanation, refs))
        reviews.append({"rule_id": rid, "assessment": assessment, "explanation": explanation, "evidence": refs})
    decision = {"clean-approval": "PAGAR", "bank-mismatch": "ESCALAR", "database-duplicate": "NO_PAGAR",
                "erp-paid-with-blockers": "NO_PAGAR", "unsupported-rule": "ESCALAR", "new-bank-account": "PAGAR"}[name]
    ids = [r["rule_id"] for r in results]
    evaluation = evaluation_id({
        "schema_version": "evaluation-result/1", "context_id": context["context_id"],
        "context_sha256": digest(canonical_bytes(context)), "context_schema_version": context["schema_version"],
        "evaluation_date": context["evaluation_date"],
        "ruleset": {key: context["ruleset"][key] for key in ("source", "ruleset_version", "policy_id")}
                   | {"sha256": context["sources"]["ruleset"]["sha256"]},
        "evaluator": {"version": "synthetic-fixture/1", "capability_version": "synthetic-fixture/1",
                      "implementation_sha256": digest(b"synthetic-fixture-not-a-real-evaluator")},
        "policy": {"precedence": ["NO_PAGAR", "ESCALAR", "PAGAR"],
                   "verdict_semantics": {"PASS": "PAGAR", "NEEDS_REVIEW": "ESCALAR", "FAIL": "on_fail"}},
        "rule_results": results,
        "completeness": {"enabled_rule_ids": ids, "accounted_rule_ids": ids,
                         "all_enabled_accounted": True, "evaluation_complete": all(r["complete"] for r in results),
                         "approval_eligible": decision == "PAGAR"},
        "outstanding_findings": outstanding, "preliminary_decision": decision,
    })
    response = {"rule_reviews": reviews, "findings": findings,
                "reviewed_sources": list(documents), "limitations": []}
    return ReviewFixture(evaluation, context, bundle.artifacts, response)
