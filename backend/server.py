"""JSON API for the invoice review pipeline on `main`.

Lists the invoices under `facturas/*.pdf`, lets you launch the real revision
(Helmcode OCR + DeepSeek interpretation + rules_ingestion business checks) for
a batch or a single file, and reports PASS/FAIL/NEEDS_REVIEW per rule with the
reason. Stdlib only (no web framework). The presentation layer is frontend/
(Next.js), which consumes this over CORS-enabled JSON.

The HTTP surface is English. The stores underneath keep their Spanish
vocabulary — `estado`/`hecha` are real column names in the legacy SQLite
schema — so the translation happens here, at the boundary, and nowhere else.
`PAGAR`/`ESCALAR`/`NO_PAGAR` are left alone: they are the `engine_records`
check constraint and the evaluator's own output, not display strings.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.results_store import PostgresResultsStore, separate_states  # noqa: E402
from backend.run_revision import (  # noqa: E402
    FACTURAS_DIR,
    revisar_lote_sync,
    run_status,
)

STORE = None
BATCH_SIZE = 20
PAGE_LIMIT = 50
PAGE_LIMIT_MAX = 200

#: Store vocabulary -> HTTP vocabulary. The values on the left are the legacy
#: SQLite column values; the ones on the right are the published contract.
STATUS = {"pendiente": "pending", "procesando": "processing",
          "hecha": "done", "error": "error"}

_state_lock = threading.Lock()
_processing = False
_last_error = None


def get_store():
    global STORE
    if STORE is None:
        from rules_ingestion.engine import InvoiceDecisionEngine

        STORE = PostgresResultsStore(InvoiceDecisionEngine.from_supabase())
    return STORE


def _invoice_ids() -> list[str]:
    return sorted({p.name for p in FACTURAS_DIR.glob("*.pdf")} | set(get_store().all()))


def _launch_background(file_ids: list[str], request_key: str) -> None:
    global _processing, _last_error
    try:
        revisar_lote_sync(file_ids, store=get_store(), request_key=request_key)
    except Exception as exc:  # keep the API alive; surface the error instead of crashing the thread
        _last_error = type(exc).__name__
    finally:
        with _state_lock:
            _processing = False


def _summary() -> dict:
    stored = get_store().all()
    counts = {"pending": 0, "processing": 0, "done": 0, "error": 0}
    decisions = {"PAGAR": 0, "ESCALAR": 0, "NO_PAGAR": 0}
    invoices = []
    for file_id in _invoice_ids():
        row = stored.get(file_id)
        status = STATUS.get(row["estado"], row["estado"]) if row else "pending"
        counts[status] = counts.get(status, 0) + 1
        decision = row.get("decision") if row else None
        if decision in decisions:
            decisions[decision] += 1
        invoices.append({"file_id": file_id, "status": status, "decision": decision})
    return {
        "total": len(invoices),
        "counts": counts,
        "decisions": decisions,
        "invoices": invoices,
        "batch_size": BATCH_SIZE,
        "processing": _processing,
        "error": _last_error,
    }


def _row(file_id: str, row: dict | None) -> dict:
    """One list row. Deliberately light: no evidence packet is loaded here."""
    if row is None:
        # On disk but not yet in Postgres: pending work, not a result.
        row = {}
        status = "pending"
    else:
        status = STATUS.get(row["estado"], row["estado"])
    return {"file_id": file_id, "status": status,
            "input_id": row.get("input_id"), "batch_id": row.get("batch_id"),
            "error": row.get("error"), **separate_states(row)}


def _integer(fields: dict[str, list[str]], name: str, default: int) -> int:
    raw = (fields.get(name) or [""])[0]
    if not raw:
        return default
    if not raw.isdigit():
        raise ValueError(name)
    return int(raw)


def _invoices_page(fields: dict[str, list[str]]) -> dict:
    limit = _integer(fields, "limit", PAGE_LIMIT)
    offset = _integer(fields, "offset", 0)
    if not 1 <= limit <= PAGE_LIMIT_MAX:
        raise ValueError("limit")
    decision = (fields.get("decision") or [""])[0]
    status = (fields.get("status") or [""])[0]
    q = (fields.get("q") or [""])[0].lower()

    # One `latest_results()` round trip per request: `_invoice_ids()` would repeat it.
    stored = get_store().all()
    every = sorted({p.name for p in FACTURAS_DIR.glob("*.pdf")} | set(stored))
    rows = [_row(file_id, stored.get(file_id)) for file_id in every]
    if decision:
        rows = [r for r in rows if r["evaluation"]["preliminary_decision"] == decision]
    if status:
        rows = [r for r in rows if r["status"] == status]
    if q:
        rows = [r for r in rows if q in r["file_id"].lower()]
    return {"total": len(rows), "limit": limit, "offset": offset,
            "rows": rows[offset:offset + limit]}


def _health() -> dict:
    try:
        dependencies = get_store().health()
    except Exception as exc:  # noqa: BLE001 - an unbuildable store is a degraded state
        dependencies = {"postgres": {"ok": False, "error": type(exc).__name__}}
    return {"status": "ok" if all(d["ok"] for d in dependencies.values()) else "degraded",
            "dependencies": dependencies,
            # Reported alongside, never as the health signal itself.
            "processing": _processing}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, status: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._json(204, {})

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/summary":
            self._json(200, _summary())
        elif url.path.startswith("/api/invoice/"):
            self._invoice(url.path[len("/api/invoice/"):])
        elif url.path.startswith("/api/runs/"):
            key = unquote(url.path[len("/api/runs/"):])
            try:
                self._json(200, run_status(get_store().engine, key))
            except KeyError:
                self._json(404, {"error": "run_not_found"})
        elif url.path == "/api/invoices":
            try:
                self._json(200, _invoices_page(parse_qs(url.query)))
            except ValueError as exc:
                self._json(422, {"error": "invalid_query", "field": str(exc)})
        elif url.path == "/api/health":
            self._json(200, _health())
        elif url.path == "/api/status":
            self._json(200, {"processing": _processing, "error": _last_error})
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/api/runs":
            try:
                ok = self._launch(parse_qs(self._body()))
                self._json(200, {"ok": ok, "processing": _processing})
            except ValueError:
                self._json(422, {"ok": False, "error": "invalid_run_request"})
        else:
            self._json(404, {"error": "not_found"})

    def _body(self) -> str:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length).decode("utf-8", errors="replace") if length else ""

    def _launch(self, fields: dict[str, list[str]]) -> bool:
        global _processing
        request_key = (fields.get("request_key") or [""])[0]
        if not 1 <= len(request_key) <= 200:
            raise ValueError("request_key is required")
        target = (fields.get("target") or [""])[0]
        if target == "one" and fields.get("file_id"):
            file_ids = [fields["file_id"][0]]
        else:
            stored = get_store().all()
            every = _invoice_ids()
            pending = [f for f in every if stored.get(f, {}).get("estado") not in ("hecha",)]
            file_ids = pending[:BATCH_SIZE]
        if not file_ids:
            return False
        with _state_lock:
            if _processing:
                return False
            _processing = True
        threading.Thread(target=_launch_background, args=(file_ids, request_key), daemon=True).start()
        return True

    def _invoice(self, file_id: str):
        file_id = unquote(file_id)
        if Path(file_id).name != file_id or "/" in file_id or "\\" in file_id:
            self._json(422, {"error": "invalid_file_id"})
            return
        row = get_store().get(file_id)
        if row is None and not (FACTURAS_DIR / file_id).is_file():
            self._json(404, {"error": "invoice_not_found"})
            return
        if row is None:
            self._json(200, {"file_id": file_id, "status": "pending", "decision": None,
                             "checks": [], "fields": {}, "error": None,
                             "provenance": None, **separate_states({})})
            return
        self._json(200, {
            "file_id": file_id,
            "status": STATUS.get(row["estado"], row["estado"]),
            "decision": row.get("decision"),
            "checks": json.loads(row["checks"]) if row["checks"] else [],
            "fields": json.loads(row["raw_invoice"]) if row["raw_invoice"] else {},
            "error": row.get("error"),
            "evaluation_record_id": row.get("evaluation_record_id"),
            "review_record_id": row.get("review_record_id"),
            "evaluation_result": row.get("evaluation_result"),
            "contextual_review": row.get("contextual_review"),
            "review_status": row.get("review_status"),
            "attention_required": row.get("attention_required"),
            # The exact artifacts behind this decision, and the five axes kept apart.
            "provenance": row.get("provenance"),
            **separate_states(row),
        })


def main(port: int = 8010) -> int:
    get_store()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"revision API on http://127.0.0.1:{port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
