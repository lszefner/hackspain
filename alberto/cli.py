"""CLI del pipeline. Los verbos son lo que se ensena en la defensa."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx

from alberto import explica as mod_explica
from alberto import pipeline, salida
from alberto.db import RUTA_DB, conectar
from alberto.erp import snapshot as snap
from alberto.ingesta import registrar


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser("alberto", description="Pipeline de decision de pago")
    p.add_argument("--db", type=Path, default=RUTA_DB)
    # Cada uno tiene la Caja donde quiere: ALBERTO_CAJA lo resuelve sin editar nada.
    p.add_argument("--caja", type=Path,
                   default=Path(os.environ.get("ALBERTO_CAJA", "caja")))
    p.add_argument("--erp-url",
                   default=os.environ.get("ALBERTO_ERP", "http://127.0.0.1:8009"))
    p.add_argument("--lote", default="lote1")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingesta")
    sub.add_parser("snapshot")
    sub.add_parser("maestro")
    sub.add_parser("extrae").add_argument("--forzar", action="store_true")
    sub.add_parser("decide").add_argument("--norma", default="v3")
    e = sub.add_parser("emite")
    e.add_argument("--salida", type=Path, default=Path("outcomes.jsonl"))
    e.add_argument("--norma", default="v3"); e.add_argument("--traza", action="store_true")
    t = sub.add_parser("todo"); t.add_argument("--norma", default="v3")
    t.add_argument("--salida", type=Path, default=Path("outcomes.jsonl"))
    x = sub.add_parser("explica", help="sigue UNA decision de principio a fin")
    x.add_argument("fichero"); x.add_argument("--norma", default="v3")
    sub.add_parser("estado")
    a = args = p.parse_args(argv)
    if not a.caja.is_dir():
        p.error(f"no encuentro la Caja en {a.caja}. Clonala y pasa --caja RUTA "
                f"o exporta ALBERTO_CAJA=RUTA")
    con = conectar(args.db)

    def ver(x): print(json.dumps(x, ensure_ascii=False, indent=2, default=str))

    if a.cmd in ("ingesta", "todo"):
        ver({"ingesta": len(registrar(con, a.caja / "facturas", lote=a.lote))})
    if a.cmd in ("snapshot", "todo"):
        try:
            ver({"snapshot_erp": pipeline.sincronizar_erp(con, a.erp_url)})
        except httpx.ConnectError:
            p.error(f"el bridge ERP no responde en {a.erp_url}.\n"
                    f"         Levantalo en otro terminal:  cd {a.caja} && make erp\n"
                    f"         (o make erp-fast para quitarle la latencia de 2009)")
    if a.cmd in ("maestro", "todo"):
        xl = next(a.caja.glob("*.xlsx"))
        ver({"snapshot_maestro": pipeline.cargar_maestro(con, xl)})
    if a.cmd in ("extrae", "todo"):
        ver(pipeline.extraer(con, lote=a.lote, forzar=getattr(a, "forzar", False)))
    if a.cmd in ("decide", "todo"):
        sid = snap.ultimo(con)
        mid = con.execute("SELECT version_id FROM maestro_versiones"
                          " ORDER BY creado_at DESC LIMIT 1").fetchone()["version_id"]
        ver(pipeline.decidir(con, snapshot_erp=sid, snapshot_maestro=mid,
                             norma=getattr(a, "norma", "v3"), lote=a.lote))
    if a.cmd in ("emite", "todo"):
        r = salida.escribir_jsonl(con, a.salida, lote=a.lote,
                                  norma=getattr(a, "norma", "v3"),
                                  con_traza=getattr(a, "traza", False))
        ver(r | {"verificacion": salida.verificar(a.salida, r["escritos"])})
    if a.cmd == "explica":
        return mod_explica.explicar(con, a.fichero, norma=a.norma)
    if a.cmd == "estado":
        ver({r["estado"]: r["n"] for r in con.execute(
            "SELECT estado, count(*) n FROM documentos GROUP BY estado")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
