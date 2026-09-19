from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from ingestion.storage import canonical_json_bytes

from .decision_context import SourceSnapshot, build_context, evaluate_context
from .decision_storage import create_decision_store


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python -m rules_ingestion.decision_cli")
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--ruleset", required=True)
    parser.add_argument("--snapshots", required=True)
    parser.add_argument("--evaluation-date", required=True)
    parser.add_argument("--backend", required=True,
                        choices=("local", "supabase"))
    parser.add_argument("--local-root", default="output/decision-contexts")
    parser.add_argument("--captured-at", default=None)
    return parser.parse_args(argv)


def _load_snapshots(path: str, captured_at: str) -> dict[str, SourceSnapshot]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("snapshots file must be a JSON object")
    snapshots = {}
    for source_id, spec in raw.items():
        if not isinstance(spec, dict):
            raise TypeError(f"snapshot {source_id!r} must be an object")
        payload = spec.get("payload")
        payload_file = spec.get("payload_file")
        if payload_file is not None:
            payload = Path(payload_file).read_bytes()
        snapshots[source_id] = SourceSnapshot(
            kind=spec["kind"], payload=payload,
            captured_at=spec.get("captured_at") or captured_at,
            asserted_by=spec.get("asserted_by", "decision-cli"),
            authoritative_for=tuple(spec.get("authoritative_for") or ()),
            scope=spec.get("scope", "cli snapshot"),
            availability=spec.get("availability", "available"),
            as_of=spec.get("as_of"))
    return snapshots


def main(argv=None) -> int:
    try:
        args = _parse_args(argv)
        captured_at = args.captured_at or datetime.now(UTC).isoformat()
        outcome = json.loads(Path(args.outcome).read_text(encoding="utf-8"))
        ruleset = Path(args.ruleset).read_bytes()
        snapshots = _load_snapshots(args.snapshots, captured_at)
        bundle = build_context(outcome, snapshots=snapshots,
                               ruleset=ruleset,
                               evaluation_date=args.evaluation_date,
                               captured_at=captured_at)
        store = create_decision_store(args.backend,
                                      local_root=Path(args.local_root))
        try:
            receipt = store.save(bundle)
            reloaded = store.load(receipt["context_id"])
            evaluation = evaluate_context(reloaded)
        finally:
            store.close()
        output = {
            "receipt": receipt,
            "decision": evaluation["decision"],
            "checks": evaluation["checks"],
            "preflight": reloaded.context["preflight"],
        }
        sys.stdout.write(
            canonical_json_bytes(output).decode("utf-8") + "\n")
        return 0
    except Exception as exc:
        sys.stderr.write(
            f"error: {type(exc).__name__}: decision_context_failed; "
            "check configuration and source artifacts\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
