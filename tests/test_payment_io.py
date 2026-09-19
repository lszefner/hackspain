import json
from pathlib import Path

import pytest
from test_payments import (
    AS_OF,
    PEDIDOS,
    PROVEEDORES,
    erp_snapshot,
    frozen_rules,
    good_invoice,
)

from ingestion.contracts import Contracts, digest
from ingestion.export import export_bundle
from payments.io import decide_files


def _workbook(tmp_path: Path) -> Path:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Proveedores"
    ws.append(["ID", "Razon Social", "NIF", "IBAN", "Ciudad", "Condiciones"])
    for row in PROVEEDORES.values():
        ws.append([row["id"], row["razon_social"], row["nif"], row["iban"],
                   row["ciudad"], row["condiciones"]])
    ws = wb.create_sheet("Pedidos_2026")
    ws.append(["Pedido", "ProveedorID", "NIF", "Importe_Total", "Estado",
               "Fecha_Pedido"])
    for row in PEDIDOS.values():
        ws.append([row["pedido"], row["proveedor_id"], row["nif"],
                   row["importe_total"], row["estado_excel"],
                   row["fecha_pedido"]])
    path = tmp_path / "master.xlsx"
    wb.save(path)
    return path


def _sources(tmp_path: Path, workbook: Path) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(
        "workbook:\n"
        f"  path: {workbook.name!r}\n"
        "  sheets:\n"
        "    proveedores:\n"
        "      sheet: Proveedores\n"
        "      key: id\n"
        "      columns:\n"
        "        id: ID\n"
        "        razon_social: Razon Social\n"
        "        nif: NIF\n"
        "        iban: IBAN\n"
        "        ciudad: Ciudad\n"
        "        condiciones: Condiciones\n"
        "      normalize: {nif: nif, iban: iban, razon_social: text,\n"
        "                  condiciones: text}\n"
        "    pedidos:\n"
        "      sheet: Pedidos_2026\n"
        "      key: pedido\n"
        "      columns:\n"
        "        pedido: Pedido\n"
        "        proveedor_id: ProveedorID\n"
        "        nif: NIF\n"
        "        importe_total: Importe_Total\n"
        "        estado_excel: Estado\n"
        "        fecha_pedido: Fecha_Pedido\n"
        "      normalize: {nif: nif, importe_total: importe,\n"
        "                  fecha_pedido: fecha}\n"
    )
    return path


@pytest.fixture()
def inputs(tmp_path):
    workbook = _workbook(tmp_path)
    sources = _sources(tmp_path, workbook)
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps(frozen_rules()))
    snap = tmp_path / "erp.json"
    snap.write_text(json.dumps(erp_snapshot()))
    return {"sources": sources, "rules": rules, "snapshot": snap}


def _export(tmp_path, invoice=None, status="completed", file_id="good.pdf",
            mutate=None):
    invoice = good_invoice() if invoice is None else invoice
    contracts = Contracts(None)
    outcome = {
        "file_id": file_id,
        "status": status,
        "error": None,
        "invoice": invoice if status == "completed" else None,
        "checks": {"interpretation_valid": True},
    }
    if mutate:
        mutate(outcome)
    export_dir = tmp_path / "export"
    export_bundle(
        export_dir,
        [{"file_id": file_id, "relative_path": file_id,
          "source_sha256": digest(b"pdf")}],
        {"interpreter": "fixture", "version": "alpha-1",
         "schema_hashes": contracts.hashes},
        [outcome],
        contracts.schemas,
    )
    return export_dir


def _decide(export_dir, inputs, tmp_path, name="decision"):
    return decide_files(
        rules=inputs["rules"],
        sources=inputs["sources"],
        as_of=AS_OF,
        output=tmp_path / name,
        export_dir=export_dir,
        erp_snapshot=inputs["snapshot"],
    )


def test_export_end_to_end(inputs, tmp_path):
    summary = _decide(_export(tmp_path), inputs, tmp_path)
    assert summary["inputs"] == 1
    assert summary["counts"] == {"PAGAR": 1, "NO PAGAR": 0, "ESCALAR": 0}
    out = tmp_path / "decision"
    for name in ("outcomes.jsonl", "rules.json", "master.json",
                 "decision-config.json", "summary.json"):
        assert (out / name).is_file()
    rows = [json.loads(l) for l in (out / "outcomes.jsonl").read_text().splitlines()]
    assert rows[0]["result"] == "PAGAR"


def test_export_rerun_identical_bytes(inputs, tmp_path):
    export_dir = _export(tmp_path)
    _decide(export_dir, inputs, tmp_path)
    first = (tmp_path / "decision" / "outcomes.jsonl").read_bytes()
    _decide(export_dir, inputs, tmp_path)
    assert (tmp_path / "decision" / "outcomes.jsonl").read_bytes() == first


def test_export_output_collision_fails(inputs, tmp_path):
    export_dir = _export(tmp_path)
    _decide(export_dir, inputs, tmp_path)
    (tmp_path / "other").mkdir()
    other = _workbook(tmp_path / "other")
    sources = _sources(tmp_path / "other", other)
    snap = tmp_path / "erp2.json"
    snap.write_text(json.dumps(erp_snapshot(estado="PAGADA")))
    with pytest.raises(ValueError):
        decide_files(rules=inputs["rules"], sources=sources, as_of=AS_OF,
                     output=tmp_path / "decision", export_dir=export_dir,
                     erp_snapshot=snap)


def test_export_missing_artifact_escalates(inputs, tmp_path):
    export_dir = _export(tmp_path)
    outcome = json.loads((export_dir / "outcomes.jsonl").read_text().strip())
    artifact = export_dir / outcome["artifacts"]["invoice"]
    artifact.unlink()
    summary = _decide(export_dir, inputs, tmp_path)
    assert summary["counts"]["ESCALAR"] == 1


def test_export_corrupt_artifact_escalates(inputs, tmp_path):
    export_dir = _export(tmp_path)
    outcome = json.loads((export_dir / "outcomes.jsonl").read_text().strip())
    artifact = export_dir / outcome["artifacts"]["invoice"]
    artifact.write_bytes(b"{}")
    summary = _decide(export_dir, inputs, tmp_path)
    assert summary["counts"]["ESCALAR"] == 1


def test_export_traversal_escalates(inputs, tmp_path):
    export_dir = _export(tmp_path)
    lines = (export_dir / "outcomes.jsonl").read_text().splitlines()
    outcome = json.loads(lines[0])
    outcome["artifacts"]["invoice"] = "../outside.json"
    data = json.dumps(outcome, sort_keys=True) + "\n"
    target = export_dir / "outcomes.jsonl"
    target.write_text(data)
    summary = _decide(export_dir, inputs, tmp_path)
    assert summary["counts"]["ESCALAR"] == 1


def test_export_unknown_envelope_rejected(inputs, tmp_path):
    export_dir = _export(tmp_path)
    with (export_dir / "outcomes.jsonl").open("a") as fh:
        fh.write(json.dumps({"file_id": "ghost.pdf", "status": "completed"}) + "\n")
    with pytest.raises(ValueError):
        _decide(export_dir, inputs, tmp_path)


def test_export_missing_outcome_placeholder(inputs, tmp_path):
    export_dir = _export(tmp_path)
    (export_dir / "outcomes.jsonl").write_text("")
    summary = _decide(export_dir, inputs, tmp_path)
    assert summary["counts"]["ESCALAR"] == 1


def test_failed_status_escalates(inputs, tmp_path):
    export_dir = _export(tmp_path, status="failed", invoice=None)
    summary = _decide(export_dir, inputs, tmp_path)
    assert summary["counts"]["ESCALAR"] == 1


def test_raw_invoices_end_to_end(inputs, tmp_path):
    inv_dir = tmp_path / "raw"
    inv_dir.mkdir()
    (inv_dir / "good.invoice.json").write_text(json.dumps(good_invoice()))
    summary = decide_files(
        rules=inputs["rules"], sources=inputs["sources"], as_of=AS_OF,
        output=tmp_path / "decision", invoices_dir=inv_dir,
        erp_snapshot=inputs["snapshot"],
    )
    assert summary["counts"] == {"PAGAR": 1, "NO PAGAR": 0, "ESCALAR": 0}


def test_raw_broken_json_escalates(inputs, tmp_path):
    inv_dir = tmp_path / "raw"
    inv_dir.mkdir()
    (inv_dir / "bad.invoice.json").write_bytes(b"{nope")
    summary = decide_files(
        rules=inputs["rules"], sources=inputs["sources"], as_of=AS_OF,
        output=tmp_path / "decision", invoices_dir=inv_dir,
        erp_snapshot=inputs["snapshot"],
    )
    assert summary["counts"]["ESCALAR"] == 1


def test_raw_input_dir_inventory_counts_every_pdf(inputs, tmp_path):
    inv_dir = tmp_path / "raw"
    pdfs = tmp_path / "facturas"
    inv_dir.mkdir()
    pdfs.mkdir()
    (inv_dir / "good.invoice.json").write_text(json.dumps(good_invoice()))
    (pdfs / "good.pdf").write_bytes(b"%PDF")
    (pdfs / "lost.pdf").write_bytes(b"%PDF")
    summary = decide_files(
        rules=inputs["rules"], sources=inputs["sources"], as_of=AS_OF,
        output=tmp_path / "decision", invoices_dir=inv_dir, input_dir=pdfs,
        erp_snapshot=inputs["snapshot"],
    )
    assert summary["inputs"] == 2
    assert summary["counts"] == {"PAGAR": 1, "NO PAGAR": 0, "ESCALAR": 1}


def test_raw_invoice_not_in_inventory_rejected(inputs, tmp_path):
    inv_dir = tmp_path / "raw"
    pdfs = tmp_path / "facturas"
    inv_dir.mkdir()
    pdfs.mkdir()
    (inv_dir / "good.invoice.json").write_text(json.dumps(good_invoice()))
    with pytest.raises(ValueError):
        decide_files(
            rules=inputs["rules"], sources=inputs["sources"], as_of=AS_OF,
            output=tmp_path / "decision", invoices_dir=inv_dir, input_dir=pdfs,
            erp_snapshot=inputs["snapshot"],
        )


def test_raw_filename_id_mismatch(inputs, tmp_path):
    inv_dir = tmp_path / "raw"
    inv_dir.mkdir()
    inv = good_invoice()
    inv["file_id"] = "other.pdf"
    (inv_dir / "good.invoice.json").write_text(json.dumps(inv))
    summary = decide_files(
        rules=inputs["rules"], sources=inputs["sources"], as_of=AS_OF,
        output=tmp_path / "decision", invoices_dir=inv_dir,
        erp_snapshot=inputs["snapshot"],
    )
    assert summary["counts"]["ESCALAR"] == 1


def test_requires_exactly_one_input_kind(inputs, tmp_path):
    with pytest.raises(ValueError):
        decide_files(rules=inputs["rules"], sources=inputs["sources"],
                     as_of=AS_OF, output=tmp_path / "d",
                     erp_snapshot=inputs["snapshot"])
    with pytest.raises(ValueError):
        decide_files(rules=inputs["rules"], sources=inputs["sources"],
                     as_of=AS_OF, output=tmp_path / "d",
                     export_dir=tmp_path / "a", invoices_dir=tmp_path / "b",
                     erp_snapshot=inputs["snapshot"])


def test_output_inside_input_rejected(inputs, tmp_path):
    export_dir = _export(tmp_path)
    with pytest.raises(ValueError):
        decide_files(rules=inputs["rules"], sources=inputs["sources"],
                     as_of=AS_OF, output=export_dir / "decision",
                     export_dir=export_dir, erp_snapshot=inputs["snapshot"])


def test_no_dotenv_or_provider_imports():
    import subprocess
    import sys

    code = (
        "import payments.adapter, payments.engine, payments.io, payments.master\n"
        "import payments.cli, sys\n"
        "assert 'dotenv' not in sys.modules\n"
        "assert not any(m.startswith('ingestion.providers') for m in sys.modules)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_export_missing_artifact_hash_escalates(inputs, tmp_path):
    export_dir = _export(tmp_path)
    line = (export_dir / "outcomes.jsonl").read_text()
    outcome = json.loads(line)
    del outcome["artifact_hashes"]["checks"]
    (export_dir / "outcomes.jsonl").write_text(json.dumps(outcome) + "\n")
    summary = _decide(export_dir, inputs, tmp_path)
    assert summary["counts"]["ESCALAR"] == 1


def test_export_missing_checks_artifact_escalates(inputs, tmp_path):
    export_dir = _export(tmp_path)
    outcome = json.loads((export_dir / "outcomes.jsonl").read_text())
    (export_dir / outcome["artifacts"]["checks"]).unlink()
    summary = _decide(export_dir, inputs, tmp_path)
    assert summary["counts"]["ESCALAR"] == 1


def test_export_source_hash_disagreement_escalates(inputs, tmp_path):
    export_dir = _export(tmp_path)
    outcome = json.loads((export_dir / "outcomes.jsonl").read_text())
    outcome["source_sha256"] = "0" * 64
    (export_dir / "outcomes.jsonl").write_text(json.dumps(outcome) + "\n")
    summary = _decide(export_dir, inputs, tmp_path)
    assert summary["counts"]["ESCALAR"] == 1
    rows = summary["results"]
    assert rows[0]["source_sha256"] != "0" * 64


def test_export_invalid_utf8_artifact_escalates(inputs, tmp_path):
    export_dir = _export(tmp_path)
    outcome = json.loads((export_dir / "outcomes.jsonl").read_text())
    artifact = export_dir / outcome["artifacts"]["invoice"]
    artifact.write_bytes(b"\xff\xfe\xff")
    outcome["artifact_hashes"]["invoice"] = digest(b"\xff\xfe\xff")
    (export_dir / "outcomes.jsonl").write_text(json.dumps(outcome) + "\n")
    summary = _decide(export_dir, inputs, tmp_path)
    assert summary["counts"]["ESCALAR"] == 1


def test_export_symlink_traversal_escalates(inputs, tmp_path):
    export_dir = _export(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(good_invoice()))
    link = export_dir / "escape.json"
    link.symlink_to(outside)
    outcome = json.loads((export_dir / "outcomes.jsonl").read_text())
    outcome["artifacts"]["invoice"] = "escape.json"
    outcome["artifact_hashes"]["invoice"] = digest(outside.read_bytes())
    (export_dir / "outcomes.jsonl").write_text(json.dumps(outcome) + "\n")
    summary = _decide(export_dir, inputs, tmp_path)
    assert summary["counts"]["ESCALAR"] == 1


def test_missing_directories_rejected(inputs, tmp_path):
    with pytest.raises(ValueError):
        decide_files(rules=inputs["rules"], sources=inputs["sources"],
                     as_of=AS_OF, output=tmp_path / "d",
                     invoices_dir=tmp_path / "nope",
                     erp_snapshot=inputs["snapshot"])
    inv_dir = tmp_path / "raw"
    inv_dir.mkdir()
    with pytest.raises(ValueError):
        decide_files(rules=inputs["rules"], sources=inputs["sources"],
                     as_of=AS_OF, output=tmp_path / "d2",
                     invoices_dir=inv_dir, input_dir=tmp_path / "nope",
                     erp_snapshot=inputs["snapshot"])


def test_nested_raw_inventory(inputs, tmp_path):
    inv_dir = tmp_path / "raw"
    pdfs = tmp_path / "facturas"
    (inv_dir / "sub").mkdir(parents=True)
    (pdfs / "sub").mkdir(parents=True)
    nested = good_invoice(file_id="sub/good.pdf")
    (inv_dir / "sub" / "good.invoice.json").write_text(json.dumps(nested))
    pdf_bytes = b"%PDF-nested"
    (pdfs / "sub" / "good.pdf").write_bytes(pdf_bytes)
    summary = decide_files(
        rules=inputs["rules"], sources=inputs["sources"], as_of=AS_OF,
        output=tmp_path / "decision", invoices_dir=inv_dir, input_dir=pdfs,
        erp_snapshot=inputs["snapshot"],
    )
    assert summary["counts"]["PAGAR"] == 1
    assert summary["results"][0]["source_sha256"] == digest(pdf_bytes)


def test_input_dir_with_export_rejected(inputs, tmp_path):
    export_dir = _export(tmp_path)
    with pytest.raises(ValueError):
        decide_files(rules=inputs["rules"], sources=inputs["sources"],
                     as_of=AS_OF, output=tmp_path / "d",
                     export_dir=export_dir, input_dir=tmp_path,
                     erp_snapshot=inputs["snapshot"])


def test_replay_decisions_identical_after_source_deleted(inputs, tmp_path):
    export_dir = _export(tmp_path)
    _decide(export_dir, inputs, tmp_path)
    original = (tmp_path / "decision" / "outcomes.jsonl").read_bytes()
    inputs["sources"].unlink()
    (tmp_path / "master.xlsx").unlink()
    (export_dir / "manifest.json").unlink()
    from payments.io import replay_decisions

    replayed = replay_decisions(tmp_path / "decision", tmp_path / "replayed")
    assert replayed["outcomes_equal_source"] is True
    assert (tmp_path / "replayed" / "outcomes.jsonl").read_bytes() == original


def test_replay_tampered_inputs_rejected(inputs, tmp_path):
    _decide(_export(tmp_path), inputs, tmp_path)
    from payments.io import replay_decisions

    (tmp_path / "decision" / "inputs.json").write_text("[]")
    with pytest.raises(ValueError):
        replay_decisions(tmp_path / "decision", tmp_path / "replayed")


def test_replay_tampered_master_rejected(inputs, tmp_path):
    _decide(_export(tmp_path), inputs, tmp_path)
    from payments.io import replay_decisions

    master = json.loads((tmp_path / "decision" / "master.json").read_text())
    master["pedidos"]["PO1"]["importe_total"] = "1"
    (tmp_path / "decision" / "master.json").write_text(json.dumps(master))
    with pytest.raises(ValueError):
        replay_decisions(tmp_path / "decision", tmp_path / "replayed")


def test_replay_output_inside_bundle_rejected(inputs, tmp_path):
    _decide(_export(tmp_path), inputs, tmp_path)
    from payments.io import replay_decisions

    with pytest.raises(ValueError):
        replay_decisions(tmp_path / "decision", tmp_path / "decision" / "x")


def test_raw_uppercase_pdf_suffix_keeps_exact_id(inputs, tmp_path):
    inv_dir = tmp_path / "raw"
    pdfs = tmp_path / "facturas"
    (inv_dir / "Sub").mkdir(parents=True)
    (pdfs / "Sub").mkdir(parents=True)
    invoice = good_invoice(file_id="Sub/Foo.PDF")
    (inv_dir / "Sub" / "Foo.invoice.json").write_text(json.dumps(invoice))
    pdf_bytes = b"%PDF-upper"
    (pdfs / "Sub" / "Foo.PDF").write_bytes(pdf_bytes)
    summary = decide_files(
        rules=inputs["rules"], sources=inputs["sources"], as_of=AS_OF,
        output=tmp_path / "decision", invoices_dir=inv_dir, input_dir=pdfs,
        erp_snapshot=inputs["snapshot"],
    )
    assert summary["counts"]["PAGAR"] == 1
    row = summary["results"][0]
    assert row["file_id"] == "Sub/Foo.PDF"
    assert row["source_sha256"] == digest(pdf_bytes)


def test_safe_record_preserves_empty_error():
    from payments.io import _safe_record

    row = _safe_record({"file_id": "a.pdf", "status": "completed",
                        "error": {}, "invoice": None})
    assert row["error"] is None


def test_replay_unlisted_schema_rejected(inputs, tmp_path):
    _decide(_export(tmp_path), inputs, tmp_path)
    from payments.io import replay_decisions

    (tmp_path / "decision" / "schemas" / "extra.json").write_text("{}")
    with pytest.raises(ValueError):
        replay_decisions(tmp_path / "decision", tmp_path / "replayed")


def test_replay_missing_invoice_schema_hash_rejected(inputs, tmp_path):
    _decide(_export(tmp_path), inputs, tmp_path)
    from payments.io import replay_decisions

    config_path = tmp_path / "decision" / "decision-config.json"
    config = json.loads(config_path.read_text())
    del config["artifact_hashes"]["schemas"]["invoice"]
    config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        replay_decisions(tmp_path / "decision", tmp_path / "replayed")


def test_replay_schemas_symlink_root_rejected(inputs, tmp_path):
    _decide(_export(tmp_path), inputs, tmp_path)
    from payments.io import replay_decisions

    elsewhere = tmp_path / "elsewhere"
    (tmp_path / "decision" / "schemas").rename(elsewhere)
    (tmp_path / "decision" / "schemas").symlink_to(elsewhere)
    with pytest.raises(ValueError):
        replay_decisions(tmp_path / "decision", tmp_path / "replayed")
