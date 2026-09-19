"""Local persistence for the review UI: one row per invoice file, across runs."""
from __future__ import annotations

import json
import sqlite3
import threading
from decimal import Decimal
from pathlib import Path


def _default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=_default)

SCHEMA = """
CREATE TABLE IF NOT EXISTS revisiones (
  file_id TEXT PRIMARY KEY,
  estado TEXT NOT NULL,            -- pendiente | procesando | hecha | error
  decision TEXT,                   -- PAGAR | ESCALAR | NO_PAGAR (derived from checks)
  raw_invoice TEXT,                -- ingestion invoice.json (evidence-linked)
  checks TEXT,                     -- rules_ingestion.checks.run_checks() output
  error TEXT,
  actualizado_at TEXT DEFAULT (datetime('now'))
);
"""

ADDITIVE_COLUMNS = ("decision_context", "context_receipt")


class ResultsStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        con = self._connect()
        con.execute(SCHEMA)
        existing = {row[1] for row in con.execute("PRAGMA table_info(revisiones)")}
        for column in ADDITIVE_COLUMNS:
            if column not in existing:
                con.execute(f"ALTER TABLE revisiones ADD COLUMN {column} TEXT")
        con.commit()
        con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def set_procesando(self, file_id: str) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO revisiones (file_id, estado) VALUES (?, 'procesando')"
                " ON CONFLICT(file_id) DO UPDATE SET estado='procesando', decision=NULL,"
                " checks=NULL, error=NULL, actualizado_at=datetime('now')",
                (file_id,),
            )

    def set_hecha(self, file_id: str, *, decision: str, raw_invoice: dict,
                  checks: list, context: dict | None = None,
                  receipt: dict | None = None) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO revisiones (file_id, estado, decision, raw_invoice, checks,"
                " decision_context, context_receipt, error)"
                " VALUES (?, 'hecha', ?, ?, ?, ?, ?, NULL)"
                " ON CONFLICT(file_id) DO UPDATE SET estado='hecha', decision=excluded.decision,"
                " raw_invoice=excluded.raw_invoice, checks=excluded.checks,"
                " decision_context=excluded.decision_context,"
                " context_receipt=excluded.context_receipt, error=NULL,"
                " actualizado_at=datetime('now')",
                (file_id, decision, _dumps(raw_invoice), _dumps(checks),
                 _dumps(context) if context is not None else None,
                 _dumps(receipt) if receipt is not None else None),
            )

    def set_error(self, file_id: str, error: str) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO revisiones (file_id, estado, error) VALUES (?, 'error', ?)"
                " ON CONFLICT(file_id) DO UPDATE SET estado='error', error=excluded.error,"
                " decision=NULL, checks=NULL, actualizado_at=datetime('now')",
                (file_id, error),
            )

    def get(self, file_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM revisiones WHERE file_id=?", (file_id,)).fetchone()
            return dict(row) if row else None

    def all(self) -> dict[str, dict]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM revisiones").fetchall()
            return {r["file_id"]: dict(r) for r in rows}

    def seen_invoice_keys(self) -> set:
        out = set()
        for row in self.all().values():
            if row["estado"] != "hecha" or not row["raw_invoice"]:
                continue
            inv = json.loads(row["raw_invoice"])
            out.add((inv.get("invoice_number"), inv.get("vendor_id")))
        return out

    def seen_amount_date(self) -> set:
        out = set()
        for row in self.all().values():
            if row["estado"] != "hecha" or not row["raw_invoice"]:
                continue
            inv = json.loads(row["raw_invoice"])
            out.add((str(inv.get("total")), inv.get("date")))
        return out

    def processed_history_snapshot(self, *, captured_at: str,
                                   exclude_file_id: str | None = None):
        from rules_ingestion.decision_context import SourceSnapshot

        records = []
        for row in self.all().values():
            if exclude_file_id is not None \
                    and row["file_id"] == exclude_file_id:
                continue
            if not row.get("decision_context") and not row.get("raw_invoice"):
                continue
            record = {"file_id": row["file_id"]}
            context = None
            if row.get("decision_context"):
                try:
                    context = json.loads(row["decision_context"])
                except (TypeError, json.JSONDecodeError):
                    context = None
            fields = (context or {}).get("fields") or {}
            if context:
                record["context_id"] = context.get("context_id")
                record["captured_at"] = (
                    (context.get("sources") or {}).get("invoice") or {}
                ).get("captured_at")

                def fact_value(field_id, _fields=fields):
                    fact = _fields.get(field_id) or {}
                    if fact.get("state") == "present":
                        return fact.get("value")
                    return None

                record["invoice_number"] = fact_value("invoice.number")
                record["supplier_id"] = fact_value("supplier.id")
                record["total"] = fact_value("invoice.total")
                record["currency"] = fact_value("invoice.currency")
                record["issue_date"] = fact_value("invoice.issue_date")
            elif row.get("raw_invoice"):
                try:
                    inv = json.loads(row["raw_invoice"])
                except (TypeError, json.JSONDecodeError):
                    inv = {}
                record["invoice_number"] = inv.get("invoice_number")
                if inv.get("vendor_id") is not None:
                    record["supplier_id"] = inv.get("vendor_id")
                record["total"] = (None if inv.get("total") is None
                                   else str(inv.get("total")))
                if inv.get("currency") is not None:
                    record["currency"] = inv.get("currency")
                record["issue_date"] = inv.get("date")
            else:
                continue
            records.append(record)
        return SourceSnapshot(
            kind="history",
            payload={"kind": "processed", "records": records,
                     "complete": False},
            captured_at=captured_at, asserted_by="revision-ui-store",
            authoritative_for=("history.processed",),
            scope="latest-per-file UI review records",
            availability="partial")
