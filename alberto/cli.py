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


def _caja_por_defecto() -> Path:
    entorno = os.environ.get("ALBERTO_CAJA")
    if entorno:
        return Path(entorno)
    if Path("caja/facturas").is_dir():
        return Path("caja")
    return Path(".")


def _cfg_vision(verificar: bool) -> dict:
    from alberto.extraccion.vision.ajustes import ajustes
    return ajustes(verificar=verificar)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser("alberto", description="Pipeline de decision de pago")
    p.add_argument("--db", type=Path, default=RUTA_DB)
    # La Caja vive en la raiz del repo (consistencia con main), pero se
    # respeta a quien la tenga clonada aparte: ALBERTO_CAJA primero, luego
    # ./caja si existe, y si no la raiz, que es donde esta facturas/.
    p.add_argument("--caja", type=Path, default=_caja_por_defecto())
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
    v = sub.add_parser("vision", help="fase 2: lee con el modelo lo que la fase 1 dejo incompleto")
    v.add_argument("--limite", type=int, help="procesa solo los N primeros")
    v.add_argument("--concurrencia", type=int, default=4)
    v.add_argument("--dpi", type=int, choices=[200, 300], default=200)
    v.add_argument("--en-seco", action="store_true",
                   help="rasteriza y persiste los PNG, sin llamar al modelo")
    v.add_argument("--verificar", action="store_true",
                   help="segunda pasada del modelo: mas caro, hasta 7 llamadas/pagina")
    v.add_argument("--max-intentos", type=int, default=2)
    v.add_argument("--raw", type=Path, default=Path("raw"))
    rg = sub.add_parser("reglas", help="de un CSV o Excel a una norma que el motor entiende")
    rg.add_argument("--excel", type=Path, help="el libro con la hoja de la norma")
    rg.add_argument("--descubrir", type=Path, metavar="CARPETA",
                    help="barre CSV y xlsx buscando reglas escondidas en texto suelto")
    rg.add_argument("--perfil", default="strict",
                    choices=["conservative", "balanced", "strict"])
    rg.add_argument("--version", default="v4")
    rg.add_argument("--con-jev", action="store_true",
                    help="usa el clasificador remoto; por defecto, el lexico offline")
    rg.add_argument("--en-seco", action="store_true", help="no escribe los YAML")
    wb = sub.add_parser("web", help="sirve la API de revision desde la BD")
    wb.add_argument("--puerto", type=int, default=8010)
    wb.add_argument("--norma", default="v3")
    wb.add_argument("--instantanea", type=Path, metavar="FICHERO",
                    help="exporta el JSON estatico del frontend y sale")
    sub.add_parser("coste", help="coste y latencia por via, desde la BD")
    au = sub.add_parser("audita", help="vuelve a tomar cada decision y comprueba que sale igual")
    au.add_argument("--norma", default=None, help="v3 o v3@7f3a1c; por defecto, todas")
    au.add_argument("--limite", type=int)
    w = sub.add_parser("valida", help="¿entrega el extractor los campos que piden las reglas?")
    w.add_argument("--norma", default="v3")
    w.add_argument("--congelar", action="store_true",
                   help="fija la cobertura actual como referencia de no regresion")
    a = args = p.parse_args(argv)
    if a.cmd not in ("valida", "vision", "explica", "estado", "audita", "coste", "web") and not a.caja.is_dir():
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
    if a.cmd == "vision":
        from alberto.extraccion.vision.ajustes import ProveedorNoConfigurado
        try:
            ver(pipeline.extraer_con_vision(
                con, lote=a.lote, concurrencia=a.concurrencia, dpi=a.dpi,
                limite=a.limite, max_intentos=a.max_intentos,
                en_seco=a.en_seco, dir_raw=a.raw,
                cfg=None if a.en_seco else _cfg_vision(a.verificar)))
        except ProveedorNoConfigurado as exc:
            p.error(f"{exc}\n         "
                    f"(`alberto vision --en-seco` no necesita credenciales)")
        return 0
    if a.cmd == "reglas":
        from alberto.reglas.autoria import exportar_norma, ingerir
        from alberto.reglas.autoria.exportador import escribir
        excel = a.excel or next(a.caja.glob("*.xlsx"), None) if a.caja.is_dir() else a.excel
        if excel is None:
            p.error("no encuentro el Excel: pasa --excel RUTA")
        rs = ingerir(excel=excel, carpeta=a.descubrir, perfil=a.perfil,
                     version=a.version, usar_jev=a.con_jev, usar_llm=a.con_jev)
        norma, politica, avisos = exportar_norma(rs, version=a.version)
        informe = {"perfil": a.perfil, "reglas": [r["id"] for r in norma["reglas"]],
                   "clasificador": norma["_autoria"]["clasificador"],
                   "avisos": avisos}
        if not a.en_seco:
            informe |= escribir(norma, politica,
                                destino=Path("alberto/reglas"), version=a.version)
            informe["siguiente"] = f"alberto decide --norma {a.version}"
        ver(informe)
        return 0
    if a.cmd == "web":
        from alberto.web import datos as wdatos
        from alberto.web.servidor import servir
        if a.instantanea:
            # OJO: no llamarla `snap`. El modulo importa `snapshot as snap`
            # arriba, y una asignacion aqui lo convierte en local de main():
            # `alberto decide` moria con UnboundLocalError sin tocar la web.
            instante = wdatos.instantanea(con, lote=a.lote)
            a.instantanea.parent.mkdir(parents=True, exist_ok=True)
            a.instantanea.write_text(
                json.dumps(instante, ensure_ascii=False, default=str), encoding="utf-8")
            ver({"instantanea": str(a.instantanea),
                 "facturas": len(instante["facturas"]),
                 "bytes": a.instantanea.stat().st_size})
            return 0
        return servir(a.db, puerto=a.puerto, lote=a.lote, caja=a.caja,
                      norma=a.norma)
    if a.cmd == "coste":
        from alberto import validacion
        return validacion.informe_coste(con, lote=a.lote)
    if a.cmd == "audita":
        from alberto import auditoria
        return auditoria.informe(con, norma=a.norma, limite=a.limite)
    if a.cmd == "valida":
        from alberto import validacion
        if a.congelar:
            n = validacion.congelar(con, lote=a.lote)
            ver({"congelado": n, "ruta": str(validacion.RUTA_ORO)})
            return 0
        return validacion.informe(con, lote=a.lote, norma=a.norma)
    if a.cmd == "explica":
        return mod_explica.explicar(con, a.fichero, norma=a.norma)
    if a.cmd == "estado":
        ver({r["estado"]: r["n"] for r in con.execute(
            "SELECT estado, count(*) n FROM documentos GROUP BY estado")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
