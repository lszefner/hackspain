"""Congela en `phone_calls/datos/` lo que la centralita dira por telefono.

Por que existe: el motor de decision vive sobre Supabase, y Supabase no sirve
para atender una llamada. Por dos razones distintas y las dos definitivas.

La primera es practica: esta red bloquea el puerto de Postgres, y aunque no lo
bloqueara, una conversacion no puede depender de que el wifi de una sala
aguante entre turno y turno.

La segunda es de diseno: el motor **no se puede consultar por numero de
pedido**. No hay columna, ni indice, ni metodo -- el pedido vive dentro de un
artefacto JSON en Storage, alcanzable solo si ya sabes el identificador del
registro. Y el numero de pedido es lo unico que el proveedor dice en voz alta.

Asi que el trabajo se hace ANTES, una vez, y el resultado queda en disco
indexado por pedido. Durante la llamada no hay red, ni credenciales, ni base
de datos.

Tres etapas, y donde vive cada fuente:

  1. extraccion   PDF -> campos. La unica que gasta dinero (Helmcode). Se
                  persiste en un repositorio LOCAL, no en Supabase:
                  `ingestion.Pipeline` acepta que se le inyecten repositorio
                  y almacen, asi que no hace falta Postgres para extraer.
  2. snapshots    proveedores y pedidos del Excel de La Caja, y los asientos
                  del ERP. Las dos, offline.
  3. evaluacion   el motor de reglas, en proceso y sin red. De aqui salen la
                  decision, la evidencia regla a regla y los motivos ya
                  redactados en prosa.

Uso:

    make export                      # las tres de la demo
    python -m phone_calls.exportar --factura FA-5633_transportes.pdf
    python -m phone_calls.exportar --sin-extraer   # re-evalua lo ya extraido
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import io
import json
import re
import sys
import zlib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATOS = Path(__file__).parent / "datos"
CRUDO = DATOS / "extraccion"          # los `outcome`, para no re-extraer
FUENTES = RAIZ / "rules_ingestion" / "sources.yaml"
RULESET = RAIZ / "rules_ingestion" / "outcome" / "v3" / "balanced" / "rules.json"
ERP_PY = RAIZ / "caja" / "alberto_erp.py"

HOY = "2026-09-19"
CAPTURADO = f"{HOY}T10:00:00Z"

# Las tres llamadas de la demo. Elegidas por lo que ENSENAN: una que ya se
# pago, una con el importe descuadrado, y la del IBAN -- que es donde el
# agente se niega a hablar, y lo unico que el jurado no habra visto ya.
DEMO = [
    "2026-17547_suministros.pdf",   # PO-2026-0475 · el ERP dice PAGADA
    "FA-5077_electricidad.pdf",     # PO-2026-0493 · 12.874,40 contra 12.847,40
    "FA-5633_transportes.pdf",      # PO-2026-0494 · IBAN distinto del maestro
]


# ------------------------------------------------------------- snapshots
def asientos_erp() -> list[dict]:
    """Los 516 asientos, sin levantar el bridge del ERP.

    `caja/alberto_erp.py` lleva los datos dentro, en un CSV comprimido. Se
    leen de ahi y no por HTTP porque el bridge simula caidas y limita las
    peticiones a proposito: esta muy bien para demostrar resiliencia y muy
    mal para exportar 516 filas sin sobresaltos.

    Y trae `asiento_id`, `fecha_registro` e `importe_esperado`, que el
    cliente HTTP del repo descarta y que la centralita dice en voz alta.
    """
    src = ERP_PY.read_text(encoding="utf-8")
    bloque = re.search(r"_DATOS_ERP = \((.*?)\)\n", src, re.S)
    if not bloque:
        raise RuntimeError(f"no encuentro _DATOS_ERP en {ERP_PY}")
    blob = "".join(re.findall(r'"([^"]*)"', bloque.group(1)))
    return list(csv.DictReader(
        io.StringIO(zlib.decompress(base64.b64decode(blob)).decode("utf-8"))))


def maestro() -> tuple[dict, dict]:
    """Proveedores y pedidos del Excel. Sin red y sin Supabase."""
    from rules_ingestion import loader
    lk = loader.load(str(FUENTES)).lookups
    return lk["proveedores"], lk["pedidos"]


def notas_de_alberto() -> dict:
    """Lo que Alberto apunto a mano, que `sources.yaml` descarta a proposito.

    Las dos hojas estan en `ignore_sheets` porque son texto libre y no datos,
    y el loader no sabe leerlas. Para el telefono importan igual: una dice
    que PO-2026-0007 esta en revision manual, y otra que los de Guadaira
    llaman los viernes.
    """
    import openpyxl
    xlsx = next((RAIZ / "caja").glob("*.xlsx"))
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    def col_a(hoja: str) -> list[str]:
        if hoja not in wb.sheetnames:
            return []
        return [str(f[0]).strip() for f in wb[hoja].iter_rows(values_only=True)
                if f and f[0]]
    return {"generales": col_a("notas_alberto"),
            "en_revision": [t for t in col_a("pendiente_revisar")
                            if t.upper().startswith("PO-")]}


def snapshots():
    """Las fuentes que el evaluador tratara como autorizadas, congeladas.

    El historico va vacio y declarado completo. Es una afirmacion, no un
    descuido: no tenemos registro de pagos previos, y marcarlo incompleto
    haria escalar toda factura por cobertura insuficiente. El contrato de
    snapshots pide justo que esto sea explicito.
    """
    from rules_ingestion.decision_context import SourceSnapshot
    prov, ped = maestro()
    def snap(kind, auth, payload, scope):
        return SourceSnapshot(kind=kind, payload=payload, captured_at=CAPTURADO,
                              asserted_by="configured-source",
                              authoritative_for=tuple(auth), scope=scope,
                              availability="available", as_of=None)
    return {
        "suppliers": snap(
            "supplier_master",
            ["supplier.identity", "supplier.bank_details",
             "supplier.payment_terms", "supplier.active"],
            {"records": list(prov.values())},
            "hoja Proveedores del Excel de La Caja"),
        "orders": snap(
            "order_master",
            ["order.identity", "order.amount", "order.currency"],
            {"records": [dict(v, importe_total=str(v["importe_total"]),
                              currency="EUR") for v in ped.values()]},
            "hoja Pedidos_2026 del Excel de La Caja"),
        "erp": snap(
            "erp", ["order.payment_status"],
            {"complete": True, "records": asientos_erp()},
            "asientos embebidos en caja/alberto_erp.py"),
        **{k: snap("history", [f"history.{k}"],
                   {"complete": True, "kind": k, "records": []},
                   "sin historico de pagos disponible")
           for k in ("processed", "approved", "paid")},
    }


# ------------------------------------------------------------ extraccion
async def _extraer(file_ids: list[str]) -> dict[str, dict]:
    """PDF -> `outcome`. La unica etapa que llama a un proveedor de pago."""
    from backend import caja_paths
    from backend.local_backend import LocalRepository, LocalStorage
    from ingestion.config import credentials, settings
    from ingestion.contracts import Contracts
    from ingestion.pipeline import Pipeline
    from ingestion.storage import sha256_bytes

    facturas = Path(caja_paths.facturas())
    config = settings(interpreter="deepseek", dpi=200, concurrency=3,
                      ocr="helmcode-vision")
    secretos = credentials(config)
    repo = LocalRepository()
    almacen = LocalStorage(CRUDO / "objetos")
    contratos = Contracts(str(RAIZ / "benchmark" / "schemas"))
    # `settings()` no los trae y el Pipeline los exige: cada job se
    # identifica, entre otras cosas, por el hash del esquema con el que se
    # valido. Se anaden igual que en `backend/run_revision.py`.
    config["schema_hashes"], config["schemas"] = contratos.hashes, contratos.schemas

    manifiesto, originales = [], {}
    for i, nombre in enumerate(file_ids):
        pdf = facturas / nombre
        if not pdf.is_file():
            raise FileNotFoundError(f"no encuentro {pdf}")
        crudo = pdf.read_bytes()
        # `put` devuelve un StorageRef (dataclass), no un dict.
        originales[nombre] = almacen.put(crudo, "original", "application/pdf")
        manifiesto.append({"relative_path": nombre, "file_id": nombre,
                           "source_sha256": sha256_bytes(crudo), "ordinal": i,
                           "error": None})
    lote = repo.create_batch(manifiesto, config)
    for nombre, ref in originales.items():
        repo.register_input(lote["id"], nombre, file_name=nombre,
                            content_hash=ref.sha256, object_key=ref.object_key,
                            size_bytes=ref.byte_size)
    await Pipeline(repo, almacen, contratos, config, secretos).run(lote)

    fuera: dict[str, dict] = {}
    for fila in repo.results(lote["id"]):
        art = repo.get_artifact(fila["artifact_id"]) if fila.get("artifact_id") else None
        carga = (art or {}).get("payload")
        if not carga:
            continue
        # Un mismo fichero puede dejar varias filas si hubo reintentos; gana
        # la que llego a `completed`.
        previo = fuera.get(fila["file_name"])
        if previo is None or (previo.get("status") != "completed"
                              and carga.get("status") == "completed"):
            fuera[fila["file_name"]] = carga
    return fuera


def outcome_de(file_id: str, *, extraer: bool) -> dict | None:
    """El `outcome` de esa factura: de disco si ya esta, si no extrayendo."""
    guardado = CRUDO / f"{file_id}.json"
    if guardado.is_file():
        return json.loads(guardado.read_text(encoding="utf-8"))
    if not extraer:
        return None
    hechos = asyncio.run(_extraer([file_id]))
    out = hechos.get(file_id)
    if out and out.get("status") == "failed":
        raise RuntimeError(f"la extraccion de {file_id} fallo: "
                           f"{(out.get('error') or {}).get('code')}")
    if out:
        CRUDO.mkdir(parents=True, exist_ok=True)
        guardado.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


# ------------------------------------------------------------ evaluacion
def evaluar(outcome: dict, snaps) -> dict:
    """El motor de reglas, en proceso y sin red."""
    from rules_ingestion.decision_context import build_context
    from rules_ingestion.evaluation_cli import evaluation_packet
    from rules_ingestion.execution_context import prepare_context
    bundle = prepare_context(build_context(
        outcome, snapshots=snaps, ruleset=RULESET.read_bytes(),
        evaluation_date=HOY, captured_at=CAPTURADO))
    return evaluation_packet(bundle)


# --------------------------------------------------------------- export
def expediente(file_id: str, outcome: dict, paquete: dict, *,
               prov: dict, ped: dict, erp: dict, notas: dict) -> dict:
    """Lo que la centralita necesita para UNA llamada, ya resuelto.

    Se guarda plano y desnormalizado a proposito: en mitad de una llamada no
    hay tiempo -- ni ganas -- de cruzar tablas. El pedido es la clave porque
    es lo unico que el proveedor dice en voz alta.
    """
    ev = paquete["evaluation_result"]
    ctx = paquete["decision_context"]
    def campo(nombre):
        f = (ctx.get("fields") or {}).get(nombre) or {}
        return f.get("value") if f.get("state") == "present" else None

    pedido = campo("invoice.order_reference")
    nif = campo("invoice.supplier_tax_id")
    p = prov.get(nif) if nif else None
    if p is None and pedido and pedido in ped:
        # El NIF de la factura puede venir mal leido; el pedido es mas fiable
        # para saber a quien pertenece, y lo necesitamos para el cruce de
        # seguridad (no dar datos del pedido de otro).
        p = prov.get(ped[pedido].get("nif"))
    return {
        "pedido": pedido,
        "file_id": file_id,
        "decision": ev["preliminary_decision"],
        "evaluacion_completa": ev["completeness"]["evaluation_complete"],
        "aprobable": ev["completeness"]["approval_eligible"],
        "motivos": ev.get("decision_reasons") or [],
        "reglas": [{"rule_id": r["rule_id"], "status": r["status"],
                    "consecuencia": r.get("applied_consequence"),
                    "reason_codes": r.get("reason_codes") or [],
                    "explicacion": r.get("explanation"),
                    "entradas": [{"campo": i.get("field"), "estado": i.get("state"),
                                  "valor": i.get("value")}
                                 for i in (r.get("inputs") or [])]}
                   for r in ev["rule_results"]],
        "factura": {k: campo(f"invoice.{k}") for k in
                    ("number", "issue_date", "supplier_tax_id", "iban",
                     "total", "taxable_base", "vat_amount", "currency")},
        "proveedor": p,
        "pedido_maestro": (lambda r: dict(r, importe_total=str(r["importe_total"]))
                           if r else None)(ped.get(pedido or "")),
        "asiento": erp.get(pedido or ""),
        "notas": {"generales": notas["generales"],
                  "en_revision": (pedido or "") in notas["en_revision"]},
        "procedencia": {
            "context_id": ctx.get("context_id"),
            "evaluation_id": ev.get("evaluation_id"),
            "ruleset": ev.get("ruleset"),
            "evaluation_date": ev.get("evaluation_date"),
            "capturado_at": CAPTURADO,
        },
    }


def exportar(file_ids: list[str], *, extraer: bool = True) -> dict:
    prov_por_id, ped = maestro()
    prov = {v["nif"]: v for v in prov_por_id.values()}
    erp = {r["pedido"]: r for r in asientos_erp()}
    notas = notas_de_alberto()
    snaps = snapshots()
    DATOS.mkdir(parents=True, exist_ok=True)

    hechos, fallos = [], []
    for file_id in file_ids:
        out = outcome_de(file_id, extraer=extraer)
        if out is None:
            fallos.append((file_id, "sin extraccion (usa --extraer)"))
            continue
        try:
            paquete = evaluar(out, snaps)
        except Exception as exc:                            # noqa: BLE001
            fallos.append((file_id, f"{type(exc).__name__}: {exc}"))
            continue
        exp = expediente(file_id, out, paquete, prov=prov, ped=ped, erp=erp,
                         notas=notas)
        if not exp["pedido"]:
            fallos.append((file_id, "la extraccion no trae numero de pedido"))
            continue
        (DATOS / f"{exp['pedido']}.json").write_text(
            json.dumps(exp, ensure_ascii=False, indent=1), encoding="utf-8")
        hechos.append(exp)

    (DATOS / "maestro.json").write_text(json.dumps(
        {"proveedores": list(prov_por_id.values()), "notas": notas,
         "capturado_at": CAPTURADO}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"exportadas": [{"pedido": e["pedido"], "file_id": e["file_id"],
                            "decision": e["decision"]} for e in hechos],
            "fallos": fallos, "datos": str(DATOS)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser("phone_calls.exportar")
    p.add_argument("--factura", action="append", default=None,
                   help="repetible; por defecto las tres de la demo")
    p.add_argument("--sin-extraer", action="store_true",
                   help="solo re-evalua lo que ya este extraido en disco")
    a = p.parse_args(argv)
    r = exportar(a.factura or DEMO, extraer=not a.sin_extraer)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    for file_id, motivo in r["fallos"]:
        print(f"  ! {file_id}: {motivo}", file=sys.stderr)
    return 0 if r["exportadas"] and not r["fallos"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
