import hashlib

import pytest

from benchmark.core import dump, key, load, paths
from benchmark.import_product import import_product
from ingestion.export import export_bundle

pytest_plugins = ["benchmark.tests.test_operations"]


def product_export(mp, entry, *, failed=False):
    root = mp.parent / "product-export"
    refs = paths(mp.parent, entry["file_id"])
    reading, invoice = load(refs["stage2"]), load(refs["stage3"])
    outcome = {
        "file_id": entry["file_id"],
        "status": "failed" if failed else "needs_review",
        "error": {"code": "synthetic_failure"} if failed else None,
    }
    if not failed:
        outcome.update(
            reading=reading,
            invoice=invoice,
            latency_seconds=1.5,
            usage={"input_tokens": 10},
            attempts=1,
            cost_usd=None,
            raw={
                "ocr": [{"text": "Invoice 01", "latency_seconds": 2, "attempts": 1}],
                "interpretation": {
                    "request": {"state": reading},
                    "response": {"synthetic": True},
                },
            },
        )
    export_bundle(
        root,
        [
            {
                "file_id": entry["file_id"],
                "relative_path": entry["path"],
                "source_sha256": entry["sha256"],
            }
        ],
        {
            "version": "alpha-1",
            "interpreter": "jev",
            "jev_adapter_version": "jev-choice-0.2",
            "jev_model": "fixture-jev",
            "fal_model": "fixture-ocr",
        },
        [outcome],
    )
    stem = hashlib.sha256(entry["file_id"].encode()).hexdigest()
    dump(root / "benchmark-import" / stem / "ocr-request.json", {"synthetic": True})
    return root


def test_actual_product_output_scores_even_when_needs_review(workspace, monkeypatch):
    mp, entry = workspace
    root = product_export(mp, entry)

    def no_network(*args, **kwargs):
        raise AssertionError("Offline import must not invoke any model")

    monkeypatch.setattr("benchmark.adapters.Caller.post", no_network)
    result = import_product(mp, root, "product")
    assert result["product"]["statuses"] == {"success": 1}
    report = load(mp.parent / "reports/product.json")
    assert report["run"]["prompt_version"] == "jev-choice-0.2"
    assert report["reviewed_coverage"] == 0
    assert report["provisional"]["macro_present_value_recall"] == 1
    assert report["provisional"]["operations"]["total_cost_usd"] is None
    assert load(
        mp.parent / "runs/product" / key(entry["file_id"]) / "input-reading.json"
    ) == load(paths(mp.parent, entry["file_id"])["stage2"])


def test_product_failure_stays_in_ocr_and_invoice_denominators(workspace):
    mp, entry = workspace
    result = import_product(
        mp, product_export(mp, entry, failed=True), "failed-product"
    )
    assert all(
        value["documents"] == 1 and value["statuses"] == {"failed": 1}
        for value in result.values()
    )
    report = load(mp.parent / "reports/failed-product.json")
    assert report["provisional"]["macro_present_value_recall"] == 0


def test_rejects_changed_export_before_awarding_credit(workspace):
    mp, entry = workspace
    root = product_export(mp, entry)
    outcome = load(root / "outcomes.jsonl")
    path = root / outcome["artifacts"]["invoice"]
    invoice = load(path)
    invoice["invoice_number"] = "changed"
    dump(path, invoice)
    with pytest.raises(ValueError, match="hash/path mismatch"):
        import_product(mp, root, "tampered-product")
