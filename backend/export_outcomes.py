"""Export per-invoice deliverable rows (JSONL) from the Postgres results store.

One row per file: {"invoice", "output", "file_id", "result", "trace"}.
The output policy is deliberate: an evaluator PAGAR is only delivered when a
completed review leaves it unchallenged; review artifacts never authorize
payment on their own, and an unreviewed PAGAR escalates.
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def decide_output(row: dict | None) -> tuple[str, str]:
    """Pure policy: (output, basis) for one store row."""
    if not row or not row.get("evaluation_record_id") \
            or row.get("extraction_status") in ("failed", "unknown"):
        return "ESCALAR", "no_evaluation"
    decision = row.get("decision")
    if decision != "PAGAR":
        return (decision if decision in ("NO_PAGAR", "ESCALAR") else "ESCALAR",
                "evaluator")
    review = row.get("contextual_review") or {}
    status = row.get("review_status") or review.get("status")
    if status in ("COMPLETED", "INCOMPLETE"):
        challenged = any(
            item.get("assessment") == "CHALLENGED"
            for item in review.get("rule_reviews", []))
        blocking = any(
            finding.get("severity") == "blocking"
            and finding.get("kind") != "REVIEW_LIMITATION"
            for finding in review.get("findings", []))
        if challenged or blocking:
            return "ESCALAR", "review_challenged"
        return "PAGAR", "evaluator_confirmed_by_review"
    return "ESCALAR", "review_unavailable"


def _extraction_route(store, row: dict):
    try:
        receipt = row.get("context_receipt")
        packet = json.loads(receipt) if isinstance(receipt, str) else receipt
        outcome_ref = (packet or {}).get("outcome")
        if not outcome_ref:
            return None
        outcome = store.engine._json(outcome_ref)
        return (outcome.get("extraction") or {}).get("route")
    except Exception:  # noqa: BLE001 - route is best-effort metadata
        return None


def build_rows(store, file_ids: list[str]) -> list[dict]:
    rows = []
    for file_id in file_ids:
        row = store.get(file_id)
        if row is None:
            output, basis = "ESCALAR", "not_processed"
            row = {}
        else:
            output, basis = decide_output(row)
        trace = {
            "basis": basis,
            "evaluator_decision": row.get("decision"),
            "review_status": row.get("review_status"),
            "attention_required": row.get("attention_required"),
            "evaluation_record_id": row.get("evaluation_record_id"),
            "review_record_id": row.get("review_record_id"),
            "extraction_route": _extraction_route(store, row) if row else None,
        }
        rows.append({"invoice": file_id, "output": output, "file_id": file_id,
                     "result": output, "trace": trace})
    return rows


def write_jsonl(rows: list[dict], path) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.export_outcomes")
    parser.add_argument("--out", default="outcomes.jsonl")
    parser.add_argument("--facturas")
    parser.add_argument("--request-key")
    args = parser.parse_args(argv)

    from backend.results_store import PostgresResultsStore
    from backend.run_revision import run_status
    from rules_ingestion.engine import InvoiceDecisionEngine

    engine = InvoiceDecisionEngine.from_supabase()
    try:
        store = PostgresResultsStore(engine)
        if args.request_key:
            status = run_status(engine, args.request_key)
            if "files" not in status:
                print(
                    f"run {args.request_key} has no result yet "
                    f"(state={status.get('state')})",
                    file=sys.stderr,
                )
                return 2
            file_ids = [entry["file_id"] for entry in status["files"]]
        elif args.facturas:
            file_ids = sorted(
                unicodedata.normalize("NFC", path.name)
                for path in Path(args.facturas).glob("*.pdf"))
        else:
            file_ids = sorted(store.all())
        rows = build_rows(store, file_ids)
        write_jsonl(rows, args.out)
    finally:
        engine.close()
    outputs = Counter(row["output"] for row in rows)
    bases = Counter(row["trace"]["basis"] for row in rows)
    print(f"{len(rows)} rows -> {args.out}", file=sys.stderr)
    print("outputs: " + ", ".join(f"{k}={v}" for k, v in sorted(outputs.items())),
          file=sys.stderr)
    print("basis: " + ", ".join(f"{k}={v}" for k, v in sorted(bases.items())),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
