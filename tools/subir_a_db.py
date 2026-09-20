"""Persiste las decisiones en Supabase por el camino canonico, en tandas.

    python -m tools.subir_a_db --facturas caja/facturas --prefijo entrega-l1

Cada tanda es una `run` con su propia `request_key`, asi que una caida de
proveedor cuesta una tanda y no la corrida entera; repetir la misma clave
recupera el estado en vez de volver a pagar el trabajo.

El historial de duplicados se acumula entre tandas: `run_revision` construye
`processed_history_snapshot` desde lo ya persistido, asi que las ultimas ven a
las primeras. Por eso las tandas van en orden y no en paralelo.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    from backend.run_revision import revisar_lote_sync
    from rules_ingestion.engine import RuleSource

    ap = argparse.ArgumentParser(prog="python -m tools.subir_a_db")
    ap.add_argument("--facturas", required=True)
    ap.add_argument("--prefijo", required=True, help="prefijo de la request_key")
    ap.add_argument("--evaluation-date", default="2026-09-19")
    ap.add_argument("--ruleset", default="rules_ingestion/outcome/v3/balanced/rules.json")
    ap.add_argument("--tanda", type=int, default=40)
    ap.add_argument("--desde", type=int, default=0, help="salta las N primeras facturas")
    ap.add_argument("--limite", type=int, default=None)
    args = ap.parse_args(argv)

    pdfs = sorted(p.name for p in Path(args.facturas).glob("*.pdf"))[args.desde:]
    if args.limite:
        pdfs = pdfs[:args.limite]
    if not pdfs:
        print(f"no hay PDFs en {args.facturas}", file=sys.stderr)
        return 2

    # El tipo por defecto de RuleSource es application/octet-stream, que es con
    # el que el almacen ya guardo estos mismos bytes. Adivinar el mimetype por
    # la extension choca con esos metadatos y aborta la corrida.
    fuentes = [
        RuleSource("workbook.xlsx", Path("caja/FINAL_v7_DEFINITIVO_ahorasi.xlsx").read_bytes()),
        RuleSource("sources.yaml", Path("rules_ingestion/sources.yaml").read_bytes()),
    ]

    total = len(pdfs)
    print(f"{total} facturas en tandas de {args.tanda} -> {args.prefijo}", file=sys.stderr)
    t0 = time.time()
    hechas = fallidas = 0
    for i in range(0, total, args.tanda):
        grupo = pdfs[i:i + args.tanda]
        clave = f"{args.prefijo}-{i // args.tanda:03d}"
        marca = time.time()
        try:
            resumen = revisar_lote_sync(
                grupo, request_key=clave, evaluation_date=args.evaluation_date,
                input_dir=args.facturas, ruleset_path=args.ruleset,
                rule_sources=fuentes)
        except Exception as exc:  # noqa: BLE001 - una tanda caida no tumba el resto
            fallidas += len(grupo)
            print(f"  {clave}: FALLO {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
            continue
        conteo = resumen.get("revision_counts", {})
        hechas += conteo.get("stored", 0)
        fallidas += conteo.get("failed", 0)
        transcurrido = time.time() - marca
        restantes = total - (i + len(grupo))
        eta = (time.time() - t0) / max(i + len(grupo), 1) * restantes
        print(f"  {clave}: {resumen.get('state')} · {conteo} · {transcurrido:.0f}s"
              f" · faltan {restantes} (~{eta/60:.0f} min)", file=sys.stderr)

    print(f"total: {hechas} persistidas, {fallidas} con error, "
          f"{(time.time()-t0)/60:.0f} min", file=sys.stderr)
    return 0 if fallidas == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
