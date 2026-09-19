"""Portable exports: immutable payload bytes and an outcome for every input."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def write_json(path: Path, value: object) -> str:
    from .contracts import canonical_bytes

    data = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != data:
        raise ValueError(
            "Export destination contains different artifacts; choose a new directory"
        )
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def export_bundle(
    output: str | Path,
    manifest: list[dict],
    config: dict,
    outcomes: list[dict],
    schemas: dict | None = None,
) -> dict:
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    ids = [entry["file_id"] for entry in manifest]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate file_id in export")
    by_id = {row["file_id"]: row for row in outcomes}
    if len(by_id) != len(outcomes) or set(by_id) != set(ids):
        raise ValueError(
            "Export outcomes must account for each manifest input exactly once"
        )
    write_json(root / "manifest.json", {"documents": manifest})
    write_json(root / "config.json", config)
    for name, schema in (schemas or {}).items():
        write_json(root / "schemas" / f"{name}.json", schema)
    envelopes, records = [], []
    for entry in manifest:
        original_result = by_id[entry["file_id"]]
        result = dict(original_result)
        paths, hashes = {}, {}
        stem = hashlib.sha256(entry["file_id"].encode()).hexdigest()
        for name in (
            "reading",
            "invoice",
            "evidence",
            "layout",
            "coverage",
            "checks",
            "warnings",
            "raw",
        ):
            value = result.pop(name, None)
            if value is not None:
                directory = {"reading": "stage2", "invoice": "stage3"}.get(name, name)
                path = Path(directory) / (stem + ".json")
                hashes[name] = write_json(root / path, value)
                paths[name] = path.as_posix()
            else:
                paths[name] = None
        result.update({"artifacts": paths, "artifact_hashes": hashes})
        envelopes.append(result)
        # Benchmark's record.json uses its own status vocabulary.
        record = {
            "schema_version": "0.1",
            "file_id": entry["file_id"],
            "source_sha256": entry.get("source_sha256"),
            "reading_sha256": hashes.get("reading"),
            "status": "success" if paths["invoice"] else "failed",
            "provider": config["interpreter"],
            "model": config.get(
                "deepseek_model"
                if config["interpreter"] == "deepseek"
                else "jev_model",
                "fixture",
            ),
            "resolved_model": result.get("resolved_model"),
            "prompt_version": config.get(
                "jev_adapter_version", config.get("version", "alpha-1")
            )
            if config["interpreter"] == "jev"
            else config.get("version", "alpha-1"),
            "latency_seconds": result.get("latency_seconds"),
            "usage": result.get("usage", {}),
            "cost_usd": result.get("cost_usd"),
            "attempts": result.get("attempts", 0),
            "error": result.get("error", {}).get("code")
            if result.get("error")
            else None,
            "artifact_sha256": hashes.get("invoice"),
            "imported": True,
        }
        # Unknown source bytes cannot honestly satisfy the benchmark hash contract.
        if record["source_sha256"] is not None:
            records.append(record)
        # The evaluator reserializes input-reading.json as indent=2 plus newline.
        # Keep that boundary hash separate from our canonical artifact-byte hash.
        metadata = {
            key: record[key]
            for key in (
                "source_sha256",
                "latency_seconds",
                "usage",
                "cost_usd",
                "attempts",
                "resolved_model",
            )
        }
        if original_result.get("reading") is not None:
            benchmark_reading_bytes = (
                json.dumps(
                    original_result["reading"],
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            ).encode()
            metadata["reading_sha256"] = hashlib.sha256(
                benchmark_reading_bytes
            ).hexdigest()
        write_json(root / "benchmark-import" / stem / "metadata.json", metadata)
        raw = original_result.get("raw", {})
        interpretation = raw.get("interpretation", {}) if isinstance(raw, dict) else {}
        if isinstance(interpretation, dict):
            for key in ("request", "response"):
                if key in interpretation:
                    write_json(
                        root / "benchmark-import" / stem / f"{key}.json",
                        interpretation[key],
                    )
    for name, rows in (("outcomes.jsonl", envelopes), ("records.jsonl", records)):
        data = "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
            for row in rows
        )
        target = root / name
        if target.exists() and target.read_text() != data:
            raise ValueError("Export destination differs; choose a new directory")
        target.write_text(data)
    return {
        "output": str(root),
        "inputs": len(envelopes),
        "counts": {
            status: sum(row["status"] == status for row in envelopes)
            for status in ("completed", "needs_review", "failed")
        },
    }
