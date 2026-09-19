"""Append-only event ledger. The single source of truth for the agentic layer.

Nothing in here is ever UPDATEd or DELETEd. Current state is a fold over the
events, never a mutated row -- that is what makes "follow a real decision from
input to outcome" a query instead of a reconstruction. SQLite triggers enforce
the append-only contract at the storage layer, not just by convention.

    from desk.ledger import Ledger
    led = Ledger(path)
    led.record("VERDICT", file_id="factura_5518.pdf", actor="engine",
               ruleset_version="v3.2", decision="PAGAR")

Idempotency lives here too: claim() is an atomic INSERT on a unique key, so a
replayed batch, a double click or a crashed-and-retried run can never produce a
second payment for the same (invoice, ruleset, approval).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  seq             INTEGER PRIMARY KEY AUTOINCREMENT,
  ts              TEXT    NOT NULL,
  file_id         TEXT,
  thread_id       TEXT,
  actor           TEXT    NOT NULL,
  kind            TEXT    NOT NULL,
  payload         TEXT    NOT NULL,
  ruleset_version TEXT,
  cost_usd        REAL    NOT NULL DEFAULT 0,
  ms              INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_file ON events(file_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind, seq);

CREATE TABLE IF NOT EXISTS idempotency (
  key      TEXT PRIMARY KEY,
  kind     TEXT NOT NULL,
  seq      INTEGER,
  claimed  TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS events_append_only_update
BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
CREATE TRIGGER IF NOT EXISTS events_append_only_delete
BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
CREATE TRIGGER IF NOT EXISTS idem_append_only_update
BEFORE UPDATE ON idempotency
BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
CREATE TRIGGER IF NOT EXISTS idem_append_only_delete
BEFORE DELETE ON idempotency
BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
"""

# Keys whose values are lifted out of the payload into their own columns.
_COLUMNS = ("file_id", "thread_id", "actor", "ruleset_version", "cost_usd", "ms")

# One RLock per resolved database path, shared by every Ledger opened on it:
# multi-step folds (rules.apply, approve) take the lock and then call record(),
# so it must be reentrant AND identical across instances or they deadlock.
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Ledger:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = _lock_for(self.path)
        self._local = threading.local()
        with self._connect() as con:
            con.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    # -- write ------------------------------------------------------------
    def _insert(self, con: sqlite3.Connection, kind: str, *, file_id=None,
                thread_id=None, actor="engine", ruleset_version=None,
                cost_usd=0.0, ms=0, ts=None, **payload) -> int:
        cur = con.execute(
            "INSERT INTO events (ts, file_id, thread_id, actor, kind, payload,"
            " ruleset_version, cost_usd, ms) VALUES (?,?,?,?,?,?,?,?,?)",
            (ts or _now(), file_id, thread_id, actor, kind,
             json.dumps(payload, ensure_ascii=False, default=str),
             ruleset_version, float(cost_usd or 0), int(ms or 0)),
        )
        return cur.lastrowid

    def record(self, kind: str, *, file_id=None, thread_id=None, actor="engine",
               ruleset_version=None, cost_usd=0.0, ms=0, ts=None, **payload) -> int:
        """Append one event. Returns its seq (the total order of the system)."""
        with self._lock:
            con = getattr(self._local, "con", None)
            if con is not None:
                return self._insert(con, kind, file_id=file_id, thread_id=thread_id,
                                    actor=actor, ruleset_version=ruleset_version,
                                    cost_usd=cost_usd, ms=ms, ts=ts, **payload)
            with self._connect() as con:
                return self._insert(con, kind, file_id=file_id, thread_id=thread_id,
                                    actor=actor, ruleset_version=ruleset_version,
                                    cost_usd=cost_usd, ms=ms, ts=ts, **payload)

    def _claim(self, con: sqlite3.Connection, key: str, kind: str) -> bool:
        try:
            con.execute(
                "INSERT INTO idempotency (key, kind, claimed) VALUES (?,?,?)",
                (key, kind, _now()),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def claim(self, key: str, kind: str) -> bool:
        """Atomically claim an idempotency key. False => already done before."""
        with self._lock:
            con = getattr(self._local, "con", None)
            if con is not None:
                return self._claim(con, key, kind)
            with self._connect() as con:
                return self._claim(con, key, kind)

    def link_claim(self, key: str, seq: int) -> None:
        """Append CLAIM_LINKED; the idempotency row itself is never UPDATEd."""
        self.record("CLAIM_LINKED", actor="ledger", key=key, claim_seq=seq)

    def claimed(self, key: str) -> dict | None:
        with self._read_con() as con:
            row = con.execute("SELECT * FROM idempotency WHERE key=?", (key,)).fetchone()
            if not row:
                return None
            out = dict(row)
            linked = con.execute(
                "SELECT payload FROM events WHERE kind='CLAIM_LINKED'"
                " AND json_extract(payload,'$.key')=? ORDER BY seq DESC LIMIT 1",
                (key,),
            ).fetchone()
            if linked:
                out["seq"] = json.loads(linked["payload"]).get("claim_seq")
            return out

    # -- atomic multi-event writes ----------------------------------------
    @contextmanager
    def transaction(self):
        """BEGIN IMMEDIATE on one connection; nested record/claim reuse it."""
        with self._lock:
            if getattr(self._local, "con", None) is not None:
                yield self._local.con
                return
            con = self._connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                self._local.con = con
                yield con
                con.commit()
            except BaseException:
                con.rollback()
                raise
            finally:
                self._local.con = None
                con.close()

    # -- read -------------------------------------------------------------
    @contextmanager
    def _read_con(self):
        """Reads join an open transaction's connection so they see its writes;
        outside one they use a short-lived connection that closes itself."""
        con = getattr(self._local, "con", None)
        if con is not None:
            yield con
        else:
            with self._connect() as con:
                yield con

    @staticmethod
    def _hydrate(row: sqlite3.Row) -> dict:
        out = dict(row)
        out["payload"] = json.loads(out["payload"])
        return out

    @staticmethod
    def _fetchone(con, sql, args):
        row = con.execute(sql, args).fetchone()
        return Ledger._hydrate(row) if row else None

    def all_events(self) -> list[dict]:
        with self._read_con() as con:
            rows = con.execute("SELECT * FROM events ORDER BY seq").fetchall()
        return [self._hydrate(r) for r in rows]

    def for_file(self, file_id: str) -> list[dict]:
        """File-scoped events plus EMAIL_* group events that name this file
        in their payload.file_ids list."""
        with self._read_con() as con:
            rows = con.execute(
                "SELECT * FROM events WHERE file_id=? OR (kind LIKE 'EMAIL%'"
                " AND json_type(payload,'$.file_ids')='array' AND EXISTS"
                " (SELECT 1 FROM json_each(events.payload,'$.file_ids') je"
                " WHERE je.value=?)) ORDER BY seq",
                (file_id, file_id),
            ).fetchall()
        return [self._hydrate(r) for r in rows]

    def recent(self, limit: int = 200, kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM events"
        args: list = []
        if kind:
            sql += " WHERE kind=?"
            args.append(kind)
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(limit)
        with self._read_con() as con:
            rows = con.execute(sql, args).fetchall()
        return [self._hydrate(r) for r in rows]

    def by_kind(self, *kinds: str) -> list[dict]:
        marks = ",".join("?" * len(kinds))
        with self._read_con() as con:
            rows = con.execute(
                f"SELECT * FROM events WHERE kind IN ({marks}) ORDER BY seq", kinds
            ).fetchall()
        return [self._hydrate(r) for r in rows]

    def last(self, file_id: str, kind: str) -> dict | None:
        with self._read_con() as con:
            row = con.execute(
                "SELECT * FROM events WHERE file_id=? AND kind=? ORDER BY seq DESC LIMIT 1",
                (file_id, kind),
            ).fetchone()
        return self._hydrate(row) if row else None

    def cost_by_file(self) -> dict[str, float]:
        with self._read_con() as con:
            rows = con.execute(
                "SELECT file_id, SUM(cost_usd) c FROM events"
                " WHERE file_id IS NOT NULL GROUP BY file_id"
            ).fetchall()
        return {r["file_id"]: r["c"] or 0.0 for r in rows}

    def totals(self) -> dict:
        with self._read_con() as con:
            row = con.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(cost_usd),0) cost,"
                " COALESCE(SUM(ms),0) ms FROM events"
            ).fetchone()
        return {"events": row["n"], "cost_usd": row["cost"], "ms": row["ms"]}

    def files(self) -> list[str]:
        with self._read_con() as con:
            rows = con.execute(
                "SELECT DISTINCT file_id FROM events WHERE file_id IS NOT NULL"
                " ORDER BY file_id"
            ).fetchall()
        return [r["file_id"] for r in rows]

    # -- payments: the only place money moves ------------------------------
    def register_payment(self, file_id: str, ruleset_version: str,
                         approval_id: str, actor: str, thread_id=None,
                         note=None, remesa=None, value_date=None) -> dict:
        """Approve + pay one invoice, atomically, inside BEGIN IMMEDIATE.

        Reads the latest VERDICT and any existing payment INSIDE the
        transaction so a concurrent approver cannot interleave. Returns
        {status: paid|blocked|refused, ...}; every outcome is itself an event.
        """
        import hashlib

        with self.transaction() as con:
            verdict = self._fetchone(
                con, "SELECT * FROM events WHERE file_id=? AND kind='VERDICT'"
                     " ORDER BY seq DESC LIMIT 1", (file_id,))
            if verdict is None:
                seq = self._insert(con, "PAYMENT_REFUSED", file_id=file_id,
                                   actor="agent:tesorero", thread_id=thread_id,
                                   requested_by=actor, decision=None,
                                   reason="no hay veredicto registrado para esta factura")
                return {"status": "refused", "file_id": file_id, "seq": seq}
            # The idempotency key binds (invoice, ruleset that decided it,
            # approval id): recomputed here from the verdict read INSIDE this
            # transaction, so a stale caller-supplied version cannot collide.
            key = hashlib.sha256(
                f"{file_id}|{verdict['ruleset_version']}|{approval_id}".encode()
            ).hexdigest()
            vp = verdict["payload"]
            checks = vp.get("checks") or []
            invoice = (vp.get("evaluation") or {}).get("invoice") or vp.get("invoice") or {}
            blocking = [c["canonical"] for c in checks
                        if c.get("effect", "PAGAR" if c.get("verdict") == "PASS"
                                 else "ESCALAR") != "PAGAR"]

            def refuse(reason):
                seq = self._insert(
                    con, "PAYMENT_REFUSED", file_id=file_id, actor="agent:tesorero",
                    thread_id=thread_id, requested_by=actor, decision=vp.get("decision"),
                    driven_by=blocking, reason=reason)
                return {"status": "refused", "file_id": file_id, "seq": seq,
                        "idempotency_key": key, "reason": reason}

            # Two layers keep this honest: the engine's verdict decides whether
            # it is payable at all (NO_PAGAR is never overridden: reversing it
            # means changing the RULE that produced it, which goes through diff
            # + backtest like any other), and this function then enforces the
            # mechanical preconditions itself inside the same transaction.
            if vp.get("decision") == "NO_PAGAR":
                return refuse("un veredicto NO_PAGAR no se paga aprobandolo; "
                              "cambia la regla")
            if vp.get("decision") not in ("PAGAR", "ESCALAR"):
                return refuse(f"veredicto {vp.get('decision')} no pagable")
            try:
                amount = Decimal(str(invoice.get("total"))).quantize(Decimal("0.01"))
            except (TypeError, ValueError, ArithmeticError):
                amount = None
            if amount is None or not amount.is_finite() or amount <= 0:
                return refuse("importe ausente o invalido en la evaluacion")
            if not invoice.get("iban"):
                return refuse("falta el IBAN del proveedor en la evaluacion")
            if invoice.get("currency") != "EUR":
                return refuse(f"moneda {invoice.get('currency')} no pagable por SEPA EUR")

            paid = self._fetchone(
                con, "SELECT * FROM events WHERE file_id=? AND kind='PAYMENT_REGISTERED'"
                     " ORDER BY seq DESC LIMIT 1", (file_id,))
            if paid:
                seq = self._insert(
                    con, "PAYMENT_BLOCKED", file_id=file_id, actor="agent:tesorero",
                    thread_id=thread_id,
                    idempotency_key=paid["payload"].get("idempotency_key"),
                    reason="la factura ya tiene un pago registrado",
                    first_claimed=paid["ts"],
                    prior_remesa=paid["payload"].get("remesa"))
                return {"status": "blocked", "file_id": file_id, "seq": seq,
                        "idempotency_key": key, "reason": "already_paid"}

            if not self._claim(con, key, "PAYMENT_REGISTERED"):
                prior = con.execute(
                    "SELECT * FROM idempotency WHERE key=?", (key,)).fetchone()
                seq = self._insert(
                    con, "PAYMENT_BLOCKED", file_id=file_id, actor="agent:tesorero",
                    thread_id=thread_id, idempotency_key=key,
                    reason="clave de idempotencia ya reclamada",
                    first_claimed=prior["claimed"] if prior else None)
                return {"status": "blocked", "file_id": file_id, "seq": seq,
                        "idempotency_key": key, "reason": "idempotency"}

            self._insert(con, "HUMAN_APPROVED", file_id=file_id, actor=actor,
                         thread_id=thread_id, approval_id=approval_id,
                         batch_size=1, note=note,
                         decision_at_approval=vp.get("decision"))
            seq = self._insert(
                con, "PAYMENT_REGISTERED", file_id=file_id, actor="agent:tesorero",
                thread_id=thread_id, ruleset_version=verdict["ruleset_version"],
                idempotency_key=key, approval_id=approval_id,
                amount=float(amount), currency="EUR", remesa=remesa,
                rail="sepa-pain.001", value_date=value_date,
                creditor={"invoice_number": invoice.get("invoice_number"),
                          "vendor": invoice.get("vendor") or vp.get("invoice", {}).get("vendor"),
                          "vendor_email": vp.get("invoice", {}).get("vendor_email")
                          or invoice.get("vendor_email"),
                          "iban": invoice.get("iban"),
                          "pedido": invoice.get("pedido"),
                          "total": float(amount), "currency": "EUR"})
            self._insert(con, "CLAIM_LINKED", actor="ledger", key=key, claim_seq=seq)
            return {"status": "paid", "file_id": file_id, "seq": seq,
                    "idempotency_key": key, "approval_id": approval_id,
                    "remesa": remesa, "amount": float(amount)}
