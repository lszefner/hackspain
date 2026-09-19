"""Offline bridge from product exports to the independent benchmark evaluator.

Never invokes provider adapters or modifies reference annotations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .core import dump, file_hash, key, load, manifest, validate
from .reporting import score_run
from .runner import create_run, import_output, run_path


def import_product(manifest_path, export_path, run_id, *, split="all"):
    mp = Path(manifest_path).resolve()
    root = Path(export_path).resolve()
    selected = manifest(mp)
    config = load(root / "config.json")
    exported = load(root / "manifest.json")["documents"]
    entries = {entry["file_id"]: entry for entry in exported}
    outcomes = [
        json.loads(line) for line in (root / "outcomes.jsonl").read_text().splitlines()
    ]
    by_id = {outcome["file_id"]: outcome for outcome in outcomes}
    if len(by_id) != len(outcomes) or set(by_id) != set(entries):
        raise ValueError("Export must account for every input exactly once")
    expected = [
        e for e in selected["documents"] if split == "all" or e["split"] == split
    ]
    for entry in expected:
        actual = entries.get(entry["file_id"])
        if actual is None or actual.get("source_sha256") != entry["sha256"]:
            raise ValueError(
                "Missing export input or source hash mismatch: " + entry["file_id"]
            )
    ocr_id = run_id + "-ocr"
    model = config["jev_model" if config["interpreter"] == "jev" else "deepseek_model"]
    provenance = {
        "export_config_sha256": file_hash(root / "config.json"),
        "export_outcomes_sha256": file_hash(root / "outcomes.jsonl"),
        "interpreter": config["interpreter"],
        "prompt_version": config.get("jev_adapter_version", config["version"])
        if config["interpreter"] == "jev"
        else config["version"],
        "measurement": "Actual product outputs; needs_review remains scorable; failures stay in denominator",
        "latency_scope": "OCR page operation latencies; interpretation stage latency. Not end-to-end wall clock or billing totals.",
    }
    for rid, track, model_id, upstream in (
        (ocr_id, "ocr", config["fal_model"], None),
        (run_id, "end_to_end", model, ocr_id),
    ):
        create_run(
            mp,
            rid,
            track,
            "import",
            model_id,
            split=split,
            reading_run=upstream,
            execution_metadata={
                **provenance,
                "prompt_version": config["version"]
                if track == "ocr"
                else provenance["prompt_version"],
            },
        )
    staging = run_path(mp.parent, run_id) / "product-import"
    staging.mkdir(parents=True, exist_ok=True)

    def artifact(outcome, name):
        relative = outcome["artifacts"].get(name)
        if not relative:
            return None
        path = (root / relative).resolve()
        if (
            not path.is_relative_to(root)
            or file_hash(path) != outcome["artifact_hashes"][name]
        ):
            raise ValueError("Export artifact hash/path mismatch")
        return path

    def failure(rid, entry, reason):
        path = run_path(mp.parent, rid) / key(entry["file_id"]) / "record.json"
        record = load(path)
        if record["status"] == "success":
            raise ValueError("Cannot replace a successful import")
        record.update(status="failed", error=reason)
        dump(path, validate("record", record))

    for entry in expected:
        fid = entry["file_id"]
        outcome = by_id[fid]
        folder = staging / key(fid)
        reading = artifact(outcome, "reading")
        invoice = artifact(outcome, "invoice")
        raw_path = artifact(outcome, "raw")
        raw = load(raw_path) if raw_path else {}
        stem = hashlib.sha256(fid.encode()).hexdigest()
        if reading:
            pages = raw.get("ocr", [])
            ocr_meta = {
                "source_sha256": entry["sha256"],
                "latency_seconds": sum(p.get("latency_seconds", 0) for p in pages)
                if pages
                else None,
                "usage": {},
                "cost_usd": None,
                "attempts": sum(p.get("attempts", 0) for p in pages),
                "resolved_model": None,
            }
            dump(folder / "ocr-metadata.json", ocr_meta)
            dump(folder / "ocr-response.json", pages)
            request = root / "benchmark-import" / stem / "ocr-request.json"
            if not request.exists():
                raise ValueError("Export OCR request evidence first: " + str(request))
            import_output(
                mp,
                ocr_id,
                fid,
                reading,
                request,
                folder / "ocr-response.json",
                folder / "ocr-metadata.json",
            )
        else:
            failure(
                ocr_id,
                entry,
                "Product reading unavailable: "
                + str((outcome.get("error") or {}).get("code", outcome["status"])),
            )
        if invoice and reading:
            metadata = load(root / "benchmark-import" / stem / "metadata.json")
            dump(folder / "invoice-metadata.json", metadata)
            import_output(
                mp,
                run_id,
                fid,
                invoice,
                root / "benchmark-import" / stem / "request.json",
                root / "benchmark-import" / stem / "response.json",
                folder / "invoice-metadata.json",
            )
        else:
            failure(
                run_id,
                entry,
                "Product invoice unavailable: "
                + str((outcome.get("error") or {}).get("code", outcome["status"])),
            )
    reports = {}
    for rid in (ocr_id, run_id):
        report = score_run(mp, rid, mp.parent / "reports" / (rid + ".json"))
        reports[rid] = {
            "documents": report["selected_documents"],
            "statuses": report["all_documents_operational"]["statuses"],
        }
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="benchmark/manifest.json")
    parser.add_argument("--export", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--split", choices=["all", "development", "held_out"], default="all"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            import_product(args.manifest, args.export, args.run_id, split=args.split)
        )
    )


if __name__ == "__main__":
    main()
