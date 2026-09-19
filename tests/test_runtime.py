from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ingestion.contracts import Contracts, blank_invoice, canonical_bytes, digest
from ingestion.pipeline import Pipeline, StageError
from ingestion.storage import StorageRef, sha256_bytes
from ingestion.validation import validate_interpretation

ROOT = Path(__file__).parents[1]
MIGRATION = next((ROOT / "supabase" / "migrations").glob("*.sql"))


class MemoryStorage:
    """Content-addressed storage that verifies immutable writes and reads."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(
        self, data: bytes, kind: str, content_type: str = "application/octet-stream"
    ) -> StorageRef:
        if not isinstance(data, bytes):
            raise TypeError("artifact data must be bytes")
        sha = sha256_bytes(data)
        key = f"memory/{sha}/{kind}"
        existing = self.objects.get(key)
        if existing is not None and existing != data:
            raise OSError("immutable artifact changed")
        self.objects[key] = data
        if sha256_bytes(self.objects[key]) != sha:
            raise OSError("artifact verification failed")
        return StorageRef(sha, key, len(data), content_type, kind)

    def get(self, object_key: str) -> bytes:
        return self.objects[object_key]


def _config(contracts: Contracts, *, timeout: float = 1.0) -> dict[str, Any]:
    return {
        "version": "runtime-test",
        "interpreter": "deepseek",
        "concurrency": 1,
        "timeout": timeout,
        "max_attempts": 1,
        "fal_model": "mock-ocr",
        "deepseek_model": "mock-deepseek",
        "provider_revision": "fixture",
        "schema_hashes": contracts.hashes,
        "schemas": contracts.schemas,
        "dpi": 72,
    }


def _reading(file_id: str) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "file_id": file_id,
        "capabilities": {"block_kinds": False, "tables": False, "layout": False},
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {
                        "id": "p1-b1",
                        "kind": "other",
                        "text": "Factura DUP",
                        "rows": [],
                        "uncertainties": [],
                    }
                ],
                "non_text_elements": [],
            },
            {
                "page": 2,
                "blocks": [
                    {
                        "id": "p2-b1",
                        "kind": "other",
                        "text": "Total 12,30 EUR",
                        "rows": [],
                        "uncertainties": [],
                    }
                ],
                "non_text_elements": [],
            },
        ],
    }


def _invoice(
    contracts: Contracts, file_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    invoice = blank_invoice(file_id, ROOT / "benchmark" / "schemas")
    invoice.update(document_type="invoice", invoice_number="DUP-1", currency="EUR")
    invoice["totals"]["total"] = "12.30"
    evidence = {
        "/document_type": [{"page": 1, "reference_ids": ["p1-b1"]}],
        "/invoice_number": [{"page": 1, "reference_ids": ["p1-b1"]}],
        "/currency": [{"page": 2, "reference_ids": ["p2-b1"]}],
        "/totals/total": [{"page": 2, "reference_ids": ["p2-b1"]}],
    }
    contracts.validate("invoice", invoice)
    return invoice, evidence


class MockPipeline(Pipeline):
    def __init__(self, *args: Any, callback: Callable[..., Any], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.provider_calls = 0
        self.callback = callback

    async def interpret(
        self, batch: dict[str, Any], entry: dict[str, Any], reading: dict[str, Any]
    ) -> dict[str, Any]:
        async def operation(attempt: dict[str, Any]) -> dict[str, Any]:
            self.provider_calls += 1
            result = await self.callback(attempt, entry, reading)
            native = self.artifact(result, "interpretation-native")
            self.repo.record_attempt_artifact(attempt["id"], native["id"])
            invoice = result["invoice"]
            self.contracts.validate("invoice", invoice)
            checks = validate_interpretation(
                invoice, result["evidence"], reading, self.contracts
            )
            return {**result, **checks, "error": None}

        return await self.job(
            batch,
            entry,
            "interpretation",
            digest(canonical_bytes(reading)),
            "deepseek",
            self.config["deepseek_model"],
            operation,
        )


def _items(source: bytes, *names: str) -> list[dict[str, Any]]:
    path = ROOT / "facturas" / "2026-01-25_P001.pdf"
    return [
        {
            "file_id": name,
            "relative_path": name,
            "local_path": str(path),
            "source_sha256": digest(source),
        }
        for name in names
    ]


@pytest.mark.asyncio
async def test_duplicate_content_multi_page_inputs_keep_separate_results_and_resume(
    db, postgres_dsn
):
    contracts = Contracts(ROOT / "benchmark" / "schemas")
    storage = MemoryStorage()
    source = (ROOT / "facturas" / "2026-01-25_P001.pdf").read_bytes()
    items = _items(source, "copy-a.pdf", "copy-b.pdf")
    config = _config(contracts)
    batch = db.create_batch(items, config)
    invoice_a, evidence_a = _invoice(contracts, "copy-a.pdf")
    invoice_b, evidence_b = _invoice(contracts, "copy-b.pdf")

    async def callback(*_: Any) -> dict[str, Any]:
        invoice, evidence = (
            (invoice_a, evidence_a)
            if mock.provider_calls == 0
            else (invoice_b, evidence_b)
        )
        return {"invoice": invoice.copy(), "evidence": evidence}

    mock = MockPipeline(db, storage, contracts, config, callback=callback)
    readings = {
        name: {"reading": _reading(name)} for name in ("copy-a.pdf", "copy-b.pdf")
    }
    first = await mock.run(batch, readings)
    assert first["counts"] == {"completed": 0, "needs_review": 2, "failed": 0}, [
        row["error"] for row in db.results(batch["id"])
    ]
    assert [row["file_name"] for row in db.results(batch["id"])] == [
        "copy-a.pdf",
        "copy-b.pdf",
    ]
    assert mock.provider_calls == 2

    second = await mock.run(batch, readings)
    assert second["counts"] == first["counts"]
    assert mock.provider_calls == 2
    assert (
        len(
            [
                job
                for job in db.list_jobs(batch["id"])
                if job["stage"] == "interpretation"
            ]
        )
        == 2
    )


@pytest.mark.asyncio
async def test_missing_input_gets_explicit_failed_outcome(db):
    contracts = Contracts(ROOT / "benchmark" / "schemas")
    config = _config(contracts)
    item = {
        "file_id": "missing.pdf",
        "relative_path": "missing.pdf",
        "error": "input_missing",
    }
    batch = db.create_batch([item], config)
    mock = MockPipeline(
        db, MemoryStorage(), contracts, config, callback=lambda *_: None
    )
    result = await mock.run(batch)
    outcome = db.results(batch["id"])[0]
    assert result["counts"]["failed"] == 1
    assert outcome["status"] == "failed"
    assert outcome["error"]["code"] == "input_missing"
    assert mock.provider_calls == 0


@pytest.mark.asyncio
async def test_invalid_provider_output_fails_explicitly_but_preserves_native_artifact(
    db,
):
    contracts = Contracts(ROOT / "benchmark" / "schemas")
    config = _config(contracts)
    source = (ROOT / "facturas" / "2026-01-25_P001.pdf").read_bytes()
    item = _items(source, "invalid.pdf")[0]
    batch = db.create_batch([item], config)

    async def callback(*_: Any) -> dict[str, Any]:
        return {
            "invoice": {"schema_version": "invalid", "file_id": "invalid.pdf"},
            "evidence": {},
        }

    mock = MockPipeline(db, MemoryStorage(), contracts, config, callback=callback)
    await mock.run(batch, {"invalid.pdf": {"reading": _reading("invalid.pdf")}})
    outcome = db.results(batch["id"])[0]
    assert outcome["status"] == "failed"
    assert outcome["error"]["code"] in {"ValueError", "invalid_response"}
    job = next(
        job for job in db.list_jobs(batch["id"]) if job["stage"] == "interpretation"
    )
    attempt = db.list_attempts(job["id"])[0]
    native_ids = attempt["raw_artifact_ids"]
    assert native_ids
    assert db.get_artifact(native_ids[0])["kind"] == "interpretation-native"


@pytest.mark.asyncio
async def test_unknown_timeout_is_not_resubmitted_on_resume(db):
    contracts = Contracts(ROOT / "benchmark" / "schemas")
    config = _config(contracts, timeout=0.01)
    source = (ROOT / "facturas" / "2026-01-25_P001.pdf").read_bytes()
    item = _items(source, "timeout.pdf")[0]
    batch = db.create_batch([item], config)

    async def callback(*_: Any) -> dict[str, Any]:
        await asyncio.sleep(0.1)
        return {}

    mock = MockPipeline(db, MemoryStorage(), contracts, config, callback=callback)
    readings = {"timeout.pdf": {"reading": _reading("timeout.pdf")}}
    await mock.run(batch, readings)
    assert (
        next(
            job for job in db.list_jobs(batch["id"]) if job["stage"] == "interpretation"
        )["state"]
        == "unknown"
    )
    assert mock.provider_calls == 1
    await mock.run(batch, readings)
    assert mock.provider_calls == 1


@pytest.mark.asyncio
async def test_artifact_reload_detects_immutable_storage_tampering(db):
    contracts = Contracts(ROOT / "benchmark" / "schemas")
    config = _config(contracts)
    source = (ROOT / "facturas" / "2026-01-25_P001.pdf").read_bytes()
    item = _items(source, "tamper.pdf")[0]
    batch = db.create_batch([item], config)
    invoice, evidence = _invoice(contracts, "tamper.pdf")

    async def callback(*_: Any) -> dict[str, Any]:
        return {"invoice": invoice.copy(), "evidence": evidence}

    storage = MemoryStorage()
    mock = MockPipeline(db, storage, contracts, config, callback=callback)
    await mock.run(batch, {"tamper.pdf": {"reading": _reading("tamper.pdf")}})
    result_artifact = db.results(batch["id"])[0]["artifact_id"]
    artifact = db.get_artifact(result_artifact)
    storage.objects[artifact["object_key"]] = b"tampered"
    with pytest.raises(StageError, match="artifact_hash_mismatch"):
        mock.load_artifact(result_artifact)
