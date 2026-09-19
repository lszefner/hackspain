"""Runs the real ingestion pipeline (Helmcode OCR + DeepSeek interpretation)
against selected facturas/*.pdf, using LocalRepository/LocalStorage instead of
Supabase (network here only allows outbound HTTPS), then evaluates the result
with the real rules_ingestion business checks (VENDOR/DUPLICATES/AMOUNT/
AUTHORIZATION/DATES/MISSING) and persists it for the UI.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from ingestion.config import settings  # noqa: E402
from ingestion.contracts import Contracts  # noqa: E402
from ingestion.pipeline import Pipeline  # noqa: E402
from rules_ingestion.checks import run_checks  # noqa: E402
from rules_ingestion.invoice import normalize_invoice as rules_normalize_invoice  # noqa: E402
from rules_ingestion.store import load_store  # noqa: E402

from backend.local_backend import LocalRepository, LocalStorage  # noqa: E402
from backend.map_invoice import to_raw_invoice  # noqa: E402
from backend.master_data import build_master  # noqa: E402
from backend.results_store import ResultsStore  # noqa: E402

FACTURAS_DIR = ROOT / "facturas"
DATA_DIR = Path(__file__).resolve().parent / "data"
RULESET_PATH = ROOT / "rules_ingestion" / "outcome" / "v3" / "balanced" / "rules.json"
SOURCES_YAML = ROOT / "rules_ingestion" / "sources.yaml"


def _decision_from_checks(checks: list[dict]) -> str:
    verdicts = {c["verdict"] for c in checks}
    if "FAIL" in verdicts:
        return "NO_PAGAR"
    if "NEEDS_REVIEW" in verdicts:
        return "ESCALAR"
    return "PAGAR"


async def revisar_lote(file_ids: list[str], *, store: ResultsStore) -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    contracts = Contracts(str(ROOT / "benchmark" / "schemas"))
    config = settings(interpreter="deepseek", dpi=200, concurrency=3, ocr="helmcode-vision")
    config["schema_hashes"] = contracts.hashes
    secrets = {"HELMCODE_API_KEY": os.environ["HELMCODE_API_KEY"]}

    repo = LocalRepository()
    storage = LocalStorage(DATA_DIR / "objects")
    pipeline = Pipeline(repo, storage, contracts, config, secrets)

    manifest = [
        {"relative_path": file_id, "file_id": file_id,
         "local_path": str(FACTURAS_DIR / file_id),
         "source_sha256": None, "ordinal": i, "error": None}
        for i, file_id in enumerate(file_ids)
    ]
    import hashlib
    for item in manifest:
        item["source_sha256"] = hashlib.sha256(Path(item["local_path"]).read_bytes()).hexdigest()

    batch = repo.create_batch(manifest, config)
    for file_id in file_ids:
        store.set_procesando(file_id)

    ruleset = load_store(str(RULESET_PATH))
    master = build_master(str(SOURCES_YAML), store.seen_invoice_keys(), store.seen_amount_date())

    result = await pipeline.run(batch)

    for row in repo.results(batch["id"]):
        artifact = repo.get_artifact(row["artifact_id"]) if row.get("artifact_id") else None
        outcome = None
        if artifact:
            import json as _json
            outcome = _json.loads(storage.get(artifact["object_key"]))
        file_id = row["file_name"]
        if row["status"] not in ("completed", "needs_review") or not outcome or not outcome.get("invoice"):
            store.set_error(file_id, f"extraccion fallida: {row.get('error') or row['status']}")
            continue
        raw = to_raw_invoice(file_id, outcome["invoice"])
        inv = rules_normalize_invoice(raw)
        checks = run_checks(inv, master, ruleset)
        store.set_hecha(file_id, decision=_decision_from_checks(checks), raw_invoice=inv, checks=checks)

    return result


def revisar_lote_sync(file_ids: list[str], *, store: ResultsStore) -> dict:
    return asyncio.run(revisar_lote(file_ids, store=store))
