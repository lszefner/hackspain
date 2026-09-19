"""De un CSV o un Excel a una norma que el motor de este repo entiende.

Corre en tiempo de AUTORIA. Produce un `norma_vN.yaml` y una politica; a
partir de ahi el motor es el de siempre y la decision sigue siendo funcion
pura de un fichero congelado. **El clasificador nunca esta en el camino de
decidir**: si se cae, no hay ninguna decision afectada.

El trabajo de verdad esta en el mapeo, porque los dos vocabularios no son
1:1: `main` agrupa en 6 reglas canonicas lo que aqui son 5 tipos de motor
mas un caso de politica.

    VENDOR         -> R1_nif_iban        (tipo nif_iban)
    AMOUNT         -> R2_pedido + R3_iva (aqui van separadas)
    DATES          -> R4_fecha           (+ R6_vencimiento si hay plazos)
    DUPLICATES     -> R5_estado_erp      (parcial: no detecta n de factura repetido)
    MISSING        -> no es regla: es `datos_incompletos` en la politica
    AUTHORIZATION  -> no existe aqui. Se avisa, no se inventa.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).parent
PERFILES = RAIZ / "perfiles"
FUENTES = RAIZ / "sources.yaml"

# canonica -> las reglas del motor que la representan, en orden
MAPEO: dict[str, list[dict]] = {
    "VENDOR": [{
        "id": "R1_nif_iban", "tipo": "nif_iban",
        "requiere": ["nif_emisor", "iban"],
        "descripcion": "El NIF esta en el maestro y el IBAN de la factura coincide",
    }],
    "AMOUNT": [
        {"id": "R2_pedido", "tipo": "pedido_importe",
         "requiere": ["pedido", "total"], "campo_importe": "total",
         "descripcion": "El pedido existe, es del proveedor y el importe coincide",
         "_param_tolerancia": "tolerance_eur"},
        {"id": "R3_iva", "tipo": "iva",
         "requiere": ["base", "iva_importe", "total"], "iva_estandar": "21",
         "descripcion": "El IVA esta bien calculado y el total es base mas IVA",
         "_param_tolerancia": "tolerance_eur"},
    ],
    "DATES": [{
        "id": "R4_fecha", "tipo": "fecha", "requiere": ["fecha"],
        "descripcion": "La fecha es valida y no futura",
    }],
    "DUPLICATES": [{
        "id": "R5_estado_erp", "tipo": "estado_erp", "requiere": ["pedido"],
        "estado_pagable": "PENDIENTE",
        "descripcion": "El pedido esta PENDIENTE en el ERP (nunca pagar dos veces)",
    }],
}

# Regla extra que solo aparece si el perfil exige plazos de pago. El motor ya
# tiene el tipo `vencimiento` y el maestro trae condiciones_dias.
VENCIMIENTO = {
    "id": "R6_vencimiento", "tipo": "vencimiento", "requiere": ["fecha"],
    "descripcion": "La factura esta dentro del plazo de pago del proveedor",
}

SIN_EQUIVALENTE = {"AUTHORIZATION"}


# --------------------------------------------------------------- ingesta
def ingerir(*, excel: Path | None = None, carpeta: Path | None = None,
            perfil: str = "balanced", version: str = "v4",
            usar_llm: bool = False, usar_jev: bool = False) -> dict:
    """Lee las fuentes, clasifica cada linea y devuelve el ruleset.

    `usar_jev` y `usar_llm` van a False por defecto A PROPOSITO: el nivel
    lexico es determinista, no necesita red ni claves, y una norma que
    depende de que un proveedor responda no es una norma.
    """
    from .portado import classify, loader, merge, store

    cfg = yaml.safe_load(FUENTES.read_text("utf-8"))
    if excel:
        cfg["workbook"]["path"] = str(Path(excel).resolve())
    tmp = RAIZ / ".sources.efectivo.yaml"
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    try:
        carga = loader.load(str(tmp), base_dir=str(RAIZ))
    finally:
        tmp.unlink(missing_ok=True)

    declaradas = classify.classify_lines(
        [{"text": l["text"], "declared": True, **l} for l in carga.norma_lines],
        prefer_jev=usar_jev, use_llm=usar_llm)

    perfil_d = yaml.safe_load((PERFILES / f"{perfil}.yaml").read_text("utf-8"))
    perfil_d["ruleset_version"] = version
    doc = store.build_ruleset(carga, declaradas, perfil_d,
                              _dt.datetime.now().isoformat(timespec="seconds"))

    descubiertas: list[dict] = []
    if carpeta:
        # `classify_lines` devuelve pares (linea, resultado): la linea trae
        # de que fichero, hoja y celda salio, y eso es la procedencia.
        for linea, res in classify.classify_lines(
                loader.scan_candidates(str(carpeta)),
                prefer_jev=usar_jev, use_llm=usar_llm):
            descubiertas.append({**{k: linea.get(k) for k in
                                    ("file", "sheet", "cell", "text")},
                                 **res.to_dict()})

    return merge.build_merged(doc, {"all": descubiertas} if descubiertas else None)


# -------------------------------------------------------------- exportar
def exportar_norma(ruleset: dict, *, version: str = "v4") -> tuple[dict, dict, list[str]]:
    """ruleset -> (norma, politica, avisos) en el esquema que lee `Motor`."""
    activas = {r["canonical"]: r for r in ruleset["rules"]
               if r.get("canonical") and r.get("enabled")}
    metricas = ruleset.get("metrics") or {}
    avisos: list[str] = []

    reglas: list[dict] = []
    mapeo: dict[str, dict] = {}
    for canonica, plantillas in MAPEO.items():
        origen = activas.get(canonica)
        if origen is None:
            avisos.append(f"{canonica} desactivada en el perfil: sus reglas no se emiten")
            continue
        params = origen.get("params") or {}
        for plantilla in plantillas:
            regla = {k: v for k, v in plantilla.items() if not k.startswith("_")}
            clave_tol = plantilla.get("_param_tolerancia")
            if clave_tol and params.get(clave_tol) is not None:
                regla["tolerancia"] = f"{float(params[clave_tol]):.2f}"
            regla["fuente"] = _fuente(origen)
            reglas.append(regla)
            mapeo[regla["id"]] = {"FALLA": origen.get("on_fail", "ESCALAR")}

    if metricas.get("enforce_payment_terms") and "DATES" in activas:
        regla = dict(VENCIMIENTO, fuente=_fuente(activas["DATES"]))
        reglas.append(regla)
        mapeo[regla["id"]] = {"FALLA": activas["DATES"].get("on_fail", "ESCALAR")}

    for canonica in SIN_EQUIVALENTE & set(activas):
        avisos.append(
            f"{canonica} esta activa en el perfil y este motor no la sabe aplicar. "
            f"No se emite: mejor que falte a que parezca que se comprueba.")

    nuevas = [r for r in ruleset["rules"] if not r.get("canonical")]
    if nuevas:
        avisos.append(
            f"{len(nuevas)} regla(s) nueva(s) descubierta(s) que el motor no sabe "
            f"aplicar todavia. Se anotan en el YAML sin activar.")

    norma = {
        "version": version,
        "_autoria": {
            "generado_at": ruleset.get("generated_at"),
            "perfil": ruleset.get("policy_id"),
            "clasificador": _tiers(ruleset),
            "fuente": "alberto reglas — NO editar a mano sin regenerar",
        },
        "reglas": reglas,
    }
    if nuevas:
        # Fuera de `reglas:` a proposito: el motor las ignora y nadie las
        # activa por accidente, pero no se pierden.
        norma["reglas_nuevas_sin_aplicar"] = [{
            "id": r["id"], "texto": r.get("text"),
            "confianza": (r.get("judge") or {}).get("maps_to_conf"),
            "metodo": r.get("method"),
            "origen": [f"{s.get('file')}:{s.get('sheet')}:{s.get('cell')}"
                       for s in (r.get("source_refs") or [])][:3],
        } for r in nuevas]

    politica = {
        "por_defecto": "PAGAR",
        "mapeo": mapeo,
        "datos_incompletos": (activas.get("MISSING") or {}).get("on_fail", "ESCALAR"),
        "iva_no_estandar": "ESCALAR",
        "marcado_revision": "ESCALAR",
        "documento_ilegible": "ESCALAR",
    }
    return norma, politica, avisos


def _fuente(regla: dict) -> str:
    refs = regla.get("source_refs") or []
    if not refs:
        return "predefinida"
    r = refs[0]
    return f"{r.get('sheet') or r.get('file')}:{r.get('cell')}"


def _tiers(ruleset: dict) -> list[str]:
    return sorted({r.get("method") for r in ruleset["rules"] if r.get("method")})


def escribir(norma: dict, politica: dict, *, destino: Path,
             version: str = "v4") -> dict:
    """Escribe norma_vN.yaml y politica_vN.yaml junto a los que ya existen."""
    cabecera = (
        f"# GENERADO por `alberto reglas`. Su huella entra en la clave de cada\n"
        f"# decision, asi que regenerarlo produce una norma nueva y las\n"
        f"# decisiones anteriores NO se sobrescriben.\n")
    ruta_n = destino / f"norma_{version}.yaml"
    ruta_p = destino / f"politica_{version}.yaml"
    ruta_n.write_text(cabecera + yaml.safe_dump(norma, allow_unicode=True,
                                                sort_keys=False), encoding="utf-8")
    ruta_p.write_text(cabecera + yaml.safe_dump(politica, allow_unicode=True,
                                                sort_keys=False), encoding="utf-8")
    return {"norma": str(ruta_n), "politica": str(ruta_p),
            "reglas": len(norma["reglas"]),
            "nuevas_sin_aplicar": len(norma.get("reglas_nuevas_sin_aplicar", []))}
