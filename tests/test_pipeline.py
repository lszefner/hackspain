"""Offline end-to-end and recovery tests, with a durable DB when configured."""

import json

import pytest

from ingestion.cli import offline_fixture
from ingestion.contracts import Contracts
from ingestion.export import export_bundle
from ingestion.manifest import discover


def test_fixture_exports_valid_payloads_and_missing_values(tmp_path):
    result = offline_fixture("benchmark/schemas", tmp_path)
    assert result["counts"]["needs_review"] == 1
    outcome = json.loads((tmp_path / "outcomes.jsonl").read_text())
    invoice = json.loads((tmp_path / outcome["artifacts"]["invoice"]).read_text())
    assert invoice["invoice_number"] == "0007"
    assert invoice["lines"] == []
    contracts = Contracts("benchmark/schemas")
    contracts.validate("invoice", invoice)
    contracts.validate("record", json.loads((tmp_path / "records.jsonl").read_text()))


def test_manifest_preserves_duplicate_bytes_but_rejects_duplicate_names(tmp_path):
    (tmp_path / "first.pdf").write_bytes(b"pdf")
    (tmp_path / "second.pdf").write_bytes(b"pdf")
    items = discover(str(tmp_path))
    assert len(items) == 2
    assert items[0]["source_sha256"] == items[1]["source_sha256"]
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "first.pdf").write_bytes(b"pdf")
    with pytest.raises(ValueError, match="Duplicate"):
        discover(str(tmp_path))


def test_missing_input_retained_and_failure_exported(tmp_path):
    manifest = tmp_path / "inputs.json"
    manifest.write_text(
        json.dumps({"documents": [{"file_id": "missing.pdf", "path": "missing.pdf"}]})
    )
    items = discover(manifest_path=str(manifest))
    assert items[0]["error"] == "source_unreadable"
    export_bundle(
        tmp_path / "export",
        items,
        {"interpreter": "jev"},
        [
            {
                "file_id": "missing.pdf",
                "status": "failed",
                "error": {"code": "source_unreadable"},
            }
        ],
    )
    outcome = json.loads((tmp_path / "export" / "outcomes.jsonl").read_text())
    assert outcome["artifacts"]["invoice"] is None


def test_export_cannot_drop_denominator(tmp_path):
    with pytest.raises(ValueError, match="each manifest"):
        export_bundle(
            tmp_path,
            [{"file_id": "a.pdf"}, {"file_id": "b.pdf"}],
            {"interpreter": "jev"},
            [],
        )
