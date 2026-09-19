"""Offline, hash-verified comparison of a product export with fixed references.

This tool never changes references or provider output. Reviewed bundles retain
an explicit label and must not be counted as automatic extraction accuracy.
"""

import argparse
import json
from pathlib import Path

from benchmark.core import file_hash, reference
from benchmark.scoring import score_invoice, summarize_fields


def audit(export, selection, output):
    entries = {d["file_id"]: d for d in json.loads(selection.read_text())["documents"]}
    outcomes = [
        json.loads(line)
        for line in (export / "outcomes.jsonl").read_text().splitlines()
    ]
    assert len(outcomes) == len(entries)
    assert {o["file_id"] for o in outcomes} == set(entries)
    documents = []
    for outcome in outcomes:
        entry = entries[outcome["file_id"]]
        assert file_hash(entry["path"]) == entry["sha256"]
        artifacts = {}
        for name, relative in outcome.get("artifacts", {}).items():
            if relative is None:
                continue
            path = export / relative
            assert file_hash(path) == outcome["artifact_hashes"][name], name
            artifacts[name] = json.loads(path.read_text())
        _, expected, provenance = reference(Path("benchmark"), entry)
        score = score_invoice(
            expected,
            artifacts.get("invoice"),
            provenance["stage3"]["excluded_pointers"],
            failure=outcome.get("error") if outcome["status"] == "failed" else None,
        )
        core = [
            d
            for d in score["details"]
            if d["reference"] is not None
            and not d["field"].startswith(("/annotations/", "/additional_fields/"))
        ]
        documents.append(
            {
                "file_id": outcome["file_id"],
                "review_assisted": bool(outcome.get("review")),
                "runtime_status": outcome["status"],
                "core_facts": summarize_fields(core),
                "core_details": core,
                "invoice_score": score,
                "checks": artifacts.get("checks"),
                "error": outcome.get("error"),
                "issues": (artifacts.get("invoice") or {}).get("issues", []),
                "source_sha256": entry["sha256"],
                "artifact_hashes": outcome.get("artifact_hashes", {}),
            }
        )
    report = {
        "export": str(export),
        "limitations": [
            "Consult the selection and run history for development exposure; a small sample does not establish corpus-wide accuracy.",
            "Draft reference uncertain fields are excluded; core score does not establish every character or annotation.",
            "Review-assisted output is not automatic extraction accuracy.",
        ],
        "documents": documents,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for d in documents:
        print(d["file_id"], "review_assisted=", d["review_assisted"], d["core_facts"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.export, args.selection, args.output)
