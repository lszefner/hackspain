"""Runtime contracts for the invoice ingestion pipeline.

The benchmark schemas are the source of truth.  This module only loads and
validates them; it deliberately does not import benchmark scoring or reference
data.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, SchemaError


def canonical_bytes(value: Any) -> bytes:
    """Return the stable UTF-8 representation used for artifact hashes."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(data: bytes | bytearray | memoryview) -> str:
    """Return the SHA-256 hex digest of bytes."""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("digest expects bytes")
    return hashlib.sha256(bytes(data)).hexdigest()


class Contracts:
    """Load versioned JSON Schemas from an explicit directory."""

    def __init__(self, schema_dir: str | Path):
        self.schema_dir = Path(schema_dir)
        if not self.schema_dir.is_dir():
            raise FileNotFoundError(f"schema directory not found: {self.schema_dir}")
        self.schemas: dict[str, dict[str, Any]] = {}
        self.hashes: dict[str, str] = {}
        for path in sorted(self.schema_dir.glob("*.json")):
            try:
                schema_bytes = path.read_bytes()
                value = json.loads(schema_bytes)
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid schema {path.name}") from exc
            name = path.stem
            if not isinstance(value, dict):
                raise TypeError(f"schema {path.name} must be a JSON object")
            try:
                Draft202012Validator.check_schema(value)
            except SchemaError as exc:
                raise ValueError(f"invalid schema {path.name}") from exc
            self.schemas[name] = value
            # Hash the exact checked-in bytes: whitespace and key ordering are
            # part of the schema snapshot used to identify a run.
            self.hashes[name] = digest(schema_bytes)

    @classmethod
    def from_snapshot(cls, schemas: dict, hashes: dict):
        """Restore accepted contracts without consulting mutable checkout files."""
        import copy

        for schema in schemas.values():
            Draft202012Validator.check_schema(schema)
        instance = cls.__new__(cls)
        instance.schema_dir = None
        instance.schemas = copy.deepcopy(schemas)
        instance.hashes = dict(hashes)
        return instance

    def validate(self, name: str, value: Any) -> Any:
        """Validate ``value`` and return it, raising a concise ValueError."""

        if name not in self.schemas:
            raise KeyError(f"unknown schema: {name}")
        errors = sorted(
            Draft202012Validator(
                self.schemas[name], format_checker=FormatChecker()
            ).iter_errors(value),
            key=lambda error: list(error.path),
        )
        if errors:
            details = []
            for error in errors[:20]:
                pointer = "/" + "/".join(
                    str(part).replace("~", "~0").replace("/", "~1")
                    for part in error.path
                )
                details.append(f"{pointer or '/'}: {error.validator}")
            raise ValueError("; ".join(details))

        if name == "reading":
            self._validate_reading(value)
        elif name == "invoice":
            positions = [line["position"] for line in value["lines"]]
            if positions != list(range(1, len(positions) + 1)):
                raise ValueError("/lines: positions must be contiguous and ordered")
        return value

    @staticmethod
    def _validate_reading(value: dict[str, Any]) -> None:
        pages = value["pages"]
        if [page["page"] for page in pages] != list(range(1, len(pages) + 1)):
            raise ValueError("/pages: pages must be contiguous and ordered")
        ids: set[str] = set()
        for page in pages:
            for block in page["blocks"]:
                for item in (block,):
                    if item["id"] in ids:
                        raise ValueError("/pages: duplicate reading ID")
                    ids.add(item["id"])
                if block["kind"] == "table" and block["text"]:
                    raise ValueError("table block text must be empty")
                if block["kind"] != "table" and block["rows"]:
                    raise ValueError("only table blocks may contain rows")
                for row in block["rows"]:
                    if row["id"] in ids:
                        raise ValueError("/pages: duplicate reading ID")
                    ids.add(row["id"])
                    occupied: set[int] = set()
                    for cell in row["cells"]:
                        if cell["id"] in ids:
                            raise ValueError("/pages: duplicate reading ID")
                        ids.add(cell["id"])
                        slots = set(
                            range(cell["column"], cell["column"] + cell["column_span"])
                        )
                        if occupied & slots:
                            raise ValueError("table row contains overlapping cells")
                        occupied.update(slots)

    def blank_invoice(self, file_id: str) -> dict[str, Any]:
        """Create a schema-shaped invoice with no inferred factual values."""

        invoice = {
            "schema_version": "0.1",
            "file_id": file_id,
            "document_type": "unknown",
            "invoice_number": None,
            "issue_date": None,
            "purchase_order_reference": None,
            "currency": None,
            "supplier": {
                "name": None,
                "tax_id": None,
                "location": None,
                "address": None,
            },
            "customer": {
                "name": None,
                "tax_id": None,
                "location": None,
                "address": None,
            },
            "payment": {"iban": None},
            "lines": [],
            "taxes": [],
            "totals": {"taxable_base": None, "total": None},
            "annotations": [],
            "additional_fields": [],
            "issues": [],
        }
        self.validate("invoice", invoice)
        return invoice


def blank_invoice(file_id: str, schema_dir: str | Path | None = None) -> dict[str, Any]:
    """Build a blank invoice using the repository's current invoice schema.

    The method on :class:`Contracts` should be used by long-lived workers so
    schemas are loaded and hashed once.  This small convenience factory keeps
    provider adapters and offline fixtures independent of a global singleton.
    """

    path = (
        Path(schema_dir)
        if schema_dir is not None
        else Path(__file__).resolve().parents[1] / "benchmark" / "schemas"
    )
    return Contracts(path).blank_invoice(file_id)
