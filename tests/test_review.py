from copy import deepcopy

import pytest

from ingestion.contracts import Contracts, canonical_bytes, digest
from ingestion.review import apply_reading_review


def example():
    reading = {
        "schema_version": "0.1",
        "file_id": "a.pdf",
        "capabilities": {"block_kinds": True, "tables": False, "layout": False},
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {
                        "id": "p1-fg-b1",
                        "kind": "heading",
                        "text": "Supplier OCR",
                        "rows": [],
                        "uncertainties": [],
                    }
                ],
                "non_text_elements": [],
            }
        ],
    }
    item = {"file_id": "a.pdf", "source_sha256": "a" * 64}
    review = {
        **item,
        "reading_sha256": digest(canonical_bytes(reading)),
        "reviewer": "AI visual review",
        "reviewed_at": "2026-09-19T00:00:00Z",
        "inspected_pages": [1],
        "changes": [
            {
                "block_id": "p1-fg-b1",
                "set": {"text": "Supplier"},
                "reason": "Compared with original rendered page",
            }
        ],
        "additions": [],
    }
    return item, {"reading": reading, "layout": {}}, review


def test_review_is_hash_bound_immutable_explicit_and_reproducible():
    item, original, review = example()
    saved = deepcopy(original)
    contracts = Contracts("benchmark/schemas")
    result = apply_reading_review(item, original, review, contracts)
    assert result["reading"]["pages"][0]["blocks"][0]["text"] == "Supplier"
    assert original == saved
    assert result["layout"]["visual_review"]["automatic_extraction"] is False
    assert result == apply_reading_review(item, original, review, contracts)


@pytest.mark.parametrize(
    "key,value",
    [
        ("source_sha256", "b" * 64),
        ("reading_sha256", "b" * 64),
        ("file_id", "b.pdf"),
        ("inspected_pages", []),
        ("reviewer", ""),
    ],
)
def test_invalid_review_is_rejected(key, value):
    item, original, review = example()
    review[key] = value
    with pytest.raises(ValueError):
        apply_reading_review(item, original, review, Contracts("benchmark/schemas"))
