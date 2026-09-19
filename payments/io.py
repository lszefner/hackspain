from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ingestion.contracts import Contracts, canonical_bytes, digest

from .engine import DecisionEngine
from .master import exportable_master, load_master, restore_master

_SHA256 = re.compile(r"[0-9a-f]{64}")


def _read_json(path: Path):
    return json.loads(path.read_bytes())


def _placeholder(file_id: str, status: str, reason: str,
                 source_sha256: str | None = None) -> dict:
    return {
        "file_id": file_id,
        "status": status,
        "invoice": None,
        "source_sha256": source_sha256,
        "error": {"code": "input"},
        "checks": None,
        "input_problem": reason,
        "artifacts": None,
        "artifact_hashes": None,
    }


def _inside(root: Path, rel: str) -> Path | None:
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _valid_hash(value) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def _file_ids(rows, what: str) -> list[str]:
    ids = []
    for row in rows:
        fid = row.get("file_id") if isinstance(row, dict) else None
        if not isinstance(fid, str) or not fid:
            raise ValueError(f"{what} contains an entry without a valid file_id")
        ids.append(fid)
    return ids


def _load_artifact(root: Path, rel, expected_hash):
    if not isinstance(rel, str) or not rel:
        return None, "artifact path is not a nonempty string"
    if not _valid_hash(expected_hash):
        return None, f"artifact {rel} lacks a valid SHA-256 hash"
    path = _inside(root, rel)
    if path is None:
        return None, f"artifact path escapes export root: {rel}"
    if not path.is_file():
        return None, f"artifact missing: {rel}"
    try:
        data = path.read_bytes()
    except OSError:
        return None, f"artifact unreadable: {rel}"
    if digest(data) != expected_hash:
        return None, f"artifact hash mismatch: {rel}"
    try:
        return json.loads(data), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, f"artifact is not valid JSON: {rel}"


def _load_export(export_dir: Path):
    root = export_dir.resolve()
    try:
        manifest = _read_json(root / "manifest.json")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("export manifest.json is missing or malformed") from exc
    if not isinstance(manifest, dict) or not isinstance(
        manifest.get("documents"), list
    ):
        raise TypeError("export manifest.json must contain a documents list")
    documents = manifest["documents"]
    ids = _file_ids(documents, "manifest")
    if len(ids) != len(set(ids)):
        raise ValueError("export manifest has duplicate file_ids")
    manifest_by_id = {d["file_id"]: d for d in documents}

    try:
        outcomes = [
            json.loads(line)
            for line in (root / "outcomes.jsonl").read_text().splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("export outcomes.jsonl is missing or malformed") from exc
    outcome_ids = _file_ids(outcomes, "outcomes")
    if len(outcome_ids) != len(set(outcome_ids)):
        raise ValueError("export outcomes contain duplicate file_ids")
    unknown = set(outcome_ids) - set(ids)
    if unknown:
        raise ValueError(f"export outcomes reference unknown file_ids: {sorted(unknown)}")
    outcome_by_id = {o["file_id"]: o for o in outcomes}

    config = {}
    config_path = root / "config.json"
    if config_path.exists():
        try:
            config = _read_json(config_path)
        except (json.JSONDecodeError, UnicodeDecodeError):
            config = {}

    records = []
    for fid in sorted(ids):
        outcome = outcome_by_id.get(fid)
        manifest_hash = manifest_by_id[fid].get("source_sha256")
        if outcome is None:
            records.append(_placeholder(fid, "failed", "missing outcome row",
                                        manifest_hash))
            continue
        envelope_hash = outcome.get("source_sha256")
        source_sha256 = manifest_hash or envelope_hash
        if manifest_hash and envelope_hash and manifest_hash != envelope_hash:
            records.append(_placeholder(
                fid, outcome.get("status") or "failed",
                "manifest and envelope source_sha256 disagree", manifest_hash,
            ))
            continue
        record = {
            "file_id": fid,
            "status": outcome.get("status") or "failed",
            "invoice": None,
            "source_sha256": source_sha256,
            "error": outcome.get("error"),
            "checks": None,
            "artifacts": outcome.get("artifacts"),
            "artifact_hashes": outcome.get("artifact_hashes"),
        }
        artifacts = outcome.get("artifacts")
        hashes = outcome.get("artifact_hashes")
        problem = None
        if record["status"] == "completed":
            if not isinstance(artifacts, dict) or not isinstance(hashes, dict):
                problem = "completed outcome lacks artifact/hash mappings"
            else:
                for name in ("invoice", "checks"):
                    if artifacts.get(name) is None or hashes.get(name) is None:
                        problem = f"completed outcome lacks {name} artifact"
                        break
        if problem is None and isinstance(artifacts, dict) and isinstance(hashes, dict):
            for name in ("invoice", "checks"):
                if artifacts.get(name) is not None:
                    value, problem = _load_artifact(
                        root, artifacts[name], hashes.get(name)
                    )
                    if problem:
                        break
                    if name == "invoice":
                        if not isinstance(value, dict) or value.get("file_id") != fid:
                            problem = "invoice artifact file_id mismatch"
                            break
                        record["invoice"] = value
                    else:
                        record["checks"] = value
        if problem is None and record["status"] == "completed":
            checks = record["checks"]
            if not isinstance(checks, dict) or not checks or not all(
                isinstance(v, bool) for v in checks.values()
            ):
                problem = "exported checks artifact is not a nonempty bool map"
        if problem:
            record["status"] = "failed"
            record["invoice"] = None
            record["error"] = {"code": "artifact"}
            record["input_problem"] = problem
        records.append(record)
    return records, config, root


def _load_raw(invoices_dir: Path, input_dir: Path | None):
    if not invoices_dir.is_dir():
        raise ValueError(f"invoices directory not found: {invoices_dir}")
    root = invoices_dir.resolve()
    pdf_index: dict[str, list[str]] = {}
    pdf_paths: dict[str, Path] = {}
    inventory = set()
    if input_dir is not None:
        base = Path(input_dir)
        if not base.is_dir():
            raise ValueError(f"input directory not found: {input_dir}")
        base = base.resolve()
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix.lower() == ".pdf":
                rel = path.relative_to(base).as_posix()
                inventory.add(rel)
                pdf_index.setdefault(rel[: -len(path.suffix)], []).append(rel)
                pdf_paths[rel] = path
        ambiguous = {s: v for s, v in pdf_index.items() if len(v) > 1}
        if ambiguous:
            raise ValueError(
                "input inventory contains case-ambiguous PDFs for the same "
                f"path stem: {sorted(ambiguous)}"
            )

    records = {}
    unmatched = []
    for path in sorted(root.rglob("*.invoice.json")):
        rel = path.relative_to(root).as_posix()
        stem = rel[: -len(".invoice.json")]
        expected_id = stem + ".pdf"
        candidates = pdf_index.get(stem, [])
        if input_dir is not None:
            if not candidates:
                unmatched.append(expected_id)
                continue
            expected_id = candidates[0]
        try:
            invoice = json.loads(path.read_bytes())
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            records[expected_id] = _placeholder(
                expected_id, "imported", "invoice JSON is unparseable"
            )
            continue
        fid = invoice.get("file_id") if isinstance(invoice, dict) else None
        if fid != expected_id:
            records[expected_id] = _placeholder(
                expected_id, "imported",
                f"invoice file_id {fid!r} does not match filename",
            )
            continue
        source_sha256 = None
        pdf_path = pdf_paths.get(expected_id)
        if pdf_path is not None:
            try:
                source_sha256 = digest(pdf_path.read_bytes())
            except OSError:
                source_sha256 = None
        records[expected_id] = {
            "file_id": expected_id,
            "status": "imported",
            "invoice": invoice,
            "source_sha256": source_sha256,
            "error": None,
            "checks": None,
            "artifacts": None,
            "artifact_hashes": None,
        }

    if input_dir is not None:
        if unmatched:
            raise ValueError(
                f"invoice JSONs without a source PDF in the input inventory: "
                f"{sorted(unmatched)}"
            )
        for rel in sorted(inventory):
            if rel not in records:
                source_sha256 = None
                try:
                    source_sha256 = digest(pdf_paths[rel].read_bytes())
                except OSError:
                    source_sha256 = None
                records[rel] = _placeholder(
                    rel, "imported", "source PDF has no invoice JSON",
                    source_sha256,
                )
    return [records[fid] for fid in sorted(records)], {}, root


def _write_bytes_exclusive(path: Path, data: bytes):
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(
                "decision output contains different artifacts; choose a new directory"
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def _safe_record(record: dict) -> dict:
    error = record.get("error")
    return {
        "file_id": record.get("file_id"),
        "status": record.get("status"),
        "invoice": record.get("invoice"),
        "source_sha256": record.get("source_sha256"),
        "checks": record.get("checks"),
        "error": None if not error else {
            "code": error.get("code") if isinstance(error, dict)
            else "extraction_error"
        },
        "input_problem": record.get("input_problem"),
        "artifacts": record.get("artifacts"),
        "artifact_hashes": record.get("artifact_hashes"),
    }


def _counts(results) -> dict:
    return {
        result: sum(row["result"] == result for row in results)
        for result in ("PAGAR", "NO PAGAR", "ESCALAR")
    }


def _outcomes_bytes(results) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
        for row in results
    ).encode("utf-8")


def _write_bundle(out_root: Path, payloads: dict[Path, dict],
                  outcomes: bytes) -> None:
    rendered = {p: canonical_bytes(v) for p, v in payloads.items()}
    rendered[out_root / "outcomes.jsonl"] = outcomes
    for path, data in rendered.items():
        if path.exists() and path.read_bytes() != data:
            raise ValueError(
                "decision output contains different artifacts; "
                "choose a new directory"
            )
    for path, data in rendered.items():
        _write_bytes_exclusive(path, data)


def decide_files(*, rules: str | Path, sources: str | Path, as_of: str,
                 output: str | Path, export_dir: str | Path | None = None,
                 invoices_dir: str | Path | None = None,
                 input_dir: str | Path | None = None,
                 erp_snapshot: str | Path | None = None,
                 schema_dir: str | Path | None = None) -> dict:
    if (export_dir is None) == (invoices_dir is None):
        raise ValueError("exactly one of export_dir or invoices_dir is required")
    if input_dir is not None and export_dir is not None:
        raise ValueError("--input-dir is only valid together with --invoices")

    ruleset = _read_json(Path(rules))
    snapshot = _read_json(Path(erp_snapshot)) if erp_snapshot else None
    master = load_master(sources, snapshot, as_of=as_of)

    out_root = Path(output)
    inputs = [Path(p).resolve() for p in (export_dir, invoices_dir, input_dir) if p]
    if out_root.resolve() in inputs:
        raise ValueError("decision output must not overlap its inputs")
    for base in inputs:
        if out_root.resolve().is_relative_to(base):
            raise ValueError("decision output must not live inside an input directory")

    if export_dir is not None:
        records, config, input_root = _load_export(Path(export_dir))
        exported_schemas = Path(export_dir) / "schemas"
        if schema_dir is not None:
            contracts_dir = schema_dir
        elif exported_schemas.is_dir():
            contracts_dir = exported_schemas
        else:
            contracts_dir = None
    else:
        records, config, input_root = _load_raw(
            Path(invoices_dir), Path(input_dir) if input_dir else None
        )
        contracts_dir = schema_dir

    engine = DecisionEngine(ruleset, master, schema_dir=contracts_dir)

    if export_dir is not None and schema_dir is None:
        current = Contracts(None)
        for record in records:
            if record.get("invoice") is not None:
                try:
                    current.validate("invoice", record["invoice"])
                except (ValueError, KeyError):
                    record["invoice"] = None
                    record["status"] = "failed"
                    record["error"] = {"code": "schema"}
                    record["input_problem"] = (
                        "invoice does not satisfy the current invoice schema"
                    )

    results = engine.decide(records)
    outcomes = _outcomes_bytes(results)
    out_root.mkdir(parents=True, exist_ok=True)

    artifact_hashes = {}
    rendered_inputs = canonical_bytes([_safe_record(r) for r in records])
    rendered_rules = canonical_bytes(ruleset)
    rendered_master = canonical_bytes(exportable_master(engine.master))
    schema_hashes = {
        name: digest(canonical_bytes(schema))
        for name, schema in engine.contracts.schemas.items()
    }
    artifact_hashes["rules"] = digest(rendered_rules)
    artifact_hashes["master"] = digest(rendered_master)
    artifact_hashes["inputs"] = digest(rendered_inputs)
    artifact_hashes["schemas"] = schema_hashes

    decision_config = {
        "as_of": as_of,
        "rules": str(rules),
        "sources": str(sources),
        "export_dir": str(export_dir) if export_dir else None,
        "invoices_dir": str(invoices_dir) if invoices_dir else None,
        "input_dir": str(input_dir) if input_dir else None,
        "erp_snapshot": str(erp_snapshot) if erp_snapshot else None,
        "ruleset_sha256": engine.ruleset_sha256,
        "master_sha256": engine.master_sha256,
        "input_root": str(input_root),
        "ingest_config": config or None,
        "artifact_hashes": artifact_hashes,
    }

    summary = {
        "output": str(out_root),
        "inputs": len(results),
        "counts": _counts(results),
        "results": results,
        "ruleset_sha256": engine.ruleset_sha256,
        "master_sha256": engine.master_sha256,
        "as_of": as_of,
    }

    payloads = {
        out_root / "rules.json": ruleset,
        out_root / "master.json": exportable_master(engine.master),
        out_root / "decision-config.json": decision_config,
        out_root / "summary.json": {
            k: v for k, v in summary.items() if k != "results"
        },
    }
    rendered = {p: canonical_bytes(v) for p, v in payloads.items()}
    rendered[out_root / "inputs.json"] = rendered_inputs
    for name, schema in engine.contracts.schemas.items():
        rendered[out_root / "schemas" / f"{name}.json"] = canonical_bytes(schema)
    rendered[out_root / "outcomes.jsonl"] = outcomes
    for path, data in rendered.items():
        if path.exists() and path.read_bytes() != data:
            raise ValueError(
                "decision output contains different artifacts; "
                "choose a new directory"
            )
    for path, data in rendered.items():
        _write_bytes_exclusive(path, data)
    return summary


def _confined(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"bundle path escapes root: {name}") from exc
    return path


def replay_decisions(bundle: str | Path, output: str | Path) -> dict:
    root = Path(bundle).resolve()
    out_root = Path(output).resolve()
    if out_root == root or out_root.is_relative_to(root):
        raise ValueError("replay output must not live inside the source bundle")
    try:
        config = _read_json(root / "decision-config.json")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("bundle decision-config.json is missing or malformed") from exc
    hashes = config.get("artifact_hashes")
    if not isinstance(hashes, dict):
        raise TypeError("bundle decision-config lacks artifact_hashes")

    required = {"rules": "rules.json", "master": "master.json",
                "inputs": "inputs.json"}
    loaded = {}
    for key, name in required.items():
        expected = hashes.get(key)
        if not _valid_hash(expected):
            raise ValueError(f"bundle lacks a valid {key} artifact hash")
        path = _confined(root, name)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"bundle {name} is missing or unreadable") from exc
        if digest(data) != expected:
            raise ValueError(f"bundle {name} does not match its recorded hash")
        loaded[key] = json.loads(data)

    schema_hashes = hashes.get("schemas")
    if not isinstance(schema_hashes, dict) or not schema_hashes:
        raise ValueError("bundle lacks per-schema artifact hashes")
    if "invoice" not in schema_hashes:
        raise ValueError("bundle lacks the invoice schema artifact hash")
    schemas_root = _confined(root, "schemas")
    if not schemas_root.is_dir():
        raise ValueError("bundle schemas directory is missing")
    actual_names = {
        p.name[: -len(".json")] for p in schemas_root.glob("*.json")
        if p.is_file()
    }
    if actual_names != set(schema_hashes):
        raise ValueError(
            "bundle schemas do not match the recorded schema hashes"
        )
    for name, expected in schema_hashes.items():
        if not _valid_hash(expected) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError(f"bundle schema entry {name!r} is invalid")
        path = _confined(schemas_root, f"{name}.json")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"bundle schema {name}.json is missing") from exc
        if digest(data) != expected:
            raise ValueError(f"bundle schema {name}.json does not match its hash")

    master = restore_master(loaded["master"])
    if not isinstance(loaded["rules"], dict) or not isinstance(loaded["inputs"], list):
        raise TypeError("bundle rules or inputs are malformed")
    engine = DecisionEngine(loaded["rules"], master, schema_dir=root / "schemas")
    results = engine.decide(loaded["inputs"])
    outcomes = _outcomes_bytes(results)

    original = root / "outcomes.jsonl"
    outcomes_equal = original.exists() and original.read_bytes() == outcomes

    out_root.mkdir(parents=True, exist_ok=True)
    summary = {
        "output": str(out_root),
        "inputs": len(results),
        "counts": _counts(results),
        "ruleset_sha256": engine.ruleset_sha256,
        "master_sha256": engine.master_sha256,
        "as_of": engine.as_of,
        "outcomes_equal_source": outcomes_equal,
    }
    _write_bundle(out_root, {out_root / "summary.json": summary}, outcomes)
    return summary
