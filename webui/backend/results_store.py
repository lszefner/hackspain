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


class ResultsStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        con = self._connect()
        con.execute(SCHEMA)
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
                " ON CONFLICT(file_id) DO UPDATE SET estado='procesando', actualizado_at=datetime('now')",
                (file_id,),
            )

    def set_hecha(self, file_id: str, *, decision: str, raw_invoice: dict, checks: list) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO revisiones (file_id, estado, decision, raw_invoice, checks, error)"
                " VALUES (?, 'hecha', ?, ?, ?, NULL)"
                " ON CONFLICT(file_id) DO UPDATE SET estado='hecha', decision=excluded.decision,"
                " raw_invoice=excluded.raw_invoice, checks=excluded.checks, error=NULL,"
                " actualizado_at=datetime('now')",
                (file_id, decision, _dumps(raw_invoice), _dumps(checks)),
            )

    def set_error(self, file_id: str, error: str) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO revisiones (file_id, estado, error) VALUES (?, 'error', ?)"
                " ON CONFLICT(file_id) DO UPDATE SET estado='error', error=excluded.error,"
                " actualizado_at=datetime('now')",
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
