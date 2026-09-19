"""Validation of interpreted invoices and their source evidence."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from .contracts import Contracts


def complete_annotation_kind_evidence(invoice, evidence, reading):
    """Derive annotation-kind links only from explicit reading metadata.

    Models often link annotation text but omit its kind. No factual text is
    changed, and unmatched classifications remain unlinked for review.
    """
    result = deepcopy(evidence)
    pointers = result.get("pointers", result)
    kinds = {b["id"]: b["kind"] for p in reading["pages"] for b in p["blocks"]}
    for index, annotation in enumerate(invoice.get("annotations", [])):
        kind_pointer = f"/annotations/{index}/kind"
        links = pointers.get(f"/annotations/{index}/text", [])
        refs = [ref for link in _links(links) for ref in link.get("reference_ids", [])]
        if kind_pointer in pointers or not refs:
            continue
        if all(kinds.get(ref) == annotation["kind"] for ref in refs) or (
            annotation["kind"] == "other" and all("-bg-" in ref for ref in refs)
        ):
            pointers[kind_pointer] = deepcopy(links)
    return result


def _pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            raise KeyError(pointer)
    return value


def _iter_leaves(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from _iter_leaves(child, f"{prefix}/{escaped}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_leaves(child, f"{prefix}/{index}")
    else:
        yield prefix or "/", value


def _reading_index(
    reading: dict[str, Any],
) -> tuple[dict[str, int], dict[str, str], dict[str, str]]:
    pages: dict[str, int] = {}
    owners: dict[str, str] = {}
    texts: dict[str, str] = {}
    for page in reading.get("pages", []):
        number = page.get("page")
        for block in page.get("blocks", []):
            block_id = block.get("id")
            if isinstance(block_id, str):
                pages[block_id] = number
                owners[block_id] = block_id
                texts[block_id] = block.get("text", "")
            for row in block.get("rows", []):
                row_id = row.get("id")
                if isinstance(row_id, str):
                    pages[row_id] = number
                    owners[row_id] = block_id
                    texts[row_id] = ""
                for cell in row.get("cells", []):
                    cell_id = cell.get("id")
                    if isinstance(cell_id, str):
                        pages[cell_id] = number
                        owners[cell_id] = block_id
                        texts[cell_id] = cell.get("text", "")
    return pages, owners, texts


def _evidence_map(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {}
    pointers = evidence.get("pointers", evidence)
    return pointers if isinstance(pointers, dict) else {}


def _links(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    return value if isinstance(value, list) else []


def _is_exempt(path: str, value: Any) -> bool:
    if path in {"/schema_version", "/file_id"}:
        return True
    if path == "/document_type" and value == "unknown":
        return True
    if path.startswith("/issues"):
        return True
    return path.startswith("/lines/") and path.endswith("/position")


def validate_interpretation(
    invoice: dict[str, Any],
    evidence: dict[str, Any],
    reading: dict[str, Any],
    contracts: Contracts,
) -> dict[str, Any]:
    """Validate schema, provenance and review conditions.

    The result is deliberately a plain JSON object.  A result can be
    schema-valid yet require review when the source has uncertainty, unresolved
    invoice issues, missing provenance, or an all-null interpretation.
    """

    invoice_value = invoice if isinstance(invoice, dict) else {}
    reading_value = reading if isinstance(reading, dict) else {}
    checks: dict[str, bool] = {}
    try:
        contracts.validate("invoice", invoice)
        checks["invoice_schema"] = True
    except (KeyError, TypeError, ValueError):
        checks["invoice_schema"] = False
    try:
        contracts.validate("reading", reading)
        checks["reading_schema"] = True
    except (KeyError, TypeError, ValueError):
        checks["reading_schema"] = False

    pages_by_id, owners, text_by_id = _reading_index(reading_value)
    pointers = _evidence_map(evidence)
    evidence_errors = False
    valid_refs: dict[str, list[dict[str, Any]]] = {}
    for pointer, raw_links in pointers.items():
        try:
            target = _pointer(invoice_value, pointer)
        except (KeyError, IndexError, TypeError, ValueError):
            evidence_errors = True
            continue
        links = _links(raw_links)
        if not links:
            evidence_errors = True
            continue
        valid: list[dict[str, Any]] = []
        for link in links:
            if not isinstance(link, dict):
                evidence_errors = True
                continue
            page = link.get("page")
            reference_ids = link.get("reference_ids")
            if (
                not isinstance(page, int)
                or isinstance(page, bool)
                or not isinstance(reference_ids, list)
                or not reference_ids
                or any(
                    not isinstance(ref, str) or ref not in pages_by_id
                    for ref in reference_ids
                )
                or any(pages_by_id[ref] != page for ref in reference_ids)
            ):
                evidence_errors = True
                continue
            start, end = link.get("start"), link.get("end")
            if (start is None) != (end is None):
                evidence_errors = True
                continue
            if start is not None and len(reference_ids) != 1:
                evidence_errors = True
                continue
            if start is not None and (
                not isinstance(start, int) or isinstance(start, bool) or start < 0
            ):
                evidence_errors = True
                continue
            if end is not None and (
                not isinstance(end, int) or isinstance(end, bool) or end < 0
            ):
                evidence_errors = True
                continue
            if start is not None and end is not None and end < start:
                evidence_errors = True
                continue
            if start is not None and end is not None:
                source_text = text_by_id.get(reference_ids[0])
                if source_text is None or end > len(source_text):
                    evidence_errors = True
                    continue
            valid.append(link)
        valid_refs[pointer] = valid
        # Non-null facts must be linked.  Empty collections are intentionally
        # not facts; their children, when present, are checked independently.
        if target is not None and not _is_exempt(pointer, target) and not valid:
            evidence_errors = True

    missing_evidence = False
    for pointer, value in _iter_leaves(invoice_value):
        if value is None or _is_exempt(pointer, value):
            continue
        if pointer not in valid_refs or not valid_refs[pointer]:
            missing_evidence = True
    checks["evidence_references"] = not evidence_errors
    checks["factual_leaves_linked"] = not missing_evidence
    checks["foreground_evidence_only"] = not any(
        "-bg-" in ref
        for pointer, links in valid_refs.items()
        if not pointer.startswith(("/annotations/", "/issues/"))
        for link in links
        for ref in link.get("reference_ids", [])
    )

    uncertainties = []
    for page in reading_value.get("pages", []):
        for block in page.get("blocks", []):
            uncertainties.extend(block.get("uncertainties", []))
    checks["source_uncertainty_clear"] = not uncertainties
    checks["invoice_issues_clear"] = not bool(invoice_value.get("issues"))

    factual_values = [
        value
        for pointer, value in _iter_leaves(invoice_value)
        if value is not None and not _is_exempt(pointer, value)
    ]
    has_source_content = any(
        block.get("text") or block.get("rows") or block.get("uncertainties")
        for page in reading_value.get("pages", [])
        for block in page.get("blocks", [])
    )
    checks["nonempty_interpretation"] = bool(factual_values) or not has_source_content

    # This worker accepts invoices. A footer, a classification, or a single
    # linked identifier is not evidence that the invoice was actually read.
    # Missing essentials cause review; they must never be inferred to pass.
    checks["invoice_identity_present"] = (
        invoice_value.get("document_type") in {"invoice", "credit_note"}
        and bool(invoice_value.get("invoice_number"))
        and bool((invoice_value.get("supplier") or {}).get("name"))
    )
    currency = invoice_value.get("currency")
    checks["currency_code_shape"] = currency is None or (
        isinstance(currency, str)
        and len(currency) == 3
        and all("A" <= char <= "Z" for char in currency)
    )
    checks["invoice_total_present"] = (invoice_value.get("totals") or {}).get(
        "total"
    ) is not None
    checks["invoice_lines_present"] = bool(invoice_value.get("lines")) and all(
        bool(row.get("description")) and row.get("amount") is not None
        for row in invoice_value.get("lines", [])
    )

    block_status: dict[str, str] = {}
    mapped_ids = {
        ref
        for links in valid_refs.values()
        for link in links
        for ref in link.get("reference_ids", [])
    }
    for block_id, owner in owners.items():
        if block_id in mapped_ids or owner in mapped_ids:
            block_status[owner] = "mapped"
        else:
            block_status.setdefault(owner, "preserved_unmapped")
    unresolved_owners = {
        block.get("id")
        for page in reading_value.get("pages", [])
        for block in page.get("blocks", [])
        if block.get("uncertainties")
    }
    for owner in unresolved_owners:
        if owner in block_status:
            block_status[owner] = "unresolved"
    total = len(block_status)
    mapped = sum(status == "mapped" for status in block_status.values())
    coverage = {
        "measurement": "OCR block linkage only; not source-image completeness or factual accuracy",
        "blocks": [
            {"id": block_id, "status": status}
            for block_id, status in sorted(block_status.items())
        ],
        "total_blocks": total,
        "mapped_blocks": mapped,
        "preserved_unmapped_blocks": sum(
            status == "preserved_unmapped" for status in block_status.values()
        ),
        "unresolved_blocks": sum(
            status == "unresolved" for status in block_status.values()
        ),
        "score": mapped / total if total else 1.0,
    }
    checks["coverage_accounted"] = coverage["unresolved_blocks"] == 0
    status = "completed" if all(checks.values()) else "needs_review"
    warnings = []
    totals = invoice.get("totals", {})
    taxes = invoice.get("taxes", [])
    # This is a conditional reconciliation observation, never a correction or
    # an inference that absent discounts/withholdings are zero.
    if (
        checks.get("invoice_schema", False)
        and totals.get("taxable_base") is not None
        and totals.get("total") is not None
        and taxes
        and all(tax.get("amount") is not None for tax in taxes)
    ):
        try:
            expected = Decimal(totals["taxable_base"]) + sum(
                Decimal(tax["amount"]) for tax in taxes
            )
            if expected != Decimal(totals["total"]):
                warnings.append(
                    {
                        "code": "printed_base_plus_listed_taxes_differs_from_total",
                        "field": "/totals/total",
                        "printed_values_preserved": True,
                    }
                )
        except (InvalidOperation, TypeError):
            pass
    return {
        "status": status,
        "checks": checks,
        "coverage": coverage,
        "warnings": warnings,
    }
