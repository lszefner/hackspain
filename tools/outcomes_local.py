"""De una carpeta de PDFs al JSONL de entrega, sin Postgres.

    python -m tools.outcomes_local --facturas caja/facturas --out outcomes.jsonl

Extrae por la cascada (texto nativo primero, vision solo si hace falta),
evalua con el ruleset congelado y escribe `{"file_id", "result"}` por fichero.
Con `--format traced` escribe ademas por que, regla a regla.

Politica de salida, explicita porque es la parte que decide:
  * el veredicto lo da el evaluador (PAGAR / ESCALAR / NO_PAGAR);
  * un fichero que no llega a evaluarse sale ESCALAR, nunca PAGAR: un fallo
    de extraccion no autoriza un pago.
Es la misma postura que `backend/export_outcomes.decide_output`, menos la
revision contextual, que este camino no ejecuta.
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VOCABULARIO = ("PAGAR", "ESCALAR", "NO_PAGAR")


def _snapshots(sources_yaml: str, captured_at: str, erp: str, erp_url: str):
    from backend.master_data import ErpClient, capture_erp_snapshot, capture_master_snapshots
    from rules_ingestion.decision_context import SourceSnapshot

    snaps = capture_master_snapshots(sources_yaml, captured_at=captured_at)
    if erp == "bridge":
        # El lote 2 actualiza el ERP en caliente (`alberto_erp.py --lote2`), asi
        # que hay que preguntarle al puente, no al volcado embebido.
        snaps["erp"] = capture_erp_snapshot(ErpClient(erp_url), captured_at=captured_at)
    else:
        from phone_calls.exportar import asientos_erp
        snaps["erp"] = SourceSnapshot(
            kind="erp", payload={"records": asientos_erp(), "complete": True},
            captured_at=captured_at, asserted_by="asientos embebidos en alberto_erp.py",
            scope="lote1", authoritative_for=("order.payment_status",),
            availability="available")
    for kind in ("processed", "approved", "paid"):
        snaps[kind] = SourceSnapshot(
            kind="history", payload={"kind": kind, "records": [], "complete": True},
            captured_at=captured_at, asserted_by="sin historico de pagos",
            scope="declarado vacio y completo",
            authoritative_for=(f"history.{kind}",), availability="available")
    return snaps


def _determinista(pdf: Path, contratos):
    """El `outcome` por la via determinista, o None si no la pasa.

    None significa dos cosas distintas y las dos llevan al mismo sitio: el PDF
    no trae capa de texto, o la trae pero el gate no acepta lo extraido (el
    lote 2 llega en cinco idiomas y con las fechas escritas con letras). En
    ambos casos decide la vision, que es justo para lo que esta la cascada.
    """
    from ingestion.deterministic import accept, extract, native_reading
    from ingestion.normalization import normalize_invoice
    from ingestion.pdf import extract_text
    from ingestion.validation import validate_interpretation

    reading = native_reading(pdf.name, extract_text(pdf.read_bytes()))
    if reading is None:
        return None
    campos = extract(reading)
    invoice = normalize_invoice(campos["invoice"])
    invoice["file_id"], invoice["schema_version"] = pdf.name, "0.1"
    try:
        contratos.validate("invoice", invoice)
    except Exception:  # noqa: BLE001 - lo que no valida, a vision
        return None
    checks = validate_interpretation(invoice, campos["evidence"], reading, contratos)
    if not accept(invoice, checks)[0]:
        return None
    return {"file_id": pdf.name, "status": "completed", "invoice": invoice,
            "evidence": campos["evidence"], "reading": reading, "checks": checks}


def _vision(pendientes: list[Path], cache: Path, tanda: int = 3) -> None:
    """Extrae con vision + interpretacion lo que la via determinista no cubrio.

    Cachea en disco: repetir la corrida no vuelve a pagar OCR.
    """
    import asyncio

    from backend.local_backend import LocalRepository, LocalStorage
    from ingestion.config import credentials, settings
    from ingestion.contracts import Contracts
    from ingestion.pipeline import Pipeline
    from ingestion.storage import sha256_bytes

    async def lote(pdfs):
        config = settings(interpreter="deepseek", dpi=200, concurrency=3,
                          ocr="helmcode-vision")
        contratos = Contracts(str(ROOT / "benchmark" / "schemas"))
        config["schema_hashes"], config["schemas"] = contratos.hashes, contratos.schemas
        repo, almacen = LocalRepository(), LocalStorage(cache / "objetos")
        manifiesto, originales = [], {}
        for i, pdf in enumerate(pdfs):
            crudo = pdf.read_bytes()
            originales[pdf.name] = almacen.put(crudo, "original", "application/pdf")
            manifiesto.append({"relative_path": pdf.name, "file_id": pdf.name,
                               "source_sha256": sha256_bytes(crudo), "ordinal": i,
                               "error": None})
        batch = repo.create_batch(manifiesto, config)
        for nombre, ref in originales.items():
            repo.register_input(batch["id"], nombre, file_name=nombre,
                                content_hash=ref.sha256, object_key=ref.object_key,
                                size_bytes=ref.byte_size)
        await Pipeline(repo, almacen, contratos, config, credentials(config)).run(batch)
        salida = {}
        for fila in repo.results(batch["id"]):
            art = repo.get_artifact(fila["artifact_id"]) if fila.get("artifact_id") else None
            carga = (art or {}).get("payload")
            if not carga:
                continue
            previo = salida.get(fila["file_name"])
            if previo is None or (previo.get("status") != "completed"
                                  and carga.get("status") == "completed"):
                salida[fila["file_name"]] = carga
        return salida

    cache.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(pendientes), tanda):
        grupo = pendientes[i:i + tanda]
        print(f"  vision: {[p.name for p in grupo]}", file=sys.stderr)
        try:
            hechos = asyncio.run(lote(grupo))
        except Exception as exc:  # noqa: BLE001 - una tanda caida no tumba el resto
            print(f"    tanda fallida: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        for nombre, outcome in hechos.items():
            (cache / f"{nombre}.json").write_text(
                json.dumps(outcome, ensure_ascii=False), encoding="utf-8")


def _extraer(pdf: Path, contratos, cache: Path):
    """Determinista si pasa el gate; si no, la extraccion de vision cacheada."""
    outcome = _determinista(pdf, contratos)
    if outcome is not None:
        return outcome
    guardado = cache / f"{pdf.name}.json"
    return json.loads(guardado.read_text(encoding="utf-8")) if guardado.is_file() else None


def _decidir(outcome, snaps, ruleset, evaluation_date, captured_at):
    from rules_ingestion.decision_context import build_context
    from rules_ingestion.evaluator import evaluate, validate_evaluation
    from rules_ingestion.execution_context import prepare_context

    bundle = prepare_context(build_context(
        outcome, snapshots=snaps, ruleset=ruleset,
        evaluation_date=evaluation_date, captured_at=captured_at))
    resultado = evaluate(bundle).to_dict()
    validate_evaluation(resultado, bundle)
    return resultado


def verificar(rows, esperados: int) -> list[str]:
    """Las invariantes del verificador de la organizacion, antes de entregar."""
    problemas = []
    ids = [r["file_id"] for r in rows]
    for n, row in enumerate(rows, 1):
        if row["result"] not in VOCABULARIO:
            problemas.append(f"linea {n}: result {row['result']!r} fuera del vocabulario")
        if row["file_id"] != unicodedata.normalize("NFC", row["file_id"]):
            problemas.append(f"linea {n}: file_id no esta en NFC")
    duplicados = {x for x in ids if ids.count(x) > 1}
    if duplicados:
        problemas.append(f"file_id duplicados: {sorted(duplicados)[:5]}")
    if len(ids) != esperados:
        problemas.append(f"hay {len(ids)} lineas y se esperaban {esperados}")
    return problemas


def main(argv=None) -> int:
    from ingestion.contracts import Contracts

    ap = argparse.ArgumentParser(prog="python -m tools.outcomes_local")
    ap.add_argument("--facturas", default=None, help="carpeta de PDFs (por defecto, La Caja)")
    ap.add_argument("--out", default="outcomes.jsonl")
    ap.add_argument("--ruleset", default="rules_ingestion/outcome/v3/balanced/rules.json")
    ap.add_argument("--sources", default="rules_ingestion/sources.yaml")
    ap.add_argument("--evaluation-date", required=True)
    ap.add_argument("--captured-at", default="2026-09-19T10:00:00Z")
    ap.add_argument("--cache", default="phone_calls/datos/extraccion")
    ap.add_argument("--vision", action="store_true",
                    help="extrae con vision lo que la via determinista no cubre")
    ap.add_argument("--erp", choices=("blob", "bridge"), default="blob")
    ap.add_argument("--erp-url", default="http://127.0.0.1:8009",
                    help="puente ERP cuando --erp bridge")
    ap.add_argument("--format", choices=("deliverable", "traced"), default="deliverable")
    args = ap.parse_args(argv)

    if args.facturas is None:
        from backend import caja_paths
        args.facturas = str(caja_paths.facturas())
    pdfs = sorted(Path(args.facturas).glob("*.pdf"))
    if not pdfs:
        print(f"no hay PDFs en {args.facturas}", file=sys.stderr)
        return 2

    contratos = Contracts(str(ROOT / "benchmark" / "schemas"))
    snaps = _snapshots(args.sources, args.captured_at, args.erp, args.erp_url)
    ruleset = Path(args.ruleset).read_bytes()
    cache = Path(args.cache)

    if args.vision:
        pendientes = [pdf for pdf in pdfs
                      if _determinista(pdf, contratos) is None
                      and not (cache / f"{pdf.name}.json").is_file()]
        if pendientes:
            print(f"{len(pendientes)} factura(s) a vision", file=sys.stderr)
            _vision(pendientes, cache)

    rows, conteo, motivos = [], {}, {}
    for pdf in pdfs:
        file_id = unicodedata.normalize("NFC", pdf.name)
        traza = {"basis": None, "rules": None, "findings": None}
        try:
            outcome = _extraer(pdf, contratos, cache)
        except Exception as exc:  # noqa: BLE001 - un PDF ilegible no tumba el lote
            outcome, traza["basis"] = None, f"extraction_error:{type(exc).__name__}"
        if outcome is None:
            result, traza["basis"] = "ESCALAR", traza["basis"] or "sin_extraccion"
        elif outcome.get("status") == "failed" or not outcome.get("invoice"):
            result, traza["basis"] = "ESCALAR", f"extraccion_{outcome.get('status')}"
        else:
            try:
                ev = _decidir(outcome, snaps, ruleset, args.evaluation_date, args.captured_at)
                result, traza["basis"] = ev["preliminary_decision"], "evaluador"
                traza["rules"] = {r["rule_id"]: r["status"] for r in ev["rule_results"]}
                traza["findings"] = [f["code"] for f in ev["outstanding_findings"]
                                     if f["severity"] == "blocking"]
                # El status solo dice que R3 fallo; el motivo es la frase que
                # el evaluador ya escribio. Sin ella la traza no explica nada
                # a quien la lee, que es justo para lo que existe.
                traza["motivos"] = [
                    {"rule_id": r["rule_id"], "status": r["status"],
                     "explanation": r["explanation"]}
                    for r in ev["rule_results"] if r["status"] != "PASS"]
                # Una factura puede escalar con todas las reglas en PASS: el
                # motivo es entonces un hallazgo bloqueante, que vive fuera de
                # rule_results. Sin esto esas filas salen sin explicacion de
                # por que no se pagan. El hallazgo no trae texto, asi que se
                # anota donde apunta.
                traza["motivos"] += [
                    {"rule_id": None, "status": "BLOCKING_FINDING",
                     "code": f["code"],
                     # Una ref apunta con "pointer" o con "field", segun de
                     # donde salga; asumir solo una de las dos rompia la
                     # evaluacion de 17 de las 40.
                     "explanation": "Hallazgo bloqueante "
                                    + f["code"] + " en "
                                    + (", ".join(sorted({
                                        ref.get("pointer") or ref.get("field") or "/"
                                        for ref in f.get("refs") or []})) or "(sin referencia)")}
                    for f in ev["outstanding_findings"]
                    if f["severity"] == "blocking"]
            except Exception as exc:  # noqa: BLE001
                result, traza["basis"] = "ESCALAR", f"evaluation_error:{type(exc).__name__}"
        conteo[result] = conteo.get(result, 0) + 1
        motivos[traza["basis"]] = motivos.get(traza["basis"], 0) + 1
        rows.append({"file_id": file_id, "result": result, "trace": traza})

    problemas = verificar(rows, len(pdfs))
    salida = ([{"file_id": r["file_id"], "result": r["result"]} for r in rows]
              if args.format == "deliverable" else rows)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in salida)

    print(f"{len(rows)} lineas -> {args.out}", file=sys.stderr)
    print("resultados: " + ", ".join(f"{k}={v}" for k, v in sorted(conteo.items())), file=sys.stderr)
    print("origen: " + ", ".join(f"{k}={v}" for k, v in sorted(motivos.items())), file=sys.stderr)
    if problemas:
        print("CONTRATO NO VALIDADO:", file=sys.stderr)
        for p in problemas:
            print("  - " + p, file=sys.stderr)
        return 1
    print("contrato validado: una linea por PDF, file_id unico en NFC, vocabulario correcto",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
