"""Explicit, source-bound review corrections; never inference reference answers."""

from __future__ import annotations

from copy import deepcopy

from .contracts import canonical_bytes, digest


def apply_reading_review(item, original, review, contracts):
    """Apply separately attested visual corrections to an immutable reading.

    The original remains in its source batch. Every change requires a reason,
    original reading hash and original PDF hash. Review is never called an
    automatic extraction result or human verification unless the reviewer is human.
    """
    if (
        review.get("file_id") != item["file_id"]
        or review.get("source_sha256") != item["source_sha256"]
        or review.get("reading_sha256") != digest(canonical_bytes(original["reading"]))
    ):
        raise ValueError("Review source/reading identity mismatch")
    if not review.get("reviewer") or not review.get("reviewed_at"):
        raise ValueError("Review must identify reviewer and time")
    reading = deepcopy(original["reading"])
    if review.get("inspected_pages") != [p["page"] for p in reading["pages"]]:
        raise ValueError("Review must cover every source page")
    blocks = {b["id"]: b for p in reading["pages"] for b in p["blocks"]}
    seen = set()
    for change in review.get("changes", []):
        bid = change.get("block_id")
        if bid not in blocks or bid in seen or not change.get("reason"):
            raise ValueError("Invalid or duplicate reviewed block")
        seen.add(bid)
        if set(change.get("set", {})) - {"text", "kind", "uncertainties"}:
            raise ValueError("Review cannot alter identity or geometric evidence")
        blocks[bid].update(deepcopy(change["set"]))
    for addition in review.get("additions", []):
        if not addition.get("reason"):
            raise ValueError("Review addition requires source rationale")
        anchor = addition.get("after_block_id")
        for page in reading["pages"]:
            ids = [b["id"] for b in page["blocks"]]
            if anchor in ids:
                page["blocks"].insert(
                    ids.index(anchor) + 1, deepcopy(addition["block"])
                )
                break
        else:
            raise ValueError("Review anchor does not exist")
    contracts.validate("reading", reading)
    result = deepcopy(original)
    result["reading"] = reading
    result["review"] = deepcopy(review)
    result.setdefault("layout", {})["visual_review"] = {
        "reviewer": review["reviewer"],
        "review_sha256": digest(canonical_bytes(review)),
        "original_reading_sha256": review["reading_sha256"],
        "reviewed_reading_sha256": digest(canonical_bytes(reading)),
        "automatic_extraction": False,
    }
    return result
