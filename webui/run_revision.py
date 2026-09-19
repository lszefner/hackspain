"""Runs the real ingestion pipeline (Helmcode OCR + DeepSeek interpretation)
against selected facturas/*.pdf, using LocalRepository/LocalStorage instead of
Supabase (network here only allows outbound HTTPS), then evaluates the result
with the real rules_ingestion business checks (VENDOR/DUPLICATES/AMOUNT/
AUTHORIZATION/DATES/MISSING) and persists it for the UI.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.config import settings  # noqa: E402
from ingestion.contracts import Contracts  # noqa: E402
from ingestion.pipeline import Pipeline  # noqa: E402
from rules_ingestion.decision_context import (  # noqa: E402
    SourceSnapshot, _parse_ruleset, build_context,
    evaluate_context, project_legacy)
from rules_ingestion.decision_storage import (  # noqa: E402
    DecisionStore, create_decision_store)

from webui.local_backend import LocalRepository, LocalStorage  # noqa: E402
from webui.master_data import (  # noqa: E402
    ErpClient, capture_erp_snapshot, capture_master_snapshots)
from webui.results_store import ResultsStore  # noqa: E402

FACTURAS_DIR = ROOT / "facturas"
DATA_DIR = Path(__file__).resolve().parent / "data"
RULESET_PATH = ROOT / "rules_ingestion" / "outcome" / "v3" / "balanced" / "rules.json"
SOURCES_YAML = ROOT / "rules_ingestion" / "sources.yaml"
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def review_outcome(outcome: dict, *, snapshots: dict[str, SourceSnapshot],
                   ruleset: bytes | dict, evaluation_date: str,
                   captured_at: str,
                   decision_store: DecisionStore) -> tuple:
    bundle = build_context(outcome, snapshots=snapshots, ruleset=ruleset,
                           evaluation_date=evaluation_date,
                           captured_at=captured_at)
    receipt = decision_store.save(bundle)
    evaluation = evaluate_context(bundle)
    return bundle, evaluation, receipt


def _resolve_evaluation_date(value: str | None) -> str:
    candidate = value or os.environ.get("REVISION_EVALUATION_DATE")
    if not candidate or not ISO_DATE.match(candidate):
        raise ValueError(
            "an explicit evaluation date (YYYY-MM-DD) is required via "
            "evaluation_date or REVISION_EVALUATION_DATE")
    try:
        from datetime import date
        date.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"invalid evaluation date {candidate!r}") from exc
    return candidate


def _validate_file_ids(file_ids: list[str]) -> list[str]:
    if not file_ids:
        raise ValueError("no file_ids selected for revision")
    root = FACTURAS_DIR.resolve()
    validated = []
    for file_id in file_ids:
        if not isinstance(file_id, str) or not file_id \
                or Path(file_id).name != file_id \
                or "/" in file_id or "\\" in file_id \
                or Path(file_id).is_absolute() \
                or not file_id.lower().endswith(".pdf"):
            raise ValueError(f"invalid file_id {file_id!r}")
        resolved = (FACTURAS_DIR / file_id).resolve()
        if root not in resolved.parents:
            raise ValueError(f"file_id escapes facturas directory: {file_id!r}")
        validated.append(file_id)
    if len(set(validated)) != len(validated):
        raise ValueError("duplicate file_ids in revision request")
    return validated


async def revisar_lote(file_ids: list[str], *, store: ResultsStore,
                       evaluation_date: str | None = None,
                       backend: str | None = None,
                       ruleset_path: str | None = None,
                       sources_yaml: str | None = None) -> dict:
    evaluation_date = _resolve_evaluation_date(evaluation_date)
    backend = backend or os.environ.get("REVISION_BACKEND") or "local"
    if backend not in ("local", "supabase"):
        raise ValueError(f"unknown REVISION_BACKEND {backend!r}")
    file_ids = _validate_file_ids(file_ids)
    ruleset_file = Path(
        ruleset_path or os.environ.get("REVISION_RULESET_PATH") or RULESET_PATH)
    if not ruleset_file.is_file():
        raise ValueError(f"ruleset file not found: {ruleset_file}")
    ruleset_bytes = ruleset_file.read_bytes()
    _parse_ruleset(ruleset_bytes)
    sources_file = Path(
        sources_yaml or os.environ.get("REVISION_SOURCES_PATH") or SOURCES_YAML)
    if not sources_file.is_file():
        raise ValueError(f"sources mapping not found: {sources_file}")

    DATA_DIR.mkdir(exist_ok=True)
    captured_at = datetime.now(UTC).isoformat()
    decision_store = create_decision_store(
        backend, local_root=DATA_DIR / "decision-contexts")
    try:
        if backend == "supabase":
            repo = decision_store.repository
            storage = decision_store.storage
        else:
            repo = LocalRepository()
            storage = LocalStorage(DATA_DIR / "objects")

        contracts = Contracts(str(ROOT / "benchmark" / "schemas"))
        config = settings(interpreter="deepseek", dpi=200, concurrency=3,
                          ocr="helmcode-vision")
        config["schema_hashes"] = contracts.hashes
        config["schemas"] = contracts.schemas
        secrets = {"HELMCODE_API_KEY": os.environ["HELMCODE_API_KEY"]}

        pipeline = Pipeline(repo, storage, contracts, config, secrets)

        manifest = [
            {"relative_path": file_id, "file_id": file_id,
             "local_path": str(FACTURAS_DIR / file_id),
             "source_sha256": None, "ordinal": i, "error": None}
            for i, file_id in enumerate(file_ids)
        ]
        import hashlib
        for item in manifest:
            item["source_sha256"] = hashlib.sha256(
                Path(item["local_path"]).read_bytes()).hexdigest()

        batch = repo.create_batch(manifest, config)
        for file_id in file_ids:
            store.set_procesando(file_id)

        master_snapshots = capture_master_snapshots(
            str(sources_file), captured_at=captured_at)
        erp_snapshot = capture_erp_snapshot(ErpClient(), captured_at=captured_at)

        result = await pipeline.run(batch)

        rows_by_file: dict[str, list] = {}
        for row in repo.results(batch["id"]):
            rows_by_file.setdefault(row["file_name"], []).append(row)
        revision_counts = {"stored": 0, "failed": 0}
        for file_id in file_ids:
            rows = rows_by_file.get(file_id, [])
            if len(rows) != 1:
                store.set_error(
                    file_id,
                    "sin resultado de extraccion" if not rows
                    else "duplicate result row")
                revision_counts["failed"] += 1
                continue
            row = rows[0]
            try:
                outcome = None
                if row.get("artifact_id"):
                    outcome = pipeline.load_artifact(row["artifact_id"])
                if row["status"] not in ("completed", "needs_review") \
                        or not isinstance(outcome, dict) \
                        or not outcome.get("invoice"):
                    store.set_error(
                        file_id,
                        f"extraccion fallida: {row.get('error') or row['status']}")
                    revision_counts["failed"] += 1
                    continue
                if outcome["invoice"].get("file_id") != file_id:
                    store.set_error(file_id, "outcome file_id mismatch")
                    revision_counts["failed"] += 1
                    continue
                snapshots = dict(master_snapshots)
                snapshots["erp"] = erp_snapshot
                snapshots["processed"] = store.processed_history_snapshot(
                    captured_at=captured_at, exclude_file_id=file_id)
                bundle, evaluation, receipt = review_outcome(
                    outcome, snapshots=snapshots, ruleset=ruleset_bytes,
                    evaluation_date=evaluation_date, captured_at=captured_at,
                    decision_store=decision_store)
                inv, _master = project_legacy(bundle.context)
                inv["file_id"] = file_id
                store.set_hecha(
                    file_id, decision=evaluation["decision"], raw_invoice=inv,
                    checks=evaluation["checks"], context=bundle.context,
                    receipt=receipt)
                revision_counts["stored"] += 1
            except Exception as exc:
                store.set_error(
                    file_id, f"revision fallida: {type(exc).__name__}")
                revision_counts["failed"] += 1
        if isinstance(result, dict):
            result["revision_counts"] = revision_counts
        else:
            result = {"pipeline": result, "revision_counts": revision_counts}
        return result
    finally:
        decision_store.close()


def revisar_lote_sync(file_ids: list[str], *, store: ResultsStore,
                      **kwargs) -> dict:
    return asyncio.run(revisar_lote(file_ids, store=store, **kwargs))
