from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ingestion.contracts import canonical_bytes

from .decision_context import SourceSnapshot, build_context
from .evaluation_cli import evaluation_packet
from .execution_context import prepare_context

CASES = ("clean", "bank-mismatch", "database-duplicate", "already-paid-blocked", "unsupported", "bank-note")
ROOT = Path(__file__).resolve().parents[1] / "docs" / "examples" / "decision-context"
CAPTURED_AT = "2026-09-19T10:00:00Z"


def example_bundle(case: str):
    if case not in CASES:
        raise ValueError("unknown synthetic example")
    outcome = json.loads((ROOT / "outcome.json").read_bytes())
    ruleset = json.loads((ROOT / "ruleset.json").read_bytes())
    specs = json.loads((ROOT / "snapshots.json").read_bytes())
    ruleset["policy_id"] = "synthetic-execution-examples"
    ruleset["ruleset_version"] = "example-" + case
    invoice = outcome["invoice"]
    file_id = "synthetic-" + case
    invoice["file_id"] = file_id
    outcome["file_id"] = file_id
    outcome["reading"]["file_id"] = file_id
    duplicate_rule = {
        "id": "R_DUPLICATES", "canonical": "DUPLICATES", "enabled": True,
        "on_fail": "NO_PAGAR", "params": {}, "input_fields": [],
        "condition": {"kind": "python_check", "module": "rules_ingestion.checks", "function": "check_duplicates"},
    }
    if case == "bank-mismatch":
        invoice["payment"]["iban"] = "ES6621000418401234567891"
    elif case == "database-duplicate":
        ruleset["rules"] = [duplicate_rule]
        specs["processed"]["availability"] = "partial"
        specs["processed"]["payload"] = {"kind": "processed", "complete": False, "records": [{
            "file_id": "earlier-submission.pdf", "invoice_number": invoice["invoice_number"],
            "supplier_id": specs["suppliers"]["payload"]["records"][0]["id"],
            "total": invoice["totals"]["total"], "currency": invoice["currency"], "issue_date": invoice["issue_date"],
        }]}
    elif case == "already-paid-blocked":
        specs["erp"]["payload"]["records"][0]["estado"] = "PAGADA"
        ruleset["rules"].append(duplicate_rule)
        ruleset["rules"][0]["params"]["require_active"] = True
    elif case == "unsupported":
        ruleset["rules"].append({
            "id": "R_UNSUPPORTED", "canonical": None, "enabled": True, "on_fail": "ESCALAR",
            "params": {}, "input_fields": [],
            "condition": {"kind": "structured", "schema_version": "condition/1", "logic": "AND",
                          "clauses": [], "then": "PASS", "else": "FAIL"},
        })
    elif case == "bank-note":
        invoice["annotations"] = [{"kind": "note", "text": "Nueva cuenta bancaria: solicitamos pagar en la cuenta indicada en esta nota."}]
        evidence = outcome["evidence"].get("pointers", outcome["evidence"])
        block = outcome["reading"]["pages"][0]["blocks"][0]
        for name in ("kind", "text"):
            evidence[f"/annotations/0/{name}"] = [{"page": 1, "reference_ids": [block["id"]]}]
    text = f"Factura {invoice['invoice_number']}. NIF {invoice['supplier']['tax_id']}. IBAN {invoice['payment']['iban']}. Total {invoice['totals']['total']} {invoice['currency']}."
    if invoice.get("annotations"):
        text += " " + " ".join(note["text"] for note in invoice["annotations"])
    outcome["reading"]["pages"][0]["blocks"][0]["text"] = text
    snapshots = {name: SourceSnapshot(**{**spec, "authoritative_for": tuple(spec["authoritative_for"])}) for name, spec in specs.items()}
    return prepare_context(build_context(outcome, snapshots=snapshots, ruleset=ruleset,
                                         evaluation_date="2026-09-19", captured_at=CAPTURED_AT))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m rules_ingestion.evaluation_examples")
    parser.add_argument("--case", choices=CASES, required=True)
    args = parser.parse_args(argv)
    packet = evaluation_packet(example_bundle(args.case), include_artifacts=True)
    packet["synthetic"] = True
    sys.stdout.write(canonical_bytes(packet).decode("utf-8") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
