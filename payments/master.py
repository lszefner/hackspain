from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from rules_ingestion import normalize as N
from rules_ingestion.loader import load

_REQUIRED_SNAPSHOT_FIELDS = ("id", "fecha", "proveedor", "nif", "pedido", "importe", "estado")

_PROVEEDOR_NORM = {"id": "text", "razon_social": "text", "nif": "nif", "iban": "iban",
                   "ciudad": "text", "condiciones": "text"}
_PEDIDO_NORM = {"pedido": "text", "proveedor_id": "text", "nif": "nif",
                "importe_total": "importe", "estado_excel": "text",
                "fecha_pedido": "fecha"}
_NORM = {
    "text": N.norm_text,
    "nif": N.norm_nif,
    "iban": N.norm_iban,
    "importe": N.norm_importe,
    "fecha": N.norm_fecha,
}
_AMOUNT_FIELDS = {"importe_total", "importe", "importe_esperado"}


def _valid_as_of(as_of) -> str:
    if not isinstance(as_of, str):
        raise TypeError("as_of must be an ISO date string")
    try:
        parsed = date.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError(f"as_of is not a valid ISO date: {as_of!r}") from exc
    if parsed.isoformat() != as_of:
        raise ValueError(f"as_of must be exactly YYYY-MM-DD: {as_of!r}")
    return as_of


def _finite(value, field: str, key: str):
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError(f"non-finite amount in {field} for key {key!r}")
    return value


def _normalize_rows(table: dict, norm_map: dict, key_field: str) -> dict:
    out = {}
    for raw_key, row in (table or {}).items():
        key = N.norm_text(raw_key)
        if not key:
            raise ValueError(f"blank key in master table for {key_field!r}")
        if key in out:
            raise ValueError(f"master key collision after normalization: {key!r}")
        record = dict(row)
        for col, kind in norm_map.items():
            if col in record:
                record[col] = _NORM[kind](record[col])
        row_key = N.norm_text(record.get(key_field))
        if row_key is None:
            record[key_field] = key
        elif row_key != key:
            raise ValueError(
                f"row {key_field} {row_key!r} does not match its table key {key!r}"
            )
        for col in _AMOUNT_FIELDS:
            record[col] = _finite(record.get(col), col, key)
        out[key] = record
    return out


def _iso_utc(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def _validate_snapshot(snapshot: dict) -> list[dict]:
    if not isinstance(snapshot, dict):
        raise TypeError("ERP snapshot must be an object")
    if snapshot.get("schema_version") != "1.0":
        raise ValueError("ERP snapshot schema_version must be '1.0'")
    if not isinstance(snapshot.get("source_url"), str):
        raise TypeError("ERP snapshot source_url must be a string")
    if not _iso_utc(snapshot.get("fetched_at")):
        raise ValueError("ERP snapshot fetched_at must be an ISO UTC timestamp")
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise TypeError("ERP snapshot records must be a list")
    seen = set()
    for i, row in enumerate(records):
        if not isinstance(row, dict):
            raise TypeError(f"ERP record {i} must be an object")
        for field in _REQUIRED_SNAPSHOT_FIELDS:
            if not isinstance(row.get(field), str):
                raise TypeError(f"ERP record {i} field {field!r} must be a string")
        rid = row["id"].strip()
        if not rid:
            raise ValueError(f"ERP record {i} has an empty id")
        if rid in seen:
            raise ValueError(f"ERP snapshot has duplicate record id {rid!r}")
        seen.add(rid)
    return records


def _erp_index(snapshot: dict | None):
    if snapshot is None:
        return {}, {}, {}
    by_pedido: dict[str, list[dict]] = {}
    for row in snapshot["records"]:
        pedido = N.norm_text(row["pedido"])
        if pedido is None:
            continue
        by_pedido.setdefault(pedido, []).append({
            "id": row["id"].strip(),
            "fecha": N.norm_fecha(row["fecha"]),
            "proveedor": N.norm_text(row["proveedor"]),
            "nif": N.norm_nif(row["nif"]),
            "pedido": pedido,
            "importe": _finite(N.norm_importe(row["importe"]), "importe", row["id"]),
            "estado": N.norm_text(row["estado"]),
        })
    erp_estado: dict[str, str] = {}
    conflicts: dict[str, str] = {}
    for pedido, rows in by_pedido.items():
        estados = {r["estado"] for r in rows}
        if any(r["estado"] == "PAGADA" for r in rows):
            erp_estado[pedido] = "PAGADA"
            continue
        if estados == {"PENDIENTE"}:
            identities = {(r["proveedor"], r["nif"]) for r in rows}
            amounts = {r["importe"] for r in rows}
            if len(identities) > 1 or len(amounts) > 1:
                conflicts[pedido] = (
                    "conflicting ERP accounting identities or amounts for order"
                )
            else:
                erp_estado[pedido] = "PENDIENTE"
            continue
        conflicts[pedido] = "ERP state absent or unknown"
    return by_pedido, erp_estado, conflicts


def master_from_lookups(proveedores: dict, pedidos: dict,
                        erp_snapshot: dict | None, *, as_of: str) -> dict:
    as_of = _valid_as_of(as_of)
    prov = _normalize_rows(proveedores or {}, _PROVEEDOR_NORM, "id")
    ped = _normalize_rows(pedidos or {}, _PEDIDO_NORM, "pedido")

    by_nif: dict[str, list[str]] = {}
    for vid, row in prov.items():
        nif = row.get("nif")
        if nif:
            by_nif.setdefault(nif, []).append(vid)
    for ids in by_nif.values():
        ids.sort()
    ambiguous = {nif for nif, ids in by_nif.items() if len(ids) > 1}
    proveedores_by_nif = {
        nif: prov[ids[0]] for nif, ids in by_nif.items()
        if nif not in ambiguous
    }

    if erp_snapshot is not None:
        _validate_snapshot(erp_snapshot)
    erp_rows, erp_estado, conflicts = _erp_index(erp_snapshot)

    master = {
        "proveedores": prov,
        "proveedores_by_nif": proveedores_by_nif,
        "by_nif": by_nif,
        "ambiguous_nifs": sorted(ambiguous),
        "pedidos": ped,
        "erp_rows": erp_rows,
        "erp_estado": erp_estado,
        "erp_conflicts": conflicts,
        "erp_unknown": erp_snapshot is None,
        "seen_invoice_keys": set(),
        "seen_amount_date": set(),
        "seen_order_keys": set(),
        "today": as_of,
        "as_of": as_of,
        "warnings": [],
        "erp_source_url": (erp_snapshot or {}).get("source_url"),
        "erp_fetched_at": (erp_snapshot or {}).get("fetched_at"),
    }
    master["sha256"] = master_digest(master, erp_snapshot)
    return master


def load_master(sources: str | Path, erp_snapshot: dict | None, *, as_of: str) -> dict:
    result = load(str(sources))
    for key in ("proveedores", "pedidos"):
        schema = result.schema.get(key) or {}
        if key not in result.lookups:
            raise ValueError(
                f"sources configuration missing required sheet '{key}'"
            )
        missing = schema.get("missing_columns") or []
        if missing:
            raise ValueError(
                f"workbook sheet '{key}' missing declared columns {missing}"
            )
    master = master_from_lookups(
        result.lookups["proveedores"], result.lookups["pedidos"],
        erp_snapshot, as_of=as_of,
    )
    master["warnings"] = list(result.warnings)
    master["sources"] = str(sources)
    master["schema"] = result.schema
    master["sha256"] = master_digest(master, erp_snapshot)
    return master


def restore_master(snapshot: dict) -> dict:
    if not isinstance(snapshot, dict):
        raise TypeError("master snapshot must be an object")
    master = {k: v for k, v in snapshot.items() if k != "sha256"}
    for key in ("proveedores", "pedidos", "erp_rows"):
        if key in master and not isinstance(master[key], dict):
            raise TypeError(f"master snapshot field {key!r} must be a mapping")
    for row in (master.get("pedidos") or {}).values():
        if isinstance(row, dict) and row.get("importe_total") is not None:
            row["importe_total"] = N.norm_importe(row["importe_total"])
    for rows in (master.get("erp_rows") or {}).values():
        if not isinstance(rows, list):
            raise TypeError("master snapshot erp_rows values must be lists")
        for row in rows:
            if isinstance(row, dict) and row.get("importe") is not None:
                row["importe"] = N.norm_importe(row["importe"])
    seen_keys = master.get("seen_invoice_keys") or []
    master["seen_invoice_keys"] = {
        tuple(item) for item in seen_keys if isinstance(item, list)
    }
    seen_soft = master.get("seen_amount_date") or []
    restored = set()
    for item in seen_soft:
        if isinstance(item, list) and len(item) == 2:
            restored.add((N.norm_importe(item[0]), item[1]))
    master["seen_amount_date"] = restored
    seen_orders = master.get("seen_order_keys") or []
    master["seen_order_keys"] = {
        item for item in seen_orders if isinstance(item, str) and item
    }
    master["as_of"] = _valid_as_of(master.get("as_of") or master.get("today"))
    master["today"] = master["as_of"]
    return master


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(_json_safe(v) for v in value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def master_digest(master: dict, erp_snapshot: dict | None = None) -> str:
    from ingestion.contracts import canonical_bytes, digest

    payload = {
        "master": _json_safe({k: v for k, v in master.items() if k != "sha256"}),
        "erp_snapshot": _json_safe(erp_snapshot),
    }
    return digest(canonical_bytes(payload))


def exportable_master(master: dict) -> dict:
    return _json_safe(master)
