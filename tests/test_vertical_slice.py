"""Exercise production render/adapters/pipeline against local SQL and mocked HTTP."""

import json
from pathlib import Path

import httpx
import pytest

from ingestion.contracts import Contracts
from ingestion.manifest import discover
from ingestion.pipeline import Pipeline
from tests.test_runtime import MemoryStorage


@pytest.mark.asyncio
async def test_real_pdf_to_provider_adapters_to_persisted_export(
    db, tmp_path, monkeypatch
):
    from ingestion.export import export_bundle

    contracts = Contracts("benchmark/schemas")
    source = Path("facturas/2026-01-08_P001.pdf").read_bytes()
    folder = tmp_path / "inputs"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(source)
    (folder / "b.pdf").write_bytes(source)
    items = discover(str(folder))
    config = {
        "version": "vertical-test",
        "interpreter": "deepseek",
        "dpi": 200,
        "concurrency": 2,
        "timeout": 10,
        "max_attempts": 3,
        "fal_model": "fal-ai/got-ocr/v2",
        "fal_base_url": "https://queue.fal.run",
        "helmcode_base_url": "https://api.helmcode.com/v1",
        "deepseek_model": "explicit-deepseek",
        "max_output_tokens": 8192,
        "max_input_chars": 100000,
        "schema_hashes": contracts.hashes,
        "schemas": contracts.schemas,
    }
    invoice = contracts.blank_invoice("ignored")
    invoice.update(document_type="invoice", invoice_number="0007", currency="EUR")
    invoice["totals"]["total"] = "12,30"
    evidence = {
        p: [{"page": 1, "reference_ids": ["p1-b1"]}]
        for p in ("/document_type", "/invoice_number", "/currency", "/totals/total")
    }
    calls = []

    def handle(request):
        calls.append(request.url.host)
        body = json.loads(request.content) if request.content else {}
        if request.url.host == "queue.fal.run":
            assert body["input_image_urls"][0].startswith("data:image/png;base64,")
            return httpx.Response(
                200, json={"outputs": ["Factura 0007\nTotal 12,30 EUR"]}
            )
        assert body["model"] == "explicit-deepseek"
        assert "a.pdf" not in json.dumps(body) and "b.pdf" not in json.dumps(body)
        return httpx.Response(
            200,
            json={
                "model": "explicit-deepseek",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {"invoice": invoice, "evidence": evidence}
                            )
                        },
                    }
                ],
            },
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.MockTransport(handle), **kw),
    )
    storage = MemoryStorage()
    batch = db.create_batch(items, config)
    runner = Pipeline(
        db,
        storage,
        contracts,
        config,
        {"FAL_KEY": "fixture", "HELMCODE_API_KEY": "fixture"},
    )
    result = await runner.run(batch)
    assert result["counts"]["needs_review"] == 2, db.results(batch["id"])
    assert len(calls) == 4
    results = [runner.load_artifact(r["artifact_id"]) for r in db.results(batch["id"])]
    assert {r["file_id"] for r in results} == {"a.pdf", "b.pdf"}
    assert all(r["invoice"]["totals"]["total"] == "12.3" for r in results)
    assert all(r["invoice"]["lines"] == [] for r in results)
    export_bundle(tmp_path / "export", items, config, results, contracts.schemas)
    await runner.run(batch)
    assert len(calls) == 4
    for job in db.list_jobs(batch["id"]):
        attempts = db.list_attempts(job["id"])
        assert len(attempts) == 1
        assert attempts[0]["raw_artifact_ids"]
        assert attempts[0]["latency_seconds"] is not None
