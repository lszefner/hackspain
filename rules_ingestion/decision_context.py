from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ingestion.contracts import Contracts, canonical_bytes, digest
from ingestion.validation import validate_interpretation

from . import decision_registry as registry
from .checks import CHECKS
from .normalize import norm_iban, norm_importe, norm_nif, norm_text

SCHEMA_VERSION = "decision-context/1"
INTERNAL_SOURCE_KINDS = {
    "invoice": "invoice",
    "reading": "reading",
    "evidence": "evidence",
    "checks": "extraction_checks",
    "ruleset": "ruleset",
    "coverage": "extraction_auxiliary",
    "warnings": "extraction_auxiliary",
}
RESERVED_SOURCES = frozenset(INTERNAL_SOURCE_KINDS)
QUALITY_RANK = {"not_applicable": 0, "no_reported_issue": 1,
                "unlinked": 2, "uncertain": 3}
HISTORY_SCOPES = {
    "processed": "history.processed",
    "approved": "history.approved",
    "paid": "history.paid",
}
SUPPLIER_FIELDS = ("supplier.id", "supplier.tax_id", "supplier.iban",
                   "supplier.city", "supplier.active",
                   "supplier.payment_terms_days")
ORDER_FIELDS = ("order.id", "order.supplier_id", "order.total", "order.currency")
DECIMAL_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TERMS_PATTERN = re.compile(r"^\s*(\d+)\s*(dias|días|days)\s*$", re.IGNORECASE)
VAT_LABEL = re.compile(registry.VAT_LABEL_PATTERN, re.IGNORECASE)
WITHHOLDING_LABEL = re.compile(registry.WITHHOLDING_LABEL_PATTERN, re.IGNORECASE)
INDEXED_AMOUNT = re.compile(r"^invoice\.(lines|taxes)\.\d+\.amount$")
INDEXED_KIND = re.compile(r"^invoice\.taxes\.\d+\.kind$")
VALID_ON_FAIL = frozenset({"ESCALAR", "NO_PAGAR"})
VERDICT_TO_DECISION = {"PASS": "PAGAR", "NEEDS_REVIEW": "ESCALAR"}
DECISION_RANK = {"PAGAR": 0, "ESCALAR": 1, "NO_PAGAR": 2}
_BINARY = object()


class ContextError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSnapshot:
    kind: str
    payload: object | None
    captured_at: str
    asserted_by: str
    authoritative_for: tuple[str, ...]
    scope: str
    availability: str = "available"
    as_of: str | None = None


@dataclass(frozen=True)
class ContextBundle:
    context: dict
    artifacts: dict[str, bytes]


def load_schema() -> dict:
    path = Path(__file__).resolve().parent / "decision_context.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ContextError("non-finite Decimal in snapshot payload")
        return format(value, "f")
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContextError("non-finite float in snapshot payload")
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise ContextError(f"unsupported snapshot value type: {type(value).__name__}")


def _iso_date(value: Any) -> bool:
    if not isinstance(value, str) or not DATE_PATTERN.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _iso_datetime(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _nonnegative_integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _strict_decimal(value: Any) -> Decimal | None:
    if not isinstance(value, str) or not DECIMAL_PATTERN.match(value):
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _decimal_string(value: Decimal) -> str:
    return format(value, "f")


def _pointer_get(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    node = document
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not re.fullmatch(r"0|[1-9]\d*", part):
                raise KeyError(pointer)
            node = node[int(part)]
        elif isinstance(node, dict):
            node = node[part]
        else:
            raise KeyError(pointer)
    return node


def _fact(value: Any, state: str, basis: str, quality: str,
          transform: str, inputs: list[dict]) -> dict:
    return {
        "value": value,
        "state": state,
        "basis": basis,
        "extraction_quality": quality,
        "transform": transform,
        "inputs": inputs,
    }


def _finding(code: str, severity: str = "blocking", field_ids=(), rule_ids=(),
             source_ids=(), refs=()) -> dict:
    return {
        "code": code,
        "severity": severity,
        "field_ids": list(field_ids),
        "rule_ids": list(rule_ids),
        "source_ids": list(source_ids),
        "refs": list(refs),
    }


def _source_ref(source: str, pointer: str) -> dict:
    return {"source": source, "pointer": pointer}


def _field_ref(field: str) -> dict:
    return {"field": field}


def _snapshot_payload_bytes(snapshot: SourceSnapshot) -> bytes | None:
    payload = snapshot.payload
    if payload is None:
        return None
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    return canonical_bytes(_json_safe(payload))


def _snapshot_json(snapshot: SourceSnapshot) -> Any:
    payload = snapshot.payload
    if isinstance(payload, (bytes, bytearray)):
        try:
            return json.loads(bytes(payload))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
    if payload is None:
        return None
    return _json_safe(payload)


def _register_source(sources: dict, artifacts: dict[str, bytes], source_id: str,
                     kind: str, payload_bytes: bytes | None,
                     availability: str, captured_at: str, as_of: str | None,
                     asserted_by: str, authoritative_for: tuple[str, ...],
                     scope: str) -> None:
    permitted = registry.SOURCE_AUTHORITY.get(kind)
    if permitted is None:
        raise ContextError(f"unknown source kind: {kind}")
    unknown = set(authoritative_for) - set(permitted)
    if unknown:
        raise ContextError(
            f"source {source_id!r} claims unauthorized scopes: {sorted(unknown)}")
    if availability not in ("available", "partial", "unavailable"):
        raise ContextError(f"invalid availability {availability!r}")
    if not _iso_datetime(captured_at):
        raise ContextError(f"source {source_id!r} has invalid captured_at")
    if as_of is not None and not _iso_datetime(as_of):
        raise ContextError(f"source {source_id!r} has invalid as_of")
    if availability == "unavailable":
        artifact_ref = sha = None
    else:
        if payload_bytes is None:
            raise ContextError(
                f"source {source_id!r} is {availability} but has no payload")
        sha = digest(payload_bytes)
        artifact_ref = f"sha256/{sha[:2]}/{sha}/decision-source-{kind}"
        artifacts[artifact_ref] = payload_bytes
    sources[source_id] = {
        "kind": kind,
        "availability": availability,
        "artifact_ref": artifact_ref,
        "sha256": sha,
        "captured_at": captured_at,
        "as_of": as_of,
        "asserted_by": asserted_by,
        "authoritative_for": list(dict.fromkeys(authoritative_for)),
        "scope": scope,
    }


def _records(payload: Any) -> list | None:
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return payload["records"]
    return None


def _issue_related(pointer: str, invoice: dict) -> bool:
    for issue in invoice.get("issues") or []:
        field = issue.get("field") or ""
        if field and (pointer == field or pointer.startswith(field + "/")
                      or field.startswith(pointer + "/")):
            return True
    return False


def _issue_state(pointer: str, invoice: dict) -> str | None:
    for issue in invoice.get("issues") or []:
        field = issue.get("field") or ""
        if not (pointer == field or pointer.startswith(field + "/")
                or field.startswith(pointer + "/")):
            continue
        if issue.get("kind") in ("ambiguous", "conflicting"):
            return "ambiguous"
        if issue.get("kind") == "unreadable":
            return "missing"
    return None


def _issue_inputs(pointer: str, invoice: dict) -> list[dict]:
    inputs = []
    for index, issue in enumerate(invoice.get("issues") or []):
        field = issue.get("field") or ""
        if field and (pointer == field or pointer.startswith(field + "/")
                      or field.startswith(pointer + "/")):
            inputs.append(_source_ref("invoice", f"/issues/{index}"))
    return inputs


def _linked_quality(pointer: str, invoice: dict, evidence_links: dict,
                    uncertain_refs: set[str]) -> str:
    if _issue_related(pointer, invoice):
        return "uncertain"
    links = evidence_links.get(pointer)
    if not links:
        return "unlinked"
    refs = {ref for link in links for ref in link.get("reference_ids", [])}
    if refs & uncertain_refs:
        return "uncertain"
    return "no_reported_issue"


def _unlinked_quality(pointer: str, invoice: dict) -> str:
    return "uncertain" if _issue_related(pointer, invoice) else "no_reported_issue"


def _invoice_scalar(invoice: dict, pointer: str, raw: Any, kind: str,
                    evidence_links: dict, uncertain_refs: set[str]) -> dict:
    inputs = [_source_ref("invoice", pointer)] + _issue_inputs(pointer, invoice)
    override = _issue_state(pointer, invoice)
    if override is not None:
        return _fact(None, override, "observed", "uncertain",
                     "preserve_unresolved_issue/1", inputs)
    if raw is None:
        return _fact(None, "missing", "observed",
                     _unlinked_quality(pointer, invoice), "identity/1", inputs)
    quality = _linked_quality(pointer, invoice, evidence_links, uncertain_refs)
    if kind == "string":
        value, transform = norm_text(raw), "text_normalize/1"
    elif kind == "tax_id":
        value, transform = norm_nif(raw), "tax_id_format/1"
    elif kind == "iban":
        value, transform = norm_iban(raw), "iban_whitespace_uppercase/1"
    elif kind == "date":
        value, transform = (raw if _iso_date(raw) else None), "iso_date/1"
    elif kind == "currency":
        value = raw if isinstance(raw, str) and CURRENCY_PATTERN.match(raw) else None
        transform = "currency_code/1"
    elif kind == "decimal":
        parsed = _strict_decimal(raw)
        value = _decimal_string(parsed) if parsed is not None else None
        transform = "decimal_string/1"
    else:
        value = raw if isinstance(raw, str) and raw else None
        transform = "identity/1"
    if value is None:
        return _fact(None, "invalid", "normalized", quality, transform, inputs)
    return _fact(value, "present", "normalized", quality, transform, inputs)


def _classify_tax(label: Any) -> str:
    text = norm_text(label)
    if text and VAT_LABEL.match(text):
        return "vat"
    if text and WITHHOLDING_LABEL.match(text):
        return "withholding"
    return "unknown"


def _authoritative(snapshot: SourceSnapshot | None, scope: str) -> bool:
    return snapshot is not None and scope in snapshot.authoritative_for


def _resolve_unique(candidates: list[tuple[int, dict]], key) -> tuple[Any, list[int], bool]:
    if not candidates:
        return None, [], False
    keys = [key(record) for _, record in candidates]
    indexes = [index for index, _ in candidates]
    if any(not isinstance(k, str) or not k for k in keys):
        return None, indexes, False
    if len(set(keys)) != 1:
        return None, indexes, False
    signatures = {
        json.dumps(_json_safe(record), sort_keys=True)
        for _, record in candidates
    }
    if len(signatures) != 1:
        return None, indexes, False
    return keys[0], indexes, True


def _dependent_unavailable(field_id: str) -> dict:
    return _fact(None, "unavailable", "derived", "not_applicable",
                 "identity/1", [_field_ref(field_id)])


def _resolve_supplier(fields: dict, snapshots: dict, snapshot_json: dict,
                      findings: list) -> None:
    suppliers = snapshots.get("suppliers")
    inputs = [_field_ref("invoice.supplier_tax_id")]
    if suppliers is not None and suppliers.availability != "unavailable":
        inputs.append(_source_ref("suppliers", ""))

    def block_all(state: str, transform: str = "unique_exact_tax_id_match/1",
                  extra_inputs=None) -> None:
        fields["supplier.id"] = _fact(
            None, state, "derived", "not_applicable", transform,
            extra_inputs if extra_inputs is not None else inputs)
        for fid in SUPPLIER_FIELDS[1:]:
            fields[fid] = _dependent_unavailable("supplier.id")

    if suppliers is None or suppliers.availability == "unavailable":
        block_all("unavailable")
        if suppliers is not None:
            findings.append(_finding("SUPPLIER_MASTER_UNAVAILABLE",
                                     field_ids=["supplier.id"],
                                     source_ids=["suppliers"]))
        return
    records = _records(snapshot_json.get("suppliers"))
    if records is None:
        block_all("unavailable")
        findings.append(_finding("SUPPLIER_MASTER_MALFORMED",
                                 field_ids=["supplier.id"],
                                 source_ids=["suppliers"]))
        return
    tax_fact = fields["invoice.supplier_tax_id"]
    if not _authoritative(suppliers, "supplier.identity"):
        block_all("unavailable")
        findings.append(_finding("AUTHORITY_NOT_GRANTED",
                                 field_ids=["supplier.id"],
                                 source_ids=["suppliers"]))
        return
    inputs[1] = _source_ref("suppliers", "/records")
    if suppliers.availability == "partial":
        block_all("ambiguous")
        findings.append(_finding("INSUFFICIENT_COVERAGE",
                                 field_ids=["supplier.id"],
                                 source_ids=["suppliers"]))
        return
    if tax_fact["state"] != "present":
        block_all(tax_fact["state"])
        return
    tax_value = tax_fact["value"]
    candidates = [
        (index, record) for index, record in enumerate(records)
        if isinstance(record, dict) and norm_nif(record.get("nif")) == tax_value
    ]
    inputs = inputs + [
        _source_ref("suppliers", f"/records/{i}") for i, _ in candidates]
    resolved, indexes, unique = _resolve_unique(
        candidates, lambda r: r.get("id"))
    if unique:
        same_id = [
            (i, r) for i, r in enumerate(records)
            if isinstance(r, dict) and r.get("id") == resolved
        ]
        if len({norm_nif(r.get("nif")) for _, r in same_id}) > 1:
            unique = False
            indexes = sorted(set(indexes) | {i for i, _ in same_id})
    if not unique or resolved is None or not isinstance(resolved, str) or not resolved:
        state = "ambiguous" if candidates else "missing"
        fields["supplier.id"] = _fact(
            None, state, "derived", "not_applicable",
            "unique_exact_tax_id_match/1", inputs)
        if candidates:
            findings.append(_finding(
                "SUPPLIER_IDENTITY_AMBIGUOUS", field_ids=["supplier.id"],
                source_ids=["suppliers"], refs=inputs[1:]))
        for fid in SUPPLIER_FIELDS[1:]:
            fields[fid] = _dependent_unavailable("supplier.id")
        return
    fields["supplier.id"] = _fact(
        resolved, "present", "derived", "not_applicable",
        "unique_exact_tax_id_match/1", inputs)
    row_index = candidates[0][0]
    row = candidates[0][1]

    def master_field(field_id, scope, raw, column, transform, normalizer):
        pointer = f"/records/{row_index}/{column}" if column in row \
            else f"/records/{row_index}"
        ref = [_field_ref("supplier.id"), _source_ref("suppliers", pointer)]
        if not _authoritative(suppliers, scope):
            findings.append(_finding("AUTHORITY_NOT_GRANTED", "info",
                                     field_ids=[field_id],
                                     source_ids=["suppliers"]))
            return _fact(None, "unavailable", "normalized", "not_applicable",
                         transform, ref)
        if raw is None:
            return _fact(None, "missing", "normalized", "not_applicable",
                         transform, ref)
        value = normalizer(raw)
        if value is None:
            return _fact(None, "invalid", "normalized", "not_applicable",
                         transform, ref)
        return _fact(value, "present", "normalized", "not_applicable",
                     transform, ref)

    fields["supplier.tax_id"] = master_field(
        "supplier.tax_id", "supplier.identity", row.get("nif"),
        "nif", "tax_id_format/1", norm_nif)
    fields["supplier.iban"] = master_field(
        "supplier.iban", "supplier.bank_details", row.get("iban"),
        "iban", "iban_whitespace_uppercase/1", norm_iban)
    # Declared in sources.yaml and carried in the supplier snapshot already;
    # exposing it lets a norm line about where a supplier sits be executed
    # instead of recorded as unexecutable.
    fields["supplier.city"] = master_field(
        "supplier.city", "supplier.identity", row.get("ciudad"),
        "ciudad", "text_normalized/1", norm_text)
    active = row.get("active")
    active_pointer = f"/records/{row_index}/active" if "active" in row \
        else f"/records/{row_index}"
    active_ref = [_field_ref("supplier.id"),
                  _source_ref("suppliers", active_pointer)]
    if not _authoritative(suppliers, "supplier.active"):
        findings.append(_finding("AUTHORITY_NOT_GRANTED", "info",
                                 field_ids=["supplier.active"],
                                 source_ids=["suppliers"]))
        fields["supplier.active"] = _fact(
            None, "unavailable", "observed", "not_applicable", "identity/1",
            active_ref)
    elif isinstance(active, bool):
        fields["supplier.active"] = _fact(
            active, "present", "observed", "not_applicable", "identity/1",
            active_ref)
    elif active is None:
        fields["supplier.active"] = _fact(
            None, "missing", "observed", "not_applicable", "identity/1",
            active_ref)
    else:
        fields["supplier.active"] = _fact(
            None, "invalid", "observed", "not_applicable", "identity/1",
            active_ref)
    if "payment_terms_days" in row:
        terms_raw = row["payment_terms_days"]
        terms_column = "payment_terms_days"
        normalizer = _nonnegative_integer
    else:
        match = TERMS_PATTERN.match(row.get("condiciones") or "") \
            if isinstance(row.get("condiciones"), str) else None
        terms_raw = int(match.group(1)) if match else row.get("condiciones")
        terms_column = "condiciones"
        normalizer = _nonnegative_integer
    fields["supplier.payment_terms_days"] = master_field(
        "supplier.payment_terms_days", "supplier.payment_terms",
        terms_raw, terms_column, "explicit_terms_days/1", normalizer)
    inv_iban = fields["invoice.iban"]["value"]
    sup_iban = fields["supplier.iban"]["value"]
    if inv_iban and sup_iban and inv_iban != sup_iban:
        findings.append(_finding(
            "INVOICE_MASTER_IBAN_DIFFER", "info",
            field_ids=["invoice.iban", "supplier.iban"]))


def _resolve_order(fields: dict, snapshots: dict, snapshot_json: dict,
                   findings: list) -> None:
    orders = snapshots.get("orders")
    inputs = [_field_ref("invoice.order_reference")]
    if orders is not None and orders.availability != "unavailable":
        inputs.append(_source_ref("orders", ""))

    def block_all(state: str, code: str | None = None,
                  severity: str = "blocking") -> None:
        fields["order.id"] = _fact(
            None, state, "derived", "not_applicable",
            "unique_exact_reference_match/1", inputs)
        for fid in ORDER_FIELDS[1:]:
            fields[fid] = _dependent_unavailable("order.id")
        if code:
            findings.append(_finding(code, severity, field_ids=["order.id"],
                                     source_ids=["orders"]))

    if orders is None or orders.availability == "unavailable":
        block_all("unavailable",
                  "ORDER_MASTER_UNAVAILABLE" if orders is not None else None)
        return
    records = _records(snapshot_json.get("orders"))
    if records is None:
        block_all("unavailable", "ORDER_MASTER_MALFORMED")
        return
    if not _authoritative(orders, "order.identity"):
        block_all("unavailable", "AUTHORITY_NOT_GRANTED")
        return
    if orders.availability == "partial":
        block_all("ambiguous", "INSUFFICIENT_COVERAGE")
        return
    inputs[1] = _source_ref("orders", "/records")
    ref_fact = fields["invoice.order_reference"]
    if ref_fact["state"] != "present":
        fields["order.id"] = _fact(
            None, ref_fact["state"], "derived", "not_applicable",
            "unique_exact_reference_match/1", inputs)
        for fid in ORDER_FIELDS[1:]:
            fields[fid] = _dependent_unavailable("order.id")
        return
    ref = ref_fact["value"]
    candidates = [
        (index, record) for index, record in enumerate(records)
        if isinstance(record, dict) and norm_text(record.get("pedido")) == ref
    ]
    inputs = inputs + [_source_ref("orders", f"/records/{i}") for i, _ in candidates]
    _, indexes, unique = _resolve_unique(candidates, lambda r: ref)
    if not unique:
        fields["order.id"] = _fact(
            None, "ambiguous" if candidates else "missing", "derived",
            "not_applicable", "unique_exact_reference_match/1", inputs)
        if candidates:
            findings.append(_finding(
                "ORDER_IDENTITY_AMBIGUOUS", field_ids=["order.id"],
                source_ids=["orders"],
                refs=[_source_ref("orders", f"/records/{i}") for i in indexes]))
        for fid in ORDER_FIELDS[1:]:
            fields[fid] = _dependent_unavailable("order.id")
        return
    fields["order.id"] = _fact(
        ref, "present", "derived", "not_applicable",
        "unique_exact_reference_match/1", inputs)
    row_index = candidates[0][0]
    row = candidates[0][1]
    owner = row.get("proveedor_id")
    owner = norm_text(owner) if isinstance(owner, str) else None
    owner_pointer = f"/records/{row_index}/proveedor_id" \
        if "proveedor_id" in row else f"/records/{row_index}"
    fields["order.supplier_id"] = _fact(
        owner, "present" if owner else (
            "missing" if row.get("proveedor_id") is None else "invalid"),
        "normalized", "not_applicable", "text_normalize/1",
        [_field_ref("order.id"), _source_ref("orders", owner_pointer)])

    def order_field(field_id, scope, raw, column, transform, normalizer):
        pointer = f"/records/{row_index}/{column}" if column in row \
            else f"/records/{row_index}"
        ref = [_field_ref("order.id"), _source_ref("orders", pointer)]
        if not _authoritative(orders, scope):
            findings.append(_finding("AUTHORITY_NOT_GRANTED", "info",
                                     field_ids=[field_id], source_ids=["orders"]))
            return _fact(None, "unavailable", "normalized", "not_applicable",
                         transform, ref)
        if raw is None:
            return _fact(None, "missing", "normalized", "not_applicable",
                         transform, ref)
        value = normalizer(raw)
        if value is None:
            return _fact(None, "invalid", "normalized", "not_applicable",
                         transform, ref)
        return _fact(value, "present", "normalized", "not_applicable",
                     transform, ref)

    fields["order.total"] = order_field(
        "order.total", "order.amount", row.get("importe_total"),
        "importe_total", "decimal_string/1",
        lambda v: _decimal_string(norm_importe(v)) if norm_importe(v) is not None else None)
    fields["order.currency"] = order_field(
        "order.currency", "order.currency", row.get("currency"), "currency",
        "currency_code/1",
        lambda v: v if isinstance(v, str) and CURRENCY_PATTERN.match(v) else None)
    supplier_id = fields["supplier.id"]["value"]
    if owner and supplier_id and owner != supplier_id:
        findings.append(_finding(
            "ORDER_SUPPLIER_MISMATCH",
            field_ids=["order.supplier_id", "supplier.id"],
            source_ids=["orders"]))


def _resolve_erp(fields: dict, snapshots: dict, snapshot_json: dict,
                 findings: list) -> None:
    erp = snapshots.get("erp")
    unavailable = _fact(None, "unavailable", "observed", "not_applicable",
                        "identity/1", [])
    if erp is None or erp.availability != "available":
        fields["erp.order_payment_status"] = unavailable
        if erp is not None:
            findings.append(_finding(
                "ERP_UNAVAILABLE", field_ids=["erp.order_payment_status"],
                source_ids=["erp"]))
        return
    if not _authoritative(erp, "order.payment_status"):
        fields["erp.order_payment_status"] = unavailable
        findings.append(_finding("AUTHORITY_NOT_GRANTED", "info",
                                 field_ids=["erp.order_payment_status"],
                                 source_ids=["erp"]))
        return
    payload = snapshot_json.get("erp")
    records = _records(payload)
    complete = isinstance(payload, dict) and payload.get("complete") is True
    if records is None or not complete:
        fields["erp.order_payment_status"] = unavailable
        findings.append(_finding("ERP_INSUFFICIENT_COVERAGE",
                                 field_ids=["erp.order_payment_status"],
                                 source_ids=["erp"]))
        return
    ref = fields["invoice.order_reference"]["value"]
    matches = [
        (i, r) for i, r in enumerate(records)
        if isinstance(r, dict) and norm_text(r.get("pedido")) == ref
    ] if ref else []
    inputs = [_field_ref("invoice.order_reference"),
              _source_ref("erp", "/records")]
    inputs += [_source_ref("erp", f"/records/{i}") for i, _ in matches]
    states = {norm_text(r.get("estado")) for _, r in matches}
    recognized = {s for s in states if s in ("PENDIENTE", "PAGADA")}
    if not matches:
        fields["erp.order_payment_status"] = _fact(
            None, "missing", "observed", "not_applicable", "identity/1", inputs)
    elif len(recognized) != 1 or len(states) != len(recognized):
        fields["erp.order_payment_status"] = _fact(
            None, "ambiguous", "observed", "not_applicable", "identity/1", inputs)
        findings.append(_finding("ERP_STATUS_CONFLICT",
                                 field_ids=["erp.order_payment_status"],
                                 source_ids=["erp"]))
    else:
        fields["erp.order_payment_status"] = _fact(
            next(iter(recognized)), "present", "observed", "not_applicable",
            "identity/1", inputs)


def _resolve_history(fields: dict, invoice: dict, source_id: str,
                     snapshots: dict, snapshot_json: dict,
                     findings: list) -> None:
    field_id = f"history.{source_id}_matches"
    scope = HISTORY_SCOPES[source_id]
    snapshot = snapshots.get(source_id)
    inputs = [
        _field_ref("invoice.number"), _field_ref("supplier.id"),
        _field_ref("invoice.total"), _field_ref("invoice.issue_date"),
        _field_ref("invoice.currency"),
    ]
    if snapshot is not None and snapshot.availability != "unavailable":
        inputs.append(_source_ref(source_id, ""))
    unavailable = _fact(None, "unavailable", "derived", "not_applicable",
                        "history_match/1", inputs)
    if snapshot is None or snapshot.availability != "available":
        fields[field_id] = unavailable
        if snapshot is not None:
            findings.append(_finding("HISTORY_UNAVAILABLE",
                                     field_ids=[field_id],
                                     source_ids=[source_id]))
        return
    if not _authoritative(snapshot, scope):
        fields[field_id] = unavailable
        findings.append(_finding("AUTHORITY_NOT_GRANTED", "info",
                                 field_ids=[field_id], source_ids=[source_id]))
        return
    payload = snapshot_json.get(source_id)
    records = _records(payload)
    complete = isinstance(payload, dict) and payload.get("complete") is True
    if records is None or not complete:
        fields[field_id] = unavailable
        findings.append(_finding("HISTORY_INSUFFICIENT_COVERAGE",
                                 field_ids=[field_id], source_ids=[source_id]))
        return
    if payload.get("kind") != source_id:
        fields[field_id] = unavailable
        findings.append(_finding("HISTORY_KIND_MISMATCH",
                                 field_ids=[field_id], source_ids=[source_id]))
        return
    inputs[-1] = _source_ref(source_id, "/records")
    key_fields = ("invoice.number", "supplier.id", "invoice.total",
                  "invoice.issue_date", "invoice.currency")
    states = [fields[fid]["state"] for fid in key_fields]
    qualities = [fields[fid]["extraction_quality"] for fid in key_fields]
    if any(q in ("uncertain", "unlinked") for q in qualities):
        fields[field_id] = _fact(
            None, "ambiguous", "derived", "not_applicable",
            "history_match/1", inputs)
        findings.append(_finding("HISTORY_INPUT_UNCERTAIN",
                                 field_ids=[field_id], source_ids=[source_id]))
        return
    if any(state != "present" for state in states):
        for bad in ("unavailable", "ambiguous", "invalid", "missing"):
            if bad in states:
                inherited = bad
                break
        fields[field_id] = _fact(
            None, inherited, "derived", "not_applicable",
            "history_match/1", inputs)
        return
    number = fields["invoice.number"]["value"]
    supplier_id = fields["supplier.id"]["value"]
    total = Decimal(fields["invoice.total"]["value"])
    issue_date = fields["invoice.issue_date"]["value"]
    currency = fields["invoice.currency"]["value"]
    file_id = invoice.get("file_id")
    matches = []
    malformed = False
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            malformed = True
            continue
        if source_id == "processed" and record.get("file_id") == file_id:
            continue
        rec_number = record.get("invoice_number")
        rec_supplier = record.get("supplier_id")
        rec_currency = record.get("currency")
        rec_date = record.get("issue_date")
        rec_total = record.get("total")
        rec_total_dec = _strict_decimal(
            rec_total if isinstance(rec_total, str)
            else str(rec_total) if rec_total is not None else None)
        record_valid = (
            isinstance(rec_number, str) and bool(rec_number)
            and isinstance(rec_supplier, str) and bool(rec_supplier)
            and isinstance(rec_date, str) and _iso_date(rec_date)
            and isinstance(rec_currency, str)
            and bool(CURRENCY_PATTERN.match(rec_currency))
            and rec_total_dec is not None)
        if not record_valid:
            malformed = True
            continue
        hard = rec_number == number and rec_supplier == supplier_id
        soft = (rec_total_dec == total and rec_date == issue_date
                and rec_currency == currency)
        if hard or soft:
            matches.append(index)
    match_inputs = inputs + [
        _source_ref(source_id, f"/records/{i}") for i in matches]
    if malformed:
        fields[field_id] = _fact(None, "ambiguous", "derived", "not_applicable",
                                 "history_match/1", match_inputs)
        findings.append(_finding("HISTORY_RECORDS_MALFORMED",
                                 field_ids=[field_id], source_ids=[source_id],
                                 refs=match_inputs[-len(matches):] if matches else []))
        return
    fields[field_id] = _fact(
        [_source_ref(source_id, f"/records/{i}") for i in matches],
        "present", "derived", "not_applicable", "history_match/1", match_inputs)


def _build_fields(invoice: dict, evidence_links: dict,
                  uncertain_refs: set[str], snapshots: dict,
                  snapshot_json: dict, findings: list) -> dict:
    fields: dict[str, dict] = {}

    def invoice_field(pointer, raw, kind):
        return _invoice_scalar(invoice, pointer, raw, kind, evidence_links,
                               uncertain_refs)

    doc_type = invoice.get("document_type")
    if doc_type not in ("invoice", "credit_note", "other", "unknown"):
        doc_type = "unknown"
    fields["invoice.document_type"] = _fact(
        doc_type, "present", "observed",
        _linked_quality("/document_type", invoice, evidence_links, uncertain_refs),
        "identity/1", [_source_ref("invoice", "/document_type")])
    fields["invoice.number"] = invoice_field(
        "/invoice_number", invoice.get("invoice_number"), "string")
    fields["invoice.supplier_tax_id"] = invoice_field(
        "/supplier/tax_id", (invoice.get("supplier") or {}).get("tax_id"), "tax_id")
    fields["invoice.iban"] = invoice_field(
        "/payment/iban", (invoice.get("payment") or {}).get("iban"), "iban")
    fields["invoice.order_reference"] = invoice_field(
        "/purchase_order_reference", invoice.get("purchase_order_reference"), "string")
    fields["invoice.issue_date"] = invoice_field(
        "/issue_date", invoice.get("issue_date"), "date")
    fields["invoice.currency"] = invoice_field(
        "/currency", invoice.get("currency"), "currency")
    totals = invoice.get("totals") or {}
    fields["invoice.taxable_base"] = invoice_field(
        "/totals/taxable_base", totals.get("taxable_base"), "decimal")
    fields["invoice.total"] = invoice_field(
        "/totals/total", totals.get("total"), "decimal")

    lines = invoice.get("lines") or []
    line_inputs = []
    line_states = []
    line_values = []
    for index, line in enumerate(lines):
        fact = invoice_field(f"/lines/{index}/amount", line.get("amount"), "decimal")
        fields[f"invoice.lines.{index}.amount"] = fact
        line_inputs.append(_field_ref(f"invoice.lines.{index}.amount"))
        line_states.append(fact["state"])
        line_values.append(fact["value"])
    if not lines:
        fields["invoice.line_amounts"] = _fact(
            None, "missing", "derived", "not_applicable",
            "collect_line_amounts/1", [_source_ref("invoice", "/lines")])
    elif "invalid" in line_states:
        fields["invoice.line_amounts"] = _fact(
            None, "invalid", "derived", "not_applicable",
            "collect_line_amounts/1", line_inputs)
    elif any(state != "present" for state in line_states):
        fields["invoice.line_amounts"] = _fact(
            None, "missing", "derived", "not_applicable",
            "collect_line_amounts/1", line_inputs)
    else:
        quality = "no_reported_issue" if all(
            fields[f"invoice.lines.{i}.amount"]["extraction_quality"]
            == "no_reported_issue" for i in range(len(lines))) else "uncertain"
        fields["invoice.line_amounts"] = _fact(
            list(line_values), "present", "derived", quality,
            "collect_line_amounts/1", line_inputs)

    taxes = invoice.get("taxes") or []
    tax_kinds = []
    tax_states = []
    vat_inputs = []
    for index, tax in enumerate(taxes):
        label_pointer = f"/taxes/{index}/label"
        label_inputs = [_source_ref("invoice", label_pointer)] \
            + _issue_inputs(label_pointer, invoice)
        if _issue_state(label_pointer, invoice) is not None:
            kind_value = None
            fields[f"invoice.taxes.{index}.kind"] = _fact(
                None, _issue_state(label_pointer, invoice), "observed",
                "uncertain", "preserve_unresolved_issue/1", label_inputs)
        else:
            kind_value = _classify_tax(tax.get("label"))
            fields[f"invoice.taxes.{index}.kind"] = _fact(
                kind_value, "present", "normalized",
                _linked_quality(label_pointer, invoice, evidence_links,
                                uncertain_refs),
                "classify_tax_label/1", label_inputs)
        tax_kinds.append(kind_value if kind_value is not None else "unknown")
        amount_fact = invoice_field(
            f"/taxes/{index}/amount", tax.get("amount"), "decimal")
        fields[f"invoice.taxes.{index}.amount"] = amount_fact
        tax_states.append(amount_fact["state"])
        vat_inputs.append(_field_ref(f"invoice.taxes.{index}.kind"))
        vat_inputs.append(_field_ref(f"invoice.taxes.{index}.amount"))
    if not taxes:
        vat_state, vat_value = "missing", None
    elif "unknown" in tax_kinds:
        vat_state, vat_value = "ambiguous", None
    elif any(state != "present" for state in tax_states):
        vat_state = "invalid" if "invalid" in tax_states else "missing"
        vat_value = None
    else:
        vat_indexes = [i for i, k in enumerate(tax_kinds) if k == "vat"]
        if not vat_indexes:
            vat_state, vat_value = "missing", None
        else:
            vat_state = "present"
            vat_value = _decimal_string(sum(
                Decimal(fields[f"invoice.taxes.{i}.amount"]["value"])
                for i in vat_indexes))
    fields["invoice.vat_amount"] = _fact(
        vat_value, vat_state, "derived",
        "no_reported_issue" if vat_state == "present" else "not_applicable",
        "sum_explicit_vat_rows/1",
        vat_inputs or [_source_ref("invoice", "/taxes")])

    _resolve_supplier(fields, snapshots, snapshot_json, findings)
    _resolve_order(fields, snapshots, snapshot_json, findings)
    _resolve_erp(fields, snapshots, snapshot_json, findings)
    for source_id in ("processed", "approved", "paid"):
        _resolve_history(fields, invoice, source_id, snapshots, snapshot_json,
                         findings)
    _propagate_quality(fields)
    return fields


def _propagate_quality(fields: dict) -> None:
    resolved: dict[str, str] = {}
    visiting: set[str] = set()

    def quality_of(field_id: str) -> str:
        if field_id in resolved:
            return resolved[field_id]
        if field_id in visiting:
            return fields[field_id]["extraction_quality"]
        visiting.add(field_id)
        fact = fields[field_id]
        worst = fact["extraction_quality"]
        for ref in fact["inputs"]:
            if "field" in ref and ref["field"] in fields:
                dep = quality_of(ref["field"])
                if QUALITY_RANK[dep] > QUALITY_RANK[worst]:
                    worst = dep
        visiting.discard(field_id)
        resolved[field_id] = worst
        return worst

    for field_id in fields:
        quality_of(field_id)
    for field_id, quality in resolved.items():
        fields[field_id]["extraction_quality"] = quality


def _condition_fields(condition: Any) -> list[str]:
    names: list[str] = []
    if isinstance(condition, dict):
        for key, value in condition.items():
            if key == "field" and isinstance(value, str):
                names.append(value)
            else:
                names.extend(_condition_fields(value))
    elif isinstance(condition, list):
        for item in condition:
            names.extend(_condition_fields(item))
    return names


def _resolve_field(name: str, fields: dict) -> str | None:
    if name in fields:
        return name
    if name in registry.ALIASES and registry.ALIASES[name] in fields:
        return registry.ALIASES[name]
    return None


def _valid_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, Decimal, str)):
        try:
            parsed = Decimal(str(value))
        except InvalidOperation:
            return False
        return parsed.is_finite() and parsed >= 0
    return False


def _bind_rule(rule: dict, fields: dict) -> dict:
    reasons: list[str] = []
    canonical = rule.get("canonical")
    raw_params = rule.get("params")
    condition = rule.get("condition")
    supported = True

    if "params" not in rule:
        params = {}
    elif isinstance(raw_params, dict):
        params = raw_params
    else:
        params = {}
        supported = False
        reasons.append("INVALID_PARAMETERS")

    if not isinstance(rule.get("enabled", True), bool):
        supported = False
        reasons.append("INVALID_ENABLED")
    if not isinstance(rule.get("on_fail"), str) \
            or rule["on_fail"] not in VALID_ON_FAIL:
        supported = False
        reasons.append("INVALID_ON_FAIL")
    known_check = isinstance(canonical, str) \
        and canonical in registry.CHECK_FUNCTIONS
    if not known_check:
        supported = False
        reasons.append("UNKNOWN_CANONICAL")
    if not isinstance(condition, dict) or condition.get("kind") != "python_check":
        supported = False
        reasons.append("UNSUPPORTED_CONDITION")
    elif known_check and (
            condition.get("module") != "rules_ingestion.checks"
            or condition.get("function") != registry.CHECK_FUNCTIONS[canonical]):
        supported = False
        reasons.append("CONDITION_FUNCTION_MISMATCH")

    raw_input_fields = rule.get("input_fields")
    if "input_fields" not in rule:
        input_fields = []
    elif not isinstance(raw_input_fields, list) or any(
            not isinstance(x, str) for x in raw_input_fields):
        supported = False
        reasons.append("INVALID_INPUT_FIELDS")
        input_fields = []
    else:
        input_fields = raw_input_fields

    if known_check:
        known_params = set(registry.FLAG_DEPENDENCIES.get(canonical, {})) | set(
            registry.OTHER_PARAMETERS.get(canonical, ()))
        for key in params:
            if key not in known_params:
                supported = False
                reasons.append("UNKNOWN_PARAMETER")
        for flag in registry.FLAG_DEPENDENCIES.get(canonical, {}):
            if flag in params and not isinstance(params[flag], bool):
                supported = False
                reasons.append("INVALID_FLAG")
        for flag in registry.UNIMPLEMENTED_FLAGS.get(canonical, ()):
            if params.get(flag) is True:
                supported = False
                reasons.append("UNIMPLEMENTED_FLAG")
        for number_param in ("tolerance_eur", "escalate_above_eur"):
            if number_param in params and not _valid_number(params[number_param]):
                supported = False
                reasons.append("INVALID_PARAMETER")
        for list_param in ("required_fields", "hard_key", "soft_key"):
            if list_param in params and (
                    not isinstance(params[list_param], list) or any(
                        not isinstance(x, str) for x in params[list_param])):
                supported = False
                reasons.append("INVALID_PARAMETER")
        if "allowed_currencies" in params:
            allowed = params["allowed_currencies"]
            if not isinstance(allowed, list) or not allowed or any(
                    not isinstance(c, str) or not CURRENCY_PATTERN.match(c)
                    for c in allowed):
                supported = False
                reasons.append("INVALID_PARAMETER")
        if "soft_duplicate_verdict" in params and params[
                "soft_duplicate_verdict"] not in ("PASS", "FAIL", "NEEDS_REVIEW"):
            supported = False
            reasons.append("INVALID_PARAMETER")

    requested = list(input_fields)
    for key in ("required_fields", "hard_key", "soft_key"):
        value = params.get(key)
        if isinstance(value, list):
            requested += [x for x in value if isinstance(x, str)]
    requested += _condition_fields(condition)
    if known_check:
        requested += registry.BASE_DEPENDENCIES.get(canonical, ())
        for flag, (default, deps) in registry.FLAG_DEPENDENCIES.get(
                canonical, {}).items():
            if params.get(flag, default) is True:
                requested += deps
        if canonical == "MISSING" and "required_fields" not in params:
            requested += registry.DEFAULT_REQUIRED_FIELDS

    dependencies = []
    seen = set()
    for name in requested:
        if name in seen:
            continue
        seen.add(name)
        field_id = _resolve_field(name, fields)
        if field_id is None:
            dependencies.append({"requested_name": name, "field_id": None,
                                 "state": "unknown_field"})
            continue
        state = fields[field_id]["state"]
        dependencies.append({
            "requested_name": name,
            "field_id": field_id,
            "state": "bound" if state == "present" else state,
        })

    allowed_states = {"bound", "missing"} if canonical == "MISSING" else {"bound"}
    blocked = False
    for dep in dependencies:
        if dep["state"] == "unknown_field":
            blocked = True
            reasons.append("UNKNOWN_FIELD")
        elif dep["state"] not in allowed_states:
            blocked = True
            reasons.append(f"{dep['state'].upper()}_INPUT")
        elif dep["field_id"]:
            quality = fields[dep["field_id"]]["extraction_quality"]
            if quality == "uncertain":
                blocked = True
                reasons.append("UNCERTAIN_INPUT")
            elif quality == "unlinked":
                blocked = True
                reasons.append("UNLINKED_INPUT")

    if known_check:
        currency = fields.get("invoice.currency", {}).get("value")
        if canonical in ("AMOUNT", "AUTHORIZATION") and currency is not None \
                and currency != "EUR":
            supported = False
            reasons.append("NON_EUR_CURRENCY")
        if canonical == "AMOUNT":
            if params.get("check_total_is_base_plus_iva", True):
                kinds = [f["value"] for key, f in fields.items()
                         if INDEXED_KIND.match(key)]
                non_vat_bad = any(
                    f["state"] != "present"
                    for key, f in fields.items()
                    if INDEXED_AMOUNT.match(key)
                    and key.startswith("invoice.taxes.")
                    and fields.get(key[:-len(".amount")] + ".kind", {}
                                   ).get("value") != "vat")
                if any(k in ("withholding", "other", "unknown") for k in kinds):
                    supported = False
                    reasons.append("UNSUPPORTED_TAX_RECONCILIATION")
                elif non_vat_bad:
                    blocked = True
                    reasons.append("MISSING_INPUT")
            if params.get("check_matches_pedido", True):
                order_currency = fields.get("order.currency", {}).get("value")
                if currency and order_currency and currency != order_currency:
                    supported = False
                    reasons.append("CURRENCY_MISMATCH")
        if canonical == "DUPLICATES":
            supported = False
            reasons.append("PROCESSED_HISTORY_POLICY_UNRESOLVED")

    return {
        "rule_id": rule["id"],
        "rule_pointer": f"/rules/{rule['_index']}",
        "dependencies": dependencies,
        "input_status": "blocked" if blocked else "ready",
        "execution_status": "supported" if supported else "unsupported",
        "reason_codes": sorted(set(reasons)),
    }


def _parse_ruleset(raw: bytes) -> dict:
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextError("ruleset artifact is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ContextError("ruleset artifact must be a JSON object")
    if document.get("schema_version") != "2.0":
        raise ContextError("ruleset schema_version must be 2.0")
    for key in ("ruleset_version", "policy_id"):
        if not isinstance(document.get(key), str) or not document[key]:
            raise ContextError(f"ruleset {key} must be a nonempty string")
    if "precedence" in document and document["precedence"] != list(
            registry.SUPPORTED_PRECEDENCE):
        raise ContextError("unsupported ruleset precedence")
    rules = document.get("rules")
    if not isinstance(rules, list):
        raise ContextError("ruleset rules must be a list")
    seen_ids = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) \
                or not rule["id"]:
            raise ContextError(f"ruleset rule at /rules/{index} lacks a valid id")
        if rule["id"] in seen_ids:
            raise ContextError(f"duplicate ruleset rule id {rule['id']!r}")
        seen_ids.add(rule["id"])
        rule["_index"] = index
    return document


def _bind_rules(ruleset_doc: dict, fields: dict) -> tuple[list, int]:
    bindings = []
    enabled = 0
    for rule in ruleset_doc["rules"]:
        if rule.get("enabled", True) is False:
            continue
        enabled += 1
        bindings.append(_bind_rule(rule, fields))
    return bindings, enabled


def build_context(outcome: dict, *, snapshots: dict[str, SourceSnapshot],
                  ruleset: dict | bytes, evaluation_date: str,
                  captured_at: str,
                  asserted_by: str = "invoice-extraction") -> ContextBundle:
    return _build_context(outcome, snapshots=snapshots, ruleset=ruleset,
                          evaluation_date=evaluation_date,
                          captured_at=captured_at, asserted_by=asserted_by,
                          validate_result=True)


def _build_context(outcome: dict, *, snapshots: dict[str, SourceSnapshot],
                   ruleset: dict | bytes, evaluation_date: str,
                   captured_at: str, asserted_by: str,
                   validate_result: bool) -> ContextBundle:
    if not _iso_date(evaluation_date):
        raise ContextError("evaluation_date must be a strict ISO date")
    if not _iso_datetime(captured_at):
        raise ContextError("captured_at must be a timezone-qualified date-time")
    outcome = outcome or {}
    invoice = outcome.get("invoice")
    if not isinstance(invoice, dict):
        raise ContextError("outcome lacks an invoice object")
    contracts = Contracts()
    try:
        contracts.validate("invoice", invoice)
    except (KeyError, TypeError, ValueError) as exc:
        raise ContextError(f"invoice fails schema validation: {exc}") from exc
    reading = outcome.get("reading")
    evidence = outcome.get("evidence")
    if outcome.get("file_id") is not None \
            and outcome["file_id"] != invoice["file_id"]:
        raise ContextError("outcome file_id does not match invoice file_id")
    if isinstance(reading, dict) and reading.get("file_id") != invoice["file_id"]:
        raise ContextError("reading file_id does not match invoice file_id")
    for source_id in snapshots:
        if source_id in RESERVED_SOURCES:
            raise ContextError(
                f"snapshot id {source_id!r} collides with a reserved source")

    findings: list = []
    sources: dict = {}
    artifacts: dict[str, bytes] = {}
    snapshot_json: dict[str, Any] = {}

    def internal_source(source_id, kind, payload, availability="available"):
        raw = canonical_bytes(_json_safe(payload)) if payload is not None else None
        _register_source(sources, artifacts, source_id, kind, raw, availability,
                         captured_at, None, asserted_by, (),
                         f"extraction {kind} artifact")
        snapshot_json[source_id] = _json_safe(payload) if payload is not None else None

    internal_source("invoice", "invoice", invoice)
    if isinstance(reading, dict):
        internal_source("reading", "reading", reading)
    else:
        _register_source(sources, artifacts, "reading", "reading", None,
                         "unavailable", captured_at, None, asserted_by, (),
                         "reading acquisition failed")
        findings.append(_finding("READING_UNAVAILABLE", source_ids=["reading"]))
    if isinstance(evidence, dict):
        internal_source("evidence", "evidence", evidence)
    else:
        _register_source(sources, artifacts, "evidence", "evidence", None,
                         "unavailable", captured_at, None, asserted_by, (),
                         "evidence acquisition failed")
        findings.append(_finding("EVIDENCE_UNAVAILABLE", source_ids=["evidence"]))

    recomputed = None
    if isinstance(reading, dict) and isinstance(evidence, dict):
        recomputed = validate_interpretation(invoice, evidence, reading, contracts)
    internal_source("checks", "extraction_checks", {
        "reported_status": outcome.get("status"),
        "reported_checks": outcome.get("checks"),
        "recomputed": recomputed,
    })
    if recomputed is not None:
        for key, ok in recomputed.get("checks", {}).items():
            if ok is not True:
                findings.append(_finding(
                    f"EXTRACTION_CHECK_FAILED_{key.upper()}",
                    source_ids=["checks"]))
    else:
        findings.append(_finding("EXTRACTION_CHECKS_UNAVAILABLE",
                                 source_ids=["checks"]))

    if isinstance(outcome.get("coverage"), dict):
        internal_source("coverage", "extraction_auxiliary", outcome["coverage"])
    if isinstance(outcome.get("warnings"), list):
        internal_source("warnings", "extraction_auxiliary", outcome["warnings"])

    ruleset_raw = bytes(ruleset) if isinstance(ruleset, (bytes, bytearray)) \
        else canonical_bytes(_json_safe(ruleset))
    _register_source(sources, artifacts, "ruleset", "ruleset", ruleset_raw,
                     "available", captured_at, None, asserted_by,
                     ("business_rules",), "frozen ruleset bytes")
    ruleset_doc = _parse_ruleset(ruleset_raw)
    snapshot_json["ruleset"] = ruleset_doc

    for source_id, snap in snapshots.items():
        raw = _snapshot_payload_bytes(snap)
        _register_source(sources, artifacts, source_id, snap.kind, raw,
                         snap.availability, snap.captured_at, snap.as_of,
                         snap.asserted_by, tuple(snap.authoritative_for),
                         snap.scope)
        snapshot_json[source_id] = _snapshot_json(snap)

    for index, _annotation in enumerate(invoice.get("annotations") or []):
        findings.append(_finding(
            "UNREVIEWED_ANNOTATION", source_ids=["invoice"],
            refs=[_source_ref("invoice", f"/annotations/{index}")]))
    if invoice.get("document_type") != "invoice":
        findings.append(_finding(
            "DOCUMENT_TYPE_UNSUPPORTED", field_ids=["invoice.document_type"],
            refs=[_source_ref("invoice", "/document_type")]))

    evidence_links: dict[str, list] = {}
    if isinstance(evidence, dict):
        raw_map = evidence.get("pointers", evidence)
        if isinstance(raw_map, dict):
            evidence_links = {
                key: [link for link in
                      (value if isinstance(value, list) else [value])
                      if isinstance(link, dict)]
                for key, value in raw_map.items()
            }
    uncertain_refs: set[str] = set()
    if isinstance(reading, dict):
        for page in reading.get("pages", []):
            for block in page.get("blocks", []):
                if not block.get("uncertainties"):
                    continue
                uncertain_refs.add(block.get("id"))
                for row in block.get("rows", []):
                    uncertain_refs.add(row.get("id"))
                    for cell in row.get("cells", []):
                        uncertain_refs.add(cell.get("id"))

    fields = _build_fields(invoice, evidence_links, uncertain_refs, snapshots,
                           snapshot_json, findings)
    for field_id, fact in fields.items():
        if fact["state"] == "present" and fact["extraction_quality"] == "unlinked":
            findings.append(_finding(
                "MISSING_EVIDENCE_LINK", field_ids=[field_id],
                refs=[r for r in fact["inputs"] if "source" in r]))

    bindings, enabled = _bind_rules(ruleset_doc, fields)
    if enabled == 0:
        findings.append(_finding("NO_ENABLED_RULES"))

    blocked_data = any(f["severity"] == "blocking" for f in findings) or any(
        b["input_status"] == "blocked" for b in bindings)
    blocked_exec = any(b["execution_status"] == "unsupported" for b in bindings)

    context = {
        "schema_version": SCHEMA_VERSION,
        "file_id": invoice["file_id"],
        "adapter_version": registry.ADAPTER_VERSION,
        "registry_version": registry.REGISTRY_VERSION,
        "evaluation_date": evaluation_date,
        "sources": sources,
        "extraction": {"invoice": "invoice", "reading": "reading",
                       "evidence": "evidence", "checks": "checks"},
        "ruleset": {
            "source": "ruleset",
            "schema_version": ruleset_doc["schema_version"],
            "ruleset_version": ruleset_doc["ruleset_version"],
            "policy_id": ruleset_doc["policy_id"],
        },
        "fields": fields,
        "rule_bindings": bindings,
        "findings": findings,
        "reviews": [],
        "preflight": {
            "data_status": "blocked" if blocked_data else "ready",
            "execution_status": "blocked" if blocked_exec else "supported",
        },
    }
    context["context_id"] = "dc_" + digest(canonical_bytes(context))
    if validate_result:
        validate_context(context, artifacts)
    return ContextBundle(context=context, artifacts=artifacts)


def _load_source_document(context: dict, artifacts: dict[str, bytes],
                          source_id: str) -> Any:
    source = context["sources"][source_id]
    ref = source["artifact_ref"]
    if ref is None or ref not in artifacts:
        return None
    try:
        return json.loads(artifacts[ref])
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _BINARY


def _validate_pointer_syntax(pointer: Any) -> None:
    if not isinstance(pointer, str):
        raise ContextError("input pointer must be a string")
    if pointer == "":
        return
    if not pointer.startswith("/"):
        raise ContextError(f"invalid JSON pointer {pointer!r}")
    for part in pointer[1:].split("/"):
        index = 0
        while index < len(part):
            if part[index] == "~":
                if index + 1 >= len(part) or part[index + 1] not in "01":
                    raise ContextError(f"invalid escape in pointer {pointer!r}")
                index += 2
            else:
                index += 1


def _check_input_ref(ref: dict, context: dict, fields: dict, document) -> None:
    if "field" in ref:
        if ref["field"] not in fields:
            raise ContextError(f"unknown field reference {ref['field']}")
        return
    source_id = ref["source"]
    if source_id not in context["sources"]:
        raise ContextError(f"unknown source reference {source_id}")
    pointer = ref["pointer"]
    _validate_pointer_syntax(pointer)
    doc = document(source_id)
    if doc is _BINARY:
        if pointer != "":
            raise ContextError(
                f"binary source {source_id} only allows the root pointer")
        return
    if doc is None:
        raise ContextError(f"source {source_id} has no retrievable payload")
    try:
        _pointer_get(doc, pointer)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ContextError(
            f"dangling pointer {pointer!r} in source {source_id}") from exc


def validate_context(context: dict, artifacts: dict[str, bytes]) -> None:
    validator = Draft202012Validator(load_schema(), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(context), key=lambda e: list(e.path))
    if errors:
        raise ContextError("; ".join(
            f"/{'/'.join(map(str, e.path))}: {e.message}" for e in errors[:10]))
    sources = context["sources"]
    for source_id, source in sources.items():
        ref = source["artifact_ref"]
        if source["availability"] == "unavailable":
            if ref is not None or source["sha256"] is not None:
                raise ContextError(
                    f"unavailable source {source_id} carries an artifact")
            continue
        expected = (f"sha256/{source['sha256'][:2]}/{source['sha256']}"
                    f"/decision-source-{source['kind']}")
        if ref != expected:
            raise ContextError(f"source {source_id} artifact_ref mismatch")
        if ref not in artifacts:
            raise ContextError(f"missing artifact for source {source_id}")
        if digest(artifacts[ref]) != source["sha256"]:
            raise ContextError(f"sha256 mismatch for source {source_id}")
    doc_cache: dict[str, Any] = {}

    def document(source_id: str) -> Any:
        if source_id not in doc_cache:
            doc_cache[source_id] = _load_source_document(
                context, artifacts, source_id)
        return doc_cache[source_id]

    fields = context["fields"]
    for fact in fields.values():
        for ref in fact["inputs"]:
            _check_input_ref(ref, context, fields, document)
    for finding in context["findings"]:
        for ref in finding["refs"]:
            _check_input_ref(ref, context, fields, document)
        for source_id in finding["source_ids"]:
            if source_id not in sources:
                raise ContextError(
                    f"finding references unknown source {source_id}")
        for field_id in finding["field_ids"]:
            if field_id not in fields:
                raise ContextError(
                    f"finding references unknown field {field_id}")
    for binding in context["rule_bindings"]:
        for dep in binding["dependencies"]:
            if dep["field_id"] is not None and dep["field_id"] not in fields:
                raise ContextError(
                    f"binding references unknown field {dep['field_id']}")

    visiting: set[str] = set()
    done: set[str] = set()

    def visit(field_id: str) -> None:
        if field_id in done:
            return
        if field_id in visiting:
            raise ContextError(f"cyclic field dependency at {field_id}")
        visiting.add(field_id)
        for ref in fields[field_id]["inputs"]:
            if "field" in ref:
                visit(ref["field"])
        visiting.discard(field_id)
        done.add(field_id)

    for field_id in fields:
        visit(field_id)

    expected_extraction = {"invoice": "invoice", "reading": "reading",
                           "evidence": "evidence", "checks": "checks"}
    if context["extraction"] != expected_extraction:
        raise ContextError("extraction mapping does not match internal sources")
    referenced = list(expected_extraction.values())
    ruleset_source_id = context["ruleset"]["source"]
    if not isinstance(ruleset_source_id, str) \
            or ruleset_source_id not in sources:
        raise ContextError("ruleset source reference is missing")
    referenced.append(ruleset_source_id)
    missing = [sid for sid in referenced if sid not in sources]
    if missing:
        raise ContextError(f"missing required sources: {missing}")
    ruleset_source = sources[ruleset_source_id]
    if ruleset_source["artifact_ref"] not in artifacts:
        raise ContextError("ruleset source has no retrievable bytes")
    ruleset_doc = _parse_ruleset(artifacts[ruleset_source["artifact_ref"]])
    expected_bindings, _enabled = _bind_rules(ruleset_doc, fields)
    if len(context["rule_bindings"]) != len(expected_bindings) or any(
            binding != expected for binding, expected in
            zip(context["rule_bindings"], expected_bindings)):
        raise ContextError("rule bindings are inconsistent with inputs")
    blocked_data = any(f["severity"] == "blocking" for f in context["findings"]) \
        or any(b["input_status"] == "blocked" for b in expected_bindings)
    blocked_exec = any(b["execution_status"] == "unsupported"
                       for b in expected_bindings)
    expected_preflight = {
        "data_status": "blocked" if blocked_data else "ready",
        "execution_status": "blocked" if blocked_exec else "supported",
    }
    if context["preflight"] != expected_preflight:
        raise ContextError("preflight is inconsistent with bindings/findings")
    clone = {k: v for k, v in context.items() if k != "context_id"}
    if context["context_id"] != "dc_" + digest(canonical_bytes(clone)):
        raise ContextError("context_id does not match content digest")

    if context["reviews"]:
        raise ContextError("review records are not accepted in this version")
    for source_id, kind in INTERNAL_SOURCE_KINDS.items():
        if source_id in sources and sources[source_id]["kind"] != kind:
            raise ContextError(
                f"internal source {source_id!r} has wrong kind")
    try:
        invoice_source = sources["invoice"]
        sources["checks"]
        ruleset_source_id = context["ruleset"]["source"]
    except KeyError as exc:
        raise ContextError(f"missing required source {exc}") from exc
    invoice_doc = document("invoice")
    if not isinstance(invoice_doc, dict):
        raise ContextError("invoice source has no retrievable JSON payload")
    checks_doc = document("checks")
    if not isinstance(checks_doc, dict):
        raise ContextError("checks source has no retrievable JSON payload")
    rebuilt_outcome = {"invoice": invoice_doc}
    for source_id in ("reading", "evidence", "coverage", "warnings"):
        if source_id in sources and sources[source_id]["artifact_ref"]:
            doc = document(source_id)
            if not isinstance(doc, (dict, list)):
                raise ContextError(
                    f"source {source_id} has no retrievable JSON payload")
            key = {"reading": "reading", "evidence": "evidence",
                   "coverage": "coverage", "warnings": "warnings"}[source_id]
            rebuilt_outcome[key] = doc
    rebuilt_outcome["status"] = checks_doc.get("reported_status")
    rebuilt_outcome["checks"] = checks_doc.get("reported_checks")
    rebuilt_snapshots: dict[str, SourceSnapshot] = {}
    for source_id, source in sources.items():
        if source_id in INTERNAL_SOURCE_KINDS:
            continue
        ref = source["artifact_ref"]
        if ref is not None and ref not in artifacts:
            raise ContextError(f"missing artifact for source {source_id}")
        payload = artifacts[ref] if ref is not None else None
        rebuilt_snapshots[source_id] = SourceSnapshot(
            kind=source["kind"], payload=payload,
            captured_at=source["captured_at"], as_of=source["as_of"],
            asserted_by=source["asserted_by"],
            authoritative_for=tuple(source["authoritative_for"]),
            scope=source["scope"], availability=source["availability"])
    ruleset_ref = sources[ruleset_source_id]["artifact_ref"]
    if ruleset_ref is None or ruleset_ref not in artifacts:
        raise ContextError("ruleset source has no retrievable bytes")
    replayed = _build_context(
        rebuilt_outcome, snapshots=rebuilt_snapshots,
        ruleset=artifacts[ruleset_ref],
        evaluation_date=context["evaluation_date"],
        captured_at=invoice_source["captured_at"],
        asserted_by=invoice_source["asserted_by"],
        validate_result=False)
    if replayed.context != context:
        raise ContextError("context does not match deterministic reconstruction")


def _dec_value(fact: dict) -> Decimal | None:
    if fact["state"] != "present" or fact["value"] is None:
        return None
    return Decimal(fact["value"])


def project_legacy(context: dict) -> tuple[dict, dict]:
    fields = context["fields"]

    def value(field_id: str):
        fact = fields[field_id]
        return fact["value"] if fact["state"] == "present" else None

    inv = {
        "vendor_id": value("supplier.id"),
        "nif": value("invoice.supplier_tax_id"),
        "iban": value("invoice.iban"),
        "pedido": value("invoice.order_reference"),
        "date": value("invoice.issue_date"),
        "currency": value("invoice.currency"),
        "base": _dec_value(fields["invoice.taxable_base"]),
        "iva": _dec_value(fields["invoice.vat_amount"]),
        "total": _dec_value(fields["invoice.total"]),
        "invoice_number": value("invoice.number"),
        "line_items": None,
    }
    line_amounts = fields["invoice.line_amounts"]
    if line_amounts["state"] == "present":
        inv["line_items"] = [Decimal(v) for v in line_amounts["value"]]
    vendors: dict[str, dict] = {}
    by_nif: dict[str, dict] = {}
    supplier_id = value("supplier.id")
    if supplier_id is not None:
        days = value("supplier.payment_terms_days")
        vendor = {
            "nif": value("supplier.tax_id"),
            "iban": value("supplier.iban"),
            "condiciones": f"{days} dias" if days is not None else None,
        }
        vendors[supplier_id] = vendor
        if vendor["nif"]:
            by_nif[vendor["nif"]] = vendor
    pedidos: dict[str, dict] = {}
    order_id = value("order.id")
    if order_id is not None:
        pedidos[order_id] = {
            "importe_total": _dec_value(fields["order.total"]),
            "proveedor_id": value("order.supplier_id"),
            "currency": value("order.currency"),
        }
    erp_estado: dict[str, str] = {}
    status = value("erp.order_payment_status")
    if status is not None and value("invoice.order_reference"):
        erp_estado[value("invoice.order_reference")] = status
    master = {
        "proveedores": vendors,
        "proveedores_by_nif": by_nif,
        "pedidos": pedidos,
        "erp_estado": erp_estado,
        "today": context["evaluation_date"],
        "seen_invoice_keys": set(),
        "seen_amount_date": set(),
    }
    return inv, master


def evaluate_context(bundle: ContextBundle) -> dict:
    context = bundle.context
    validate_context(context, bundle.artifacts)
    ruleset_ref = context["ruleset"]["source"]
    ruleset_doc = json.loads(
        bundle.artifacts[context["sources"][ruleset_ref]["artifact_ref"]])
    rules_by_id = {r["id"]: r for r in ruleset_doc["rules"]}
    inv, master = project_legacy(context)
    checks = []
    decisions = []
    for binding in context["rule_bindings"]:
        rule = rules_by_id[binding["rule_id"]]
        canonical = rule.get("canonical")
        canonical_name = canonical if isinstance(canonical, str) else "UNKNOWN"
        on_fail = rule.get("on_fail")
        if binding["input_status"] == "blocked" or \
                binding["execution_status"] == "unsupported":
            checks.append({"rule_id": binding["rule_id"],
                           "canonical": canonical_name,
                           "verdict": "NEEDS_REVIEW",
                           "reason": "; ".join(binding["reason_codes"]) or "blocked",
                           "on_fail": on_fail,
                           "reason_codes": binding["reason_codes"]})
            decisions.append("ESCALAR")
            continue
        if canonical == "MISSING":
            rule_params = rule.get("params") or {}
            required = rule_params.get(
                "required_fields", list(registry.DEFAULT_REQUIRED_FIELDS))
            dep_states = {d["requested_name"]: d["state"]
                          for d in binding["dependencies"]}
            missing = [name for name in required
                       if dep_states.get(name) != "bound"]
            if missing:
                verdict, reason = "FAIL", (
                    "missing required fields: " + ", ".join(missing))
            else:
                verdict, reason = "PASS", "all required fields are present"
        else:
            verdict, reason = CHECKS[canonical](
                inv, master, rule.get("params") or {})
        checks.append({"rule_id": binding["rule_id"],
                       "canonical": canonical_name, "verdict": verdict,
                       "reason": reason, "on_fail": on_fail,
                       "reason_codes": binding["reason_codes"]})
        decisions.append(on_fail if verdict == "FAIL"
                         else VERDICT_TO_DECISION[verdict])
    if any(f["severity"] == "blocking" for f in context["findings"]):
        codes = sorted({f["code"] for f in context["findings"]
                        if f["severity"] == "blocking"})
        checks.append({"rule_id": "CONTEXT", "canonical": "CONTEXT",
                       "verdict": "NEEDS_REVIEW",
                       "reason": "blocking context findings: " + ", ".join(codes),
                       "on_fail": "ESCALAR", "reason_codes": codes})
        decisions.append("ESCALAR")
    if not context["rule_bindings"]:
        decisions.append("ESCALAR")
    decision = "PAGAR"
    for candidate in decisions:
        if DECISION_RANK[candidate] > DECISION_RANK[decision]:
            decision = candidate
    return {"context_id": context["context_id"], "decision": decision,
            "checks": checks}
