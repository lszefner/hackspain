"""Deterministic extraction over the real text layer of caja/facturas."""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

import pytest

from ingestion.contracts import Contracts
from ingestion.deterministic import (
    accept,
    extract,
    native_reading,
    strip_invisible,
)
from ingestion.normalization import normalize_invoice
from ingestion.pdf import extract_text
from ingestion.validation import validate_interpretation

ROOT = Path(__file__).resolve().parent.parent
FACTURAS = ROOT / "caja" / "facturas"

TEXT_BEARING = [
    "2026-01-08_P001.pdf",
    "2026-01-11_P007.pdf",
    "2026-01-12_P002.pdf",
    "2026-01-12_P010.pdf",
    "2026-01-14_P002.pdf",
    "2026-01-15_P003.pdf",
    "2026-01-16_P004.pdf",
    "2026-01-17_P011.pdf",
    "2026-01-18_P005.pdf",
    "2026-01-22_P006.pdf",
    "FA-4349_electricidad.pdf",
    "FA-4385_informática.pdf",
    "FA-4488_transportes.pdf",
    "FA-4634_seguridad.pdf",
    "FA-4666_transportes.pdf",
    "FA-4673_informática.pdf",
    "FA-4813_seguridad.pdf",
    "FA-4816_seguridad.pdf",
    "FA-4819_informática.pdf",
    "FA-4962_mensajería.pdf",
]

EXPECTED = {
    "2026-01-08_P001.pdf": {
        "invoice_number": "2026/11604", "issue_date": "2026-01-08",
        "purchase_order_reference": "PO-2026-0096",
        "supplier": {"name": "Suministros Levante S.L.", "tax_id": "B46102331"},
        "payment": {"iban": "ES2100491500051234567890"},
        "totals": {"taxable_base": "2489.99", "total": "3012.89"},
        "taxes": [{"label": "IVA", "rate_percent": "21", "amount": "522.9"}],
        "currency": None,
        "lines": [("Servicio mensual", None, "2489.99")],
    },
    "2026-01-15_P003.pdf": {
        "invoice_number": "F26-9524", "issue_date": "2026-01-15",
        "purchase_order_reference": "PO-2026-0070",
        "supplier": {"name": "Ofimática Cieza S.L.", "tax_id": "B30455812"},
        "payment": {"iban": "ES6001825322180201588391"},
        "totals": {"taxable_base": "6543.49", "total": "7917.62"},
        "taxes": [{"label": "IVA", "rate_percent": "21", "amount": "1374.13"}],
        "currency": "EUR",
        "lines": [("Transporte urgente", "1", "458.04"),
                  ("Horas de soporte", "1", "5169.36"),
                  ("Consumibles", "1", "916.09")],
    },
    "FA-4349_electricidad.pdf": {
        "invoice_number": "FA-4349", "issue_date": "2026-03-17",
        "purchase_order_reference": "PO-2026-0403",
        "supplier": {"name": "ELECTRICIDAD MONTCADA S.A.", "tax_id": "A46990201"},
        "payment": {"iban": "ES3520385778983000765410"},
        "totals": {"taxable_base": "3545.99", "total": "4290.65"},
        "taxes": [{"label": "IVA", "rate_percent": "21", "amount": "744.66"}],
        "currency": "EUR",
        "lines": [("Mantenimiento trimestral", "1", "3545.99")],
    },
    "FA-4816_seguridad.pdf": {
        "invoice_number": "FA-4816", "issue_date": "2026-03-07",
        "purchase_order_reference": "PO-2026-0394",
        "supplier": {"name": "Seguridad Alcores S.L.", "tax_id": "B91455230"},
        "payment": {"iban": "ES0900730100510505331902"},
        "totals": {"taxable_base": "1949.59", "total": "2359"},
        "taxes": [{"label": "IVA", "rate_percent": "21", "amount": "409.41"}],
        "currency": None,
        "lines": [("Material de oficina", "1", "760.34"),
                  ("Instalación", "1", "1189.25")],
    },
    "2026-01-14_P002.pdf": {
        "invoice_number": "FA-2954", "issue_date": "2026-01-14",
        "purchase_order_reference": "PO-2026-0144",
        "supplier": {"name": "TRANSPORTES GUADAIRA S.A.", "tax_id": "A41220987"},
        "payment": {"iban": "ES7621000813610123456789"},
        "totals": {"taxable_base": "2452.27", "total": "2967.25"},
        "taxes": [{"label": "IVA", "rate_percent": "21", "amount": "514.98"}],
        "currency": None,
        "lines": [("MATERIAL DE OFICINA", None, "416.89"),
                  ("SUMINISTRO PEDIDO", None, "2035.38")],
    },
    "FA-4385_informática.pdf": {
        "invoice_number": "FA-4385", "issue_date": "2026-06-21",
        "purchase_order_reference": "PO-2026-0091",
        "supplier": {"name": "INFORMÁTICA BENIMÁMET S.L.", "tax_id": "B98455101"},
        "payment": {"iban": "ES2702390806667122334455"},
        "totals": {"taxable_base": "104.04", "total": "125.89"},
        "taxes": [{"label": "IVA", "rate_percent": "21", "amount": "21.85"}],
        "currency": "EUR",
        "lines": [("Cuota de servicio", "1", "2.08"),
                  ("Instalación", "1", "101.96")],
    },
}

CUSTOMER_CIF = "A58231074"


@pytest.fixture(scope="module")
def contracts():
    return Contracts(str(ROOT / "benchmark" / "schemas"))


def _read(file_id):
    pdf = FACTURAS / file_id
    if not pdf.is_file():
        pytest.skip("caja/facturas not present")
    return native_reading(file_id, extract_text(pdf.read_bytes()))


@pytest.mark.parametrize("file_id", TEXT_BEARING)
def test_sample_accepted(file_id, contracts):
    reading = _read(file_id)
    assert reading is not None
    contracts.validate("reading", reading)
    out = extract(reading)
    invoice = normalize_invoice(out["invoice"])
    invoice["file_id"] = file_id
    invoice["schema_version"] = "0.1"
    contracts.validate("invoice", invoice)
    checks = validate_interpretation(
        invoice, out["evidence"], reading, contracts)
    assert checks["status"] == "completed"
    ok, reasons = accept(invoice, checks)
    assert ok, reasons
    assert invoice["supplier"]["tax_id"] != CUSTOMER_CIF

    expected = EXPECTED.get(file_id)
    if expected:
        for key in ("invoice_number", "issue_date",
                    "purchase_order_reference", "currency"):
            assert invoice[key] == expected[key], key
        assert invoice["supplier"]["name"] == expected["supplier"]["name"]
        assert invoice["supplier"]["tax_id"] == expected["supplier"]["tax_id"]
        assert invoice["payment"]["iban"] == expected["payment"]["iban"]
        assert invoice["totals"] == expected["totals"]
        assert invoice["taxes"] == expected["taxes"]
        assert [(line["description"], line["quantity"], line["amount"])
                for line in invoice["lines"]] == expected["lines"]


@pytest.mark.parametrize("file_id", TEXT_BEARING[:6])
def test_evidence_spans_match_source(file_id, contracts):
    reading = _read(file_id)
    out = extract(reading)
    blocks = {block["id"]: block["text"]
              for page in reading["pages"] for block in page["blocks"]}
    for pointer, links in out["evidence"]["pointers"].items():
        for link in links:
            text = blocks[link["reference_ids"][0]]
            span = text[link["start"]:link["end"]]
            assert span.strip(), (file_id, pointer)
            assert re.search(r"[\w€]", span), (file_id, pointer, span)


def test_scan_has_no_text_layer(contracts):
    pdf = FACTURAS / "scan_017.pdf"
    if not pdf.is_file():
        pytest.skip("caja/facturas not present")
    assert native_reading("scan_017.pdf", extract_text(pdf.read_bytes())) is None


def test_accept_rejects_missing_gate_pointer(contracts):
    reading = native_reading("f.pdf", [(
        "FACTURA\nFactura: F-1 Fecha: 08/01/2026\n"
        "Suministros Levante S.L. NIF: B46102331\n"
        "Pedido: PO-2026-0096\n"
        "Cliente: Banco Miralmar S.A. · CIF: A58231074\n"
        "Servicio ....... 100,00\n"
        "Base: 100,00\nIVA (21%): 21,00\nTOTAL: 121,00")])
    out = extract(reading)
    invoice = normalize_invoice(out["invoice"])
    invoice["file_id"] = "f.pdf"
    checks = validate_interpretation(invoice, out["evidence"], reading, contracts)
    ok, reasons = accept(invoice, checks)
    assert not ok
    assert any("/payment/iban" in reason for reason in reasons)


def test_ambiguous_purchase_order_gap():
    reading = native_reading("f.pdf", [(
        "FACTURA\nFactura: F-1 Fecha: 08/01/2026\n"
        "Pedido: PO-2026-0096 y PO-2026-0097\n"
        "Suministros Levante S.L.\nNIF: B46102331\n"
        "IBAN: ES21 0049 1500 0512 3456 7890\n"
        "Cliente: Banco Miralmar S.A. · CIF: A58231074\n"
        "Servicio ....... 100,00\nBase: 100,00\nIVA (21%): 21,00\nTOTAL: 121,00")])
    out = extract(reading)
    assert out["invoice"]["purchase_order_reference"] is None
    assert "ambiguous_purchase_order" in out["gaps"]


SAMPLE_TEXT = (
    "FACTURA\n"
    "Factura: 2026/11604 Fecha: 08/01/2026\n"
    "Pedido: PO-2026-0096\n"
    "Suministros Levante S.L.\n"
    "NIF: B46102331\n"
    "IBAN: ES21 0049 1500 0512 3456 7890\n"
    "Cliente: Banco Miralmar S.A. · CIF: A58231074\n"
    "Servicio mensual ....... 2.489,99\n"
    "Base: 2.489,99\n"
    "IVA (21%): 522,90\n"
    "TOTAL: 3.012,89\n"
    "Documento generado por el sistema de facturacion del proveedor.\n")


def _pipeline(tmp_path, monkeypatch, *, page_texts, cascade=None):
    pytest.importorskip("backend.local_backend")
    import ingestion.pipeline as pipe_mod
    import ingestion.providers.deepseek as ds_mod
    import ingestion.providers.vision as vs_mod
    from backend.local_backend import LocalRepository, LocalStorage

    pdf = tmp_path / "f1.pdf"
    pdf.write_bytes(b"%PDF-fake-f1")
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    monkeypatch.setattr(pipe_mod, "render_pdf",
                        lambda b, dpi: [{"page": 1, "width": 100, "height": 100,
                                         "rotation": 0, "image": b"png"}])
    monkeypatch.setattr(pipe_mod, "extract_text", lambda b: page_texts)

    vision_calls = []

    async def vision_run(self, image, page_number):
        vision_calls.append(page_number)
        return {"text": VISION_TEXT,
                "page": {"page": 1, "blocks": [{"id": "p1-b1", "kind": "other",
                         "text": VISION_TEXT, "rows": [], "uncertainties": []}],
                         "non_text_elements": []},
                "layout": {"blocks": {"p1-b1": {"page": 1, "bbox": None,
                                              "confidence": None,
                                              "source": "vision"}}},
                "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    async def ds_run(self, reading, schema):
        return {"invoice": dict(VISION_INVOICE), "evidence": VISION_EVIDENCE,
                "usage": {"prompt_tokens": 9, "completion_tokens": 4}}

    monkeypatch.setattr(vs_mod.VisionReader, "run", vision_run)
    monkeypatch.setattr(ds_mod.DeepSeek, "run", ds_run)

    repo = LocalRepository()
    storage = LocalStorage(tmp_path / "objects")
    contracts = Contracts(str(ROOT / "benchmark" / "schemas"))
    config = {"version": "test", "interpreter": "deepseek",
              "ocr": "helmcode-vision", "deepseek_model": "ds",
              "vision_model": "vm", "dpi": 200, "timeout": 30,
              "concurrency": 1, "schema_hashes": contracts.hashes,
              "helmcode_base_url": "https://api.helmcode.com/v1"}
    if cascade is not None:
        config["extraction_cascade"] = cascade
    pipeline = pipe_mod.Pipeline(repo, storage, contracts, config,
                                 {"HELMCODE_API_KEY": "x"})
    manifest = [{"file_id": "f1.pdf", "relative_path": "f1.pdf",
                 "local_path": str(pdf), "source_sha256": sha,
                 "ordinal": 0, "error": None}]
    batch = repo.create_batch(manifest, config)
    return pipeline, batch, manifest[0], repo, vision_calls


VISION_TEXT = ("F-20261510 Suministros Levante B46102331 PO-2026-0164 "
               "2026-09-07 ES2100491500051234567890 2918.31 612.85 3531.16 EUR")
VISION_INVOICE = {"schema_version": "0.1", "file_id": "f1.pdf",
                  "document_type": "invoice", "invoice_number": "F-20261510",
                  "issue_date": "2026-09-07",
                  "purchase_order_reference": "PO-2026-0164", "currency": "EUR",
                  "supplier": {"name": "Suministros Levante S.L.",
                               "tax_id": "B46102331", "location": None,
                               "address": None},
                  "customer": {"name": "Cliente", "tax_id": None,
                               "location": None, "address": None},
                  "payment": {"iban": "ES2100491500051234567890"},
                  "lines": [{"position": 1, "description": "Material",
                             "quantity": "1", "amount": "2918.31"}],
                  "taxes": [{"label": "IVA", "rate_percent": "21",
                             "amount": "612.85"}],
                  "totals": {"taxable_base": "2918.31", "total": "3531.16"},
                  "annotations": [], "additional_fields": [], "issues": []}
VISION_EVIDENCE = {p: [{"page": 1, "reference_ids": ["p1-b1"]}] for p in (
    "/invoice_number", "/issue_date", "/purchase_order_reference",
    "/currency", "/document_type", "/supplier/name", "/supplier/tax_id",
    "/customer/name", "/payment/iban", "/lines/0/description",
    "/lines/0/quantity", "/lines/0/amount", "/taxes/0/label",
    "/taxes/0/rate_percent", "/taxes/0/amount",
    "/totals/taxable_base", "/totals/total")}


class TestCascadeRouting:
    def test_text_pdf_takes_deterministic_route(self, tmp_path, monkeypatch):
        pipeline, batch, item, repo, vision_calls = _pipeline(
            tmp_path, monkeypatch, page_texts=[SAMPLE_TEXT])
        outcome = asyncio.run(pipeline.process_input(batch, item))
        assert outcome["status"] == "completed"
        assert outcome["extraction"]["route"] == "deterministic"
        assert outcome["extraction"]["deterministic"] == {
            "attempted": True, "accepted": True, "gaps": []}
        assert outcome["invoice"]["invoice_number"] == "2026/11604"
        assert vision_calls == []
        providers = {job["provider"] for job in repo.list_jobs(batch["id"])}
        assert providers == {"native-text", "deterministic"}

    def test_no_text_falls_back_to_vision(self, tmp_path, monkeypatch):
        pipeline, batch, item, repo, vision_calls = _pipeline(
            tmp_path, monkeypatch, page_texts=[""])
        outcome = asyncio.run(pipeline.process_input(batch, item))
        assert outcome["status"] == "completed"
        assert outcome["extraction"]["route"] == "vision"
        assert outcome["extraction"]["deterministic"]["attempted"] is False
        assert vision_calls == [1]
        providers = {job["provider"] for job in repo.list_jobs(batch["id"])}
        assert "helmcode-vision" in providers and "deepseek" in providers

    def test_gate_rejection_falls_back_to_vision(self, tmp_path, monkeypatch):
        no_iban = SAMPLE_TEXT.replace(
            "IBAN: ES21 0049 1500 0512 3456 7890\n", "")
        pipeline, batch, item, repo, vision_calls = _pipeline(
            tmp_path, monkeypatch, page_texts=[no_iban])
        outcome = asyncio.run(pipeline.process_input(batch, item))
        assert outcome["status"] == "completed"
        extraction = outcome["extraction"]
        assert extraction["route"] == "vision"
        assert extraction["deterministic"] == {
            "attempted": True, "accepted": False,
            "gaps": ["missing:/payment/iban"]}
        assert vision_calls == [1]
        states = {job["provider"]: job["state"]
                  for job in repo.list_jobs(batch["id"])}
        assert states["deterministic"] == "needs_review"

    def test_extractor_error_falls_back_to_vision(self, tmp_path, monkeypatch):
        import ingestion.pipeline as pipe_mod
        pipeline, batch, item, repo, vision_calls = _pipeline(
            tmp_path, monkeypatch, page_texts=[SAMPLE_TEXT])

        def boom(reading):
            raise RuntimeError("boom")

        monkeypatch.setattr(pipe_mod, "extract", boom)
        outcome = asyncio.run(pipeline.process_input(batch, item))
        assert outcome["status"] == "completed"
        extraction = outcome["extraction"]
        assert extraction["route"] == "vision"
        assert extraction["deterministic"]["attempted"] is True
        assert "deterministic_error:RuntimeError" in (
            extraction["deterministic"]["gaps"])
        assert vision_calls == [1]
        providers = {job["provider"] for job in repo.list_jobs(batch["id"])}
        assert "helmcode-vision" in providers and "deepseek" in providers

    def test_vision_only_skips_deterministic(self, tmp_path, monkeypatch):
        import ingestion.pipeline as pipe_mod
        pipeline, batch, item, _repo, vision_calls = _pipeline(
            tmp_path, monkeypatch, page_texts=[SAMPLE_TEXT],
            cascade="vision-only")
        monkeypatch.setattr(pipe_mod, "extract_text",
                            lambda b: (_ for _ in ()).throw(
                                AssertionError("extract_text called")))
        outcome = asyncio.run(pipeline.process_input(batch, item))
        assert outcome["status"] == "completed"
        assert outcome["extraction"]["route"] == "vision"
        assert outcome["extraction"]["deterministic"]["attempted"] is False
        assert vision_calls == [1]


def test_invisible_chars_stripped_from_amounts():
    zwsp = "\u200b"
    text = ("FACTURA\nFactura: F-1 Fecha: 08/01/2026\n"
            "Pedido: PO-2026-0096\nSuministros Levante S.L.\nNIF: B46102331\n"
            "IBAN: ES21 0049 1500 0512 3456 7890\n"
            "Cliente: Banco Miralmar S.A. · CIF: A58231074\n"
            "Servicio ....... 100,00\nBase: 100,00\nIVA (21%): 21,00\n"
            "TOTAL: " + zwsp.join("2.637,80"))
    clean, removed = strip_invisible(text)
    assert removed == 7
    reading = native_reading("f.pdf", [text])
    out = extract(reading)
    invoice = normalize_invoice(out["invoice"])
    assert invoice["totals"]["total"] == "2637.8"
    assert clean.index("2.637,80") >= 0
