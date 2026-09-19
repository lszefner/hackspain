from __future__ import annotations

import argparse
import base64
import copy
import json
import sys
from pathlib import Path

from jsonschema import ValidationError

from ingestion.contracts import canonical_bytes

from .decision_context import SourceSnapshot, build_context
from .evaluator import evaluate
from .execution_context import prepare_context


def evaluation_packet(bundle, *, include_artifacts=False) -> dict:
    result = evaluate(bundle)
    output = {"evaluation_result": result.to_dict(), "decision_context": copy.deepcopy(bundle.context)}
    if include_artifacts:
        documents, opaque, raw_artifacts = {}, {}, {}
        for source, metadata in bundle.context["sources"].items():
            ref = metadata["artifact_ref"]
            if ref is None:
                continue
            try:
                documents[source] = json.loads(bundle.artifacts[ref])
                raw_artifacts[ref] = base64.b64encode(bundle.artifacts[ref]).decode("ascii")
            except (UnicodeDecodeError, json.JSONDecodeError):
                opaque[source] = ref
        output.update({"source_documents": documents, "opaque_artifacts": opaque,
                       "artifact_bytes_base64": raw_artifacts})
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rules_ingestion.evaluation_cli")
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--ruleset", required=True)
    parser.add_argument("--snapshots", required=True)
    parser.add_argument("--evaluation-date", required=True)
    parser.add_argument("--captured-at", required=True)
    parser.add_argument("--include-artifacts", action="store_true")
    args = parser.parse_args(argv)
    try:
        outcome = json.loads(Path(args.outcome).read_bytes())
        ruleset = Path(args.ruleset).read_bytes()
        specs = json.loads(Path(args.snapshots).read_bytes())
        snapshots = {}
        for source, spec in specs.items():
            payload = Path(spec["payload_file"]).read_bytes() if spec.get("payload_file") is not None else spec.get("payload")
            snapshots[source] = SourceSnapshot(
                kind=spec["kind"], payload=payload,
                captured_at=spec.get("captured_at", args.captured_at),
                asserted_by=spec.get("asserted_by", "evaluation-cli"),
                authoritative_for=tuple(spec.get("authoritative_for", [])),
                scope=spec.get("scope", "frozen offline snapshot"),
                availability=spec.get("availability", "available"), as_of=spec.get("as_of"))
        parent = build_context(outcome, snapshots=snapshots, ruleset=ruleset,
                               evaluation_date=args.evaluation_date, captured_at=args.captured_at)
        bundle = prepare_context(parent)
        output = evaluation_packet(bundle, include_artifacts=args.include_artifacts)
        sys.stdout.write(canonical_bytes(output).decode("utf-8") + "\n")
        return 0
    except (ArithmeticError, AttributeError, KeyError, OSError, TypeError, ValueError, ValidationError) as exc:
        sys.stderr.write(f"error: {type(exc).__name__}: evaluation_failed; check frozen inputs and supported capabilities\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
