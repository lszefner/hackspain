from io import BytesIO
from pathlib import Path

import pytest

from ingestion.contracts import Contracts, canonical_bytes, digest
from ingestion.normalization import (
    normalize_date,
    normalize_decimal,
    normalize_iban,
)
from ingestion.pdf import render_pdf
from ingestion.validation import validate_interpretation

ROOT = Path(__file__).parents[1]


@pytest.fixture()
def contracts():
    return Contracts(ROOT / "benchmark" / "schemas")


def test_canonical_bytes_is_sorted_compact_utf8_and_digest_is_stable():
    assert canonical_bytes({"é": "sí", "a": 1}) == b'{"a":1,"\xc3\xa9":"s\xc3\xad"}'
    assert digest(b"invoice") == digest(b"invoice")
    with pytest.raises(ValueError):
        canonical_bytes(float("nan"))


def test_blank_invoice_matches_current_contract(contracts):
    invoice = contracts.blank_invoice("scan.pdf")
    assert invoice["file_id"] == "scan.pdf"
    assert invoice["document_type"] == "unknown"
    assert invoice["lines"] == []
    assert invoice["currency"] is None


def test_normalization_handles_spanish_numbers_dates_and_iban():
    assert normalize_decimal("1.234,50 €") == "1234.5"
    assert normalize_decimal("(2,00)") == "-2"
    assert normalize_date("31/01/2026") == "2026-01-31"
    assert normalize_date("01/02/2026") is None
    assert normalize_date("8 de enero de 2026") == "2026-01-08"
    assert normalize_iban("ES12 1234 5678 9012") == "ES12123456789012"


def test_interpretation_requires_valid_evidence_and_marks_uncertainty(contracts):
    invoice = contracts.blank_invoice("scan.pdf")
    reading = {
        "schema_version": "0.1",
        "file_id": "scan.pdf",
        "capabilities": {"block_kinds": True, "tables": False, "layout": False},
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {
                        "id": "b1",
                        "kind": "paragraph",
                        "text": "Factura ilegible",
                        "rows": [],
                        "uncertainties": [
                            {
                                "kind": "unreadable",
                                "raw_text": None,
                                "description": "blurred",
                            }
                        ],
                    }
                ],
                "non_text_elements": [],
            }
        ],
    }
    result = validate_interpretation(invoice, {}, reading, contracts)
    assert result["status"] == "needs_review"
    assert result["checks"]["source_uncertainty_clear"] is False
    assert result["coverage"]["unresolved_blocks"] == 1


def test_interpretation_accepts_linked_factual_leaf(contracts):
    invoice = contracts.blank_invoice("one.pdf")
    invoice["invoice_number"] = "A-1"
    reading = {
        "schema_version": "0.1",
        "file_id": "one.pdf",
        "capabilities": {"block_kinds": True, "tables": False, "layout": False},
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {
                        "id": "b1",
                        "kind": "paragraph",
                        "text": "A-1",
                        "rows": [],
                        "uncertainties": [],
                    }
                ],
                "non_text_elements": [],
            }
        ],
    }
    result = validate_interpretation(
        invoice,
        {"/invoice_number": [{"page": 1, "reference_ids": ["b1"]}]},
        reading,
        contracts,
    )
    assert result["status"] == "needs_review"
    assert result["checks"]["factual_leaves_linked"] is True
    assert result["coverage"]["mapped_blocks"] == 1


def test_evidence_spans_are_bounded_and_must_be_complete(contracts):
    invoice = contracts.blank_invoice("span.pdf")
    invoice["invoice_number"] = "A-1"
    reading = {
        "schema_version": "0.1",
        "file_id": "span.pdf",
        "capabilities": {"block_kinds": True, "tables": False, "layout": False},
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {
                        "id": "b1",
                        "kind": "paragraph",
                        "text": "A-1",
                        "rows": [],
                        "uncertainties": [],
                    }
                ],
                "non_text_elements": [],
            }
        ],
    }
    good = {
        "/invoice_number": [{"page": 1, "reference_ids": ["b1"], "start": 0, "end": 3}]
    }
    assert (
        validate_interpretation(invoice, good, reading, contracts)["checks"][
            "evidence_references"
        ]
        is True
    )
    bad = {"/invoice_number": [{"page": 1, "reference_ids": ["b1"], "start": 0}]}
    assert (
        validate_interpretation(invoice, bad, reading, contracts)["status"]
        == "needs_review"
    )


def test_render_pdf_reports_png_page_metadata():
    pdf = next((ROOT / "facturas").glob("*.pdf")).read_bytes()
    page = render_pdf(pdf, dpi=72)[0]
    assert page["page"] == 1
    assert page["dpi"] == 72
    assert page["width"] > 0 and page["height"] > 0
    assert page["image"].startswith(b"\x89PNG")
    assert isinstance(page["embedded_text"], str)


def test_render_pdf_respects_intrinsic_page_rotation_without_double_rotation():
    import pypdfium2 as pdfium

    source = next((ROOT / "facturas").glob("*.pdf")).read_bytes()
    original = render_pdf(source, dpi=72)[0]
    document = pdfium.PdfDocument(source)
    document[0].set_rotation(90)
    rotated_bytes = BytesIO()
    document.save(rotated_bytes)
    document.close()

    rotated = render_pdf(rotated_bytes.getvalue(), dpi=72)[0]
    assert rotated["rotation"] == 90
    assert (rotated["width"], rotated["height"]) == (
        original["height"],
        original["width"],
    )


def test_frozen_contracts_survive_checkout_changes(contracts):
    frozen = Contracts.from_snapshot(contracts.schemas, contracts.hashes)
    contracts.schemas["invoice"]["properties"]["file_id"] = {"type": "integer"}
    frozen.validate("invoice", frozen.blank_invoice("still-a-string.pdf"))


def test_arithmetic_warning_preserves_printed_total(contracts):
    invoice = contracts.blank_invoice("a.pdf")
    invoice["totals"] = {"taxable_base": "100", "total": "120"}
    invoice["taxes"] = [{"label": "IVA", "rate_percent": "21", "amount": "21"}]
    reading = {
        "schema_version": "0.1",
        "file_id": "a.pdf",
        "capabilities": {"block_kinds": False, "tables": False, "layout": False},
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {
                        "id": "b",
                        "kind": "other",
                        "text": "Base 100 IVA 21% 21 Total 120",
                        "rows": [],
                        "uncertainties": [],
                    }
                ],
                "non_text_elements": [],
            }
        ],
    }
    evidence = {
        p: [{"page": 1, "reference_ids": ["b"]}]
        for p in (
            "/totals/taxable_base",
            "/totals/total",
            "/taxes/0/label",
            "/taxes/0/rate_percent",
            "/taxes/0/amount",
        )
    }
    result = validate_interpretation(invoice, evidence, reading, contracts)
    assert (
        result["warnings"][0]["code"]
        == "printed_base_plus_listed_taxes_differs_from_total"
    )
    assert invoice["totals"]["total"] == "120"


def test_footer_only_reading_cannot_complete_invoice(contracts):
    invoice = contracts.blank_invoice("footer-only.pdf")
    invoice["document_type"] = "other"
    invoice["annotations"] = [
        {"kind": "footer", "text": "Documento generado"} for _ in range(4)
    ]
    reading = {
        "schema_version": "0.1",
        "file_id": invoice["file_id"],
        "capabilities": {"block_kinds": False, "tables": False, "layout": False},
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {
                        "id": "b1",
                        "kind": "other",
                        "text": "Documento generado\n" * 4,
                        "rows": [],
                        "uncertainties": [],
                    }
                ],
                "non_text_elements": [],
            }
        ],
    }
    evidence = {"/document_type": [{"page": 1, "reference_ids": ["b1"]}]}
    for n in range(4):
        for field in ("kind", "text"):
            evidence[f"/annotations/{n}/{field}"] = [
                {"page": 1, "reference_ids": ["b1"]}
            ]
    result = validate_interpretation(invoice, evidence, reading, contracts)
    assert result["checks"]["invoice_schema"]
    assert result["checks"]["factual_leaves_linked"]
    assert result["coverage"]["score"] == 1
    assert result["status"] == "needs_review"
    assert not result["checks"]["invoice_identity_present"]
    assert not result["checks"]["invoice_total_present"]
    assert not result["checks"]["invoice_lines_present"]


def test_complete_invoice_can_complete_without_optional_bank_or_tax(contracts):
    invoice = contracts.blank_invoice("complete.pdf")
    invoice.update(document_type="invoice", invoice_number="A-1")
    invoice["supplier"]["name"] = "Supplier"
    invoice["totals"]["total"] = "12.00"
    invoice["lines"] = [
        {"position": 1, "description": "Service", "quantity": None, "amount": "12.00"}
    ]
    reading = {
        "schema_version": "0.1",
        "file_id": invoice["file_id"],
        "capabilities": {"block_kinds": False, "tables": False, "layout": False},
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {
                        "id": "b1",
                        "kind": "other",
                        "text": "Supplier Invoice A-1 Service 12.00 Total 12.00",
                        "rows": [],
                        "uncertainties": [],
                    }
                ],
                "non_text_elements": [],
            }
        ],
    }
    evidence = {
        p: [{"page": 1, "reference_ids": ["b1"]}]
        for p in (
            "/document_type",
            "/invoice_number",
            "/supplier/name",
            "/totals/total",
            "/lines/0/description",
            "/lines/0/amount",
        )
    }
    assert (
        validate_interpretation(invoice, evidence, reading, contracts)["status"]
        == "completed"
    )

    # Observed live regression: schema allowed the printed symbol in a code field.
    invoice["currency"] = "€"
    evidence["/currency"] = [{"page": 1, "reference_ids": ["b1"]}]
    result = validate_interpretation(invoice, evidence, reading, contracts)
    assert result["status"] == "needs_review"
    assert not result["checks"]["currency_code_shape"]
    assert invoice["currency"] == "€"  # Validation never silently repairs facts.
    invoice["currency"] = "EUR"
    assert (
        validate_interpretation(invoice, evidence, reading, contracts)["status"]
        == "completed"
    )


def test_annotation_kind_evidence_derived_only_from_matching_reading_kind():
    from ingestion.validation import complete_annotation_kind_evidence

    invoice = {
        "annotations": [
            {"kind": "note", "text": "OK"},
            {"kind": "stamp", "text": "Footer"},
        ]
    }
    evidence = {
        "/annotations/0/text": [{"page": 1, "reference_ids": ["note"]}],
        "/annotations/1/text": [{"page": 1, "reference_ids": ["footer"]}],
    }
    reading = {
        "pages": [
            {
                "blocks": [
                    {"id": "note", "kind": "note"},
                    {"id": "footer", "kind": "footer"},
                ]
            }
        ]
    }
    result = complete_annotation_kind_evidence(invoice, evidence, reading)
    assert result["/annotations/0/kind"] == evidence["/annotations/0/text"]
    assert "/annotations/1/kind" not in result
    assert "/annotations/0/kind" not in evidence
