from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from backend.local_backend import LocalRepository, LocalStorage
from backend.map_invoice import to_raw_invoice
from desk import engine, master, rules, state
from desk.erp import ErpClient
from ingestion.config import settings
from ingestion.contracts import Contracts
from ingestion.pipeline import Pipeline
from rules_ingestion.invoice import normalize_invoice

ROOT = Path(__file__).resolve().parent.parent


def _billing(value):
    usage = value.get("usage") or {}
    cost = value.get("cost_usd")
    if cost is None:
        cost = usage.get("cost_usd", usage.get("cost"))
    return {"cost_usd": float(cost or 0), "cost_known": cost is not None,
            "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
            "output_tokens": usage.get("completion_tokens", usage.get("output_tokens"))}


class DeskRepository(LocalRepository):
    def __init__(self, ledger):
        super().__init__()
        self.ledger = ledger
        self.started = {}

    def begin_attempt(self, job_id, claim_token, **kwargs):
        result = super().begin_attempt(job_id, claim_token, **kwargs)
        self.started[result["id"]] = time.monotonic()
        return result

    def fail(self, job_id, claim_token, error, **kwargs):
        result = super().fail(job_id, claim_token, error, **kwargs)
        job = self.get_job(job_id)
        attempt = self.list_attempts(job_id)[-1]
        entry = self.get_input(job["input_id"])
        elapsed = int((time.monotonic() - self.started.get(attempt["id"], time.monotonic())) * 1000)
        self.ledger.record("PROVIDER_FAILED", file_id=entry["file_name"], actor="pipeline",
                           provider=job["provider"], model=job["model"], attempts=attempt["attempt_number"],
                           error_code=error.get("code", "provider_error"), stage=job["stage"],
                           action="retry" if result["state"] == "retry_wait" else "held_for_review",
                           recovered=False, cost_known=False, ms=elapsed)
        return result


class DeskPipeline(Pipeline):
    def __init__(self, repo, storage, contracts, config, secrets, *, ledger):
        super().__init__(repo, storage, contracts, config, secrets)
        self.ledger = ledger

    async def job(self, batch, entry, stage, source_hash, provider, model, operation, extra=None):
        result = await super().job(batch, entry, stage, source_hash, provider, model, operation, extra)
        kind = "EXTRACTED" if stage == "reading" else "NORMALIZED"
        self.ledger.record(kind, file_id=entry["file_name"], actor="agent:extractor",
                           provider=provider, model=result.get("resolved_model", model), stage=stage,
                           attempts=result.get("attempts", 1), confidence=None,
                           pages=1 if stage == "reading" else None,
                           fields=result.get("invoice", {}), changes=[], evidence=result.get("evidence", {}),
                           page=(extra or {}).get("page"),
                           ms=int((result.get("latency_seconds") or 0) * 1000), **_billing(result))
        return result


def _display(inv, extracted, vendor, pedido):
    supplier = extracted.get("supplier") or {}
    result = dict(inv)
    for key in ("base", "iva", "total"):
        result[key] = float(inv[key]) if inv.get(key) is not None else 0.0
    result.update(vendor=(vendor or {}).get("razon_social") or supplier.get("name") or "Proveedor sin identificar",
                  vendor_id=(vendor or {}).get("id") or inv.get("nif") or "sin-identificar",
                  vendor_email=os.environ.get("DESK_EMAIL_TO", ""),
                  invoice_number=inv.get("invoice_number") or "sin numero",
                  date=inv.get("date") or "sin fecha",
                  due_date=extracted.get("due_date"),
                  pedido_importe=float(pedido["importe_total"]) if pedido and pedido.get("importe_total") is not None else None,
                  terms=(vendor or {}).get("condiciones"),
                  missing_values=[key for key in ("base", "iva", "total", "date", "invoice_number") if inv.get(key) is None])
    return engine.serial(result)


def record_outcome(led, file_id, outcome, providers, orders, erp_snapshot, *, source_sha256, today):
    if outcome.get("status") != "completed" or not outcome.get("invoice"):
        led.record("INGESTION_FAILED", file_id=file_id, actor="pipeline",
                   error_code=(outcome.get("error") or {}).get("code", "extraction_requires_review"),
                   status=outcome.get("status"), evidence_issues=outcome.get("issues", []))
        return False
    started = time.monotonic()
    extracted = outcome["invoice"]
    raw = to_raw_invoice(file_id, extracted)
    inv = normalize_invoice(raw)
    vendor = next((v for v in providers.values() if v.get("nif") == inv.get("nif")), None)
    inv["vendor_id"] = vendor["id"] if vendor else inv.get("nif")
    inv["currency"] = extracted.get("currency")
    order = orders.get(inv.get("pedido"))
    if inv.get("pedido") and not any(e.get("pedido") == inv["pedido"] for e in erp_snapshot["entries"]):
        led.record("INGESTION_FAILED", file_id=file_id, actor="agent:conciliador",
                   error_code="pedido_not_in_authoritative_erp", pedido=inv["pedido"])
        return False
    changes = [{"field": k, "from": engine.serial(raw.get(k)), "to": engine.serial(v)}
               for k, v in inv.items() if engine.serial(raw.get(k)) != engine.serial(v)]
    led.record("NORMALIZED", file_id=file_id, actor="pipeline", provider="local", model="rules_ingestion.invoice",
               attempts=1, changes=changes, fields=engine.serial(inv), cost_known=True,
               ms=int((time.monotonic() - started) * 1000))
    matches = [e for e in erp_snapshot["entries"] if inv.get("pedido") and e.get("pedido") == inv["pedido"]]
    erp_state = "PAGADA" if any(e.get("estado") == "PAGADA" for e in matches) else (
        "PENDIENTE" if matches and all(e.get("estado") == "PENDIENTE" for e in matches) else None)
    if inv.get("pedido") and erp_state is None:
        led.record("INGESTION_FAILED", file_id=file_id, actor="agent:conciliador", error_code="erp_state_unknown")
        return False
    led.record("ERP_RECONCILED", file_id=file_id, actor="agent:conciliador", provider="erp-2009", model=None,
               attempts=1, pedido=inv.get("pedido"), asiento=matches[0].get("id") if matches else None,
               erp_estado=erp_state, snapshot_seq=erp_snapshot.get("seq"), matches=matches,
               retries=len(erp_snapshot["retries"]), errors=[r["code"] for r in erp_snapshot["retries"]],
               retry_details=erp_snapshot["retries"], pages=erp_snapshot["pages"],
               shared_snapshot=True, cost_known=True, ms=0)
    with led.transaction():
        previous = [r for r in state.invoices(led) if r["file_id"] != file_id]
        hard = [r["file_id"] for r in previous if inv.get("invoice_number") and inv.get("vendor_id")
                and (r.get("evaluation") or {}).get("invoice", {}).get("invoice_number") == inv["invoice_number"]
                and (r.get("evaluation") or {}).get("invoice", {}).get("vendor_id") == inv["vendor_id"]]
        soft = [r["file_id"] for r in previous if inv.get("total") is not None and inv.get("date")
                and engine.decimals((r.get("evaluation") or {}).get("invoice", {})).get("total") == inv["total"]
                and (r.get("evaluation") or {}).get("invoice", {}).get("date") == inv["date"]]
        evidence = engine.snapshot(inv, vendor, order, erp_estado=erp_state, today=today,
                                   hard_matches=hard, soft_matches=soft)
        policy = rules.current(led)
        started = time.monotonic()
        decision, checks = engine.evaluate(evidence, policy)
        for check in checks:
            led.record("RULE_EVALUATED", file_id=file_id, actor="engine", ruleset_version=policy["version"], **check)
        old = led.last(file_id, "VERDICT")
        led.record("VERDICT", file_id=file_id, actor="engine", ruleset_version=policy["version"],
                   decision=decision, deterministic=True, evaluation=evidence, checks=checks,
                   invoice=_display(inv, extracted, vendor, order), source_sha256=source_sha256,
                   driven_by=[c["canonical"] for c in checks if c["effect"] != "PAGAR"],
                   reprocessed_from=old["payload"]["decision"] if old else None,
                   reason_for_reprocess="nueva ingesta" if old else None,
                   ms=int((time.monotonic() - started) * 1000))
    return True


async def _run(led, root, limit):
    providers, orders = master.load(root / "FINAL_v7_DEFINITIVO_ahorasi.xlsx")
    erp_snapshot = ErpClient().snapshot()
    erp_snapshot["seq"] = led.record("ERP_SNAPSHOT", actor="agent:conciliador", **erp_snapshot)
    contracts = Contracts(str(root / "benchmark" / "schemas"))
    config = settings(interpreter="deepseek", ocr="helmcode-vision")
    config["schema_hashes"] = contracts.hashes
    repo = DeskRepository(led)
    storage = LocalStorage(led.path.parent / "objects")
    pipeline = DeskPipeline(repo, storage, contracts, config,
                            {"HELMCODE_API_KEY": os.environ["HELMCODE_API_KEY"]}, ledger=led)
    paths = sorted((root / "facturas").glob("*.pdf"))
    if limit is not None:
        paths = paths[:limit]
    result = {"completed": 0, "failed": 0, "reused": 0, "total": len(paths)}
    for index, path in enumerate(paths):
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        previous = led.last(path.name, "VERDICT")
        if previous and previous["payload"].get("source_sha256") == sha:
            result["reused"] += 1
            continue
        manifest = [{"file_id": path.name, "relative_path": path.name, "local_path": str(path),
                     "source_sha256": sha, "ordinal": index, "error": None}]
        led.record("FILE_RECEIVED", file_id=path.name, actor="pipeline", source="facturas/",
                   sha256=sha, bytes=path.stat().st_size, pages=None)
        batch = repo.create_batch(manifest, config)
        await pipeline.run(batch)
        row = next(iter(repo.results(batch["id"])), {})
        artifact = repo.get_artifact(row["artifact_id"]) if row.get("artifact_id") else None
        outcome = json.loads(storage.get(artifact["object_key"])) if artifact else {}
        ok = record_outcome(led, path.name, outcome, providers, orders, erp_snapshot,
                            source_sha256=sha, today=datetime.now(UTC).date().isoformat())
        result["completed" if ok else "failed"] += 1
    return result


def run(led, *, root=ROOT, limit=None):
    led.record("INGESTION_STARTED", actor="pipeline", mode="real", limit=limit)
    missing = [k for k in ("HELMCODE_BASE_URL", "HELMCODE_API_KEY", "HELMCODE_DEEPSEEK_MODEL") if not os.environ.get(k)]
    if missing:
        led.record("INGESTION_FAILED", actor="pipeline", error_code="missing_configuration", missing=missing)
        return {"error": "missing_configuration", "missing": missing}
    from urllib.parse import urlsplit
    endpoint = urlsplit(os.environ["HELMCODE_BASE_URL"])
    if endpoint.scheme != "https" or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        led.record("INGESTION_FAILED", actor="pipeline", error_code="invalid_helmcode_endpoint")
        return {"error": "invalid_helmcode_endpoint"}
    try:
        result = asyncio.run(_run(led, Path(root), limit))
    except Exception as exc:  # noqa: BLE001 - any pipeline failure must land in the ledger
        led.record("INGESTION_FAILED", actor="pipeline", error_code=type(exc).__name__)
        return {"error": type(exc).__name__}
    led.record("INGESTION_FINISHED", actor="pipeline", **result)
    return result
