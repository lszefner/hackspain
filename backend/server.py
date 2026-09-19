"""JSON API for the invoice review pipeline on `main`.

Lists the 500 facturas/*.pdf, lets you launch the real revision (Helmcode OCR
+ DeepSeek interpretation + rules_ingestion business checks) for a batch or a
single file, and reports PASS/FAIL/NEEDS_REVIEW per rule with the reason.
Stdlib only (no web framework). The presentation layer is frontend/
(Next.js), which consumes this over CORS-enabled JSON.
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

from backend.results_store import PostgresResultsStore  # noqa: E402
from backend.run_revision import (  # noqa: E402
    FACTURAS_DIR,
    revisar_lote_sync,
    run_status,
)

STORE = None
LOTE_TAMANO = 20

_estado_lock = threading.Lock()
_procesando = False
_ultimo_error = None


def get_store():
    global STORE
    if STORE is None:
        from rules_ingestion.engine import InvoiceDecisionEngine

        STORE = PostgresResultsStore(InvoiceDecisionEngine.from_supabase())
    return STORE


def _facturas() -> list[str]:
    return sorted({p.name for p in FACTURAS_DIR.glob("*.pdf")} | set(get_store().all()))


def _lanzar_en_fondo(file_ids: list[str], request_key: str) -> None:
    global _procesando, _ultimo_error
    try:
        revisar_lote_sync(file_ids, store=get_store(), request_key=request_key)
    except Exception as exc:  # keep the API alive; surface the error instead of crashing the thread
        _ultimo_error = type(exc).__name__
    finally:
        with _estado_lock:
            _procesando = False


def _resumen() -> dict:
    todas = _facturas()
    estado = get_store().all()
    conteo = {"pendiente": 0, "procesando": 0, "hecha": 0, "error": 0}
    decisiones = {"PAGAR": 0, "ESCALAR": 0, "NO_PAGAR": 0}
    facturas = []
    for file_id in todas:
        row = estado.get(file_id)
        st = row["estado"] if row else "pendiente"
        conteo[st] = conteo.get(st, 0) + 1
        decision = row.get("decision") if row else None
        if decision in decisiones:
            decisiones[decision] += 1
        facturas.append({"file_id": file_id, "estado": st, "decision": decision})
    return {
        "total": len(todas),
        "conteo": conteo,
        "decisiones": decisiones,
        "facturas": facturas,
        "lote_tamano": LOTE_TAMANO,
        "procesando": _procesando,
        "error": _ultimo_error,
    }


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
        if url.path == "/api/salud":
            self._salud()
        elif url.path == "/api/resumen":
            self._json(200, _resumen())
        elif url.path.startswith("/api/factura/"):
            rest = url.path[len("/api/factura/"):]
            if rest.endswith("/flujo"):
                self._flujo(rest[:-len("/flujo")])
            else:
                self._factura(rest)
        elif url.path.startswith('/api/ejecucion/'):
            key = unquote(url.path[len('/api/ejecucion/'):])
            try:
                self._json(200, run_status(get_store().engine, key))
            except KeyError:
                self._json(404, {'error': 'run_not_found'})
        elif url.path == "/api/estado":
            self._json(200, {"procesando": _procesando, "error": _ultimo_error})
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/api/lanzar":
            try:
                ok = self._lanzar(parse_qs(self._body()))
                self._json(200, {"ok": ok, "procesando": _procesando})
            except ValueError:
                self._json(422, {'ok': False, 'error': 'invalid_run_request'})
        else:
            self._json(404, {"error": "not_found"})

    def _body(self) -> str:
        longitud = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(longitud).decode("utf-8", errors="replace") if longitud else ""

    def _lanzar(self, campos: dict[str, list[str]]) -> bool:
        global _procesando
        request_key = (campos.get('request_key') or [''])[0]
        if not 1 <= len(request_key) <= 200:
            raise ValueError('request_key is required')
        objetivo = (campos.get("objetivo") or [""])[0]
        if objetivo == "una" and campos.get("file_id"):
            file_ids = [campos["file_id"][0]]
        else:
            todas = _facturas()
            estado = get_store().all()
            pendientes = [f for f in todas if estado.get(f, {}).get("estado") not in ("hecha",)]
            file_ids = pendientes[:LOTE_TAMANO]
        if not file_ids:
            return False
        with _estado_lock:
            if _procesando:
                return False
            _procesando = True
        threading.Thread(target=_lanzar_en_fondo, args=(file_ids, request_key), daemon=True).start()
        return True

    def _factura(self, file_id: str):
        file_id = unquote(file_id)
        if Path(file_id).name != file_id or '/' in file_id or '\\' in file_id:
            self._json(422, {'error': 'invalid_file_id'})
            return
        row = get_store().get(file_id)
        if row is None and not (FACTURAS_DIR / file_id).is_file():
            self._json(404, {"error": "factura_no_encontrada"})
            return
        if row is None:
            self._json(200, {"file_id": file_id, "estado": "pendiente", "decision": None,
                            "checks": [], "campos": {}, "error": None})
            return
        self._json(200, {
            "file_id": file_id,
            "estado": row["estado"],
            "decision": row.get("decision"),
            "checks": json.loads(row["checks"]) if row["checks"] else [],
            "campos": json.loads(row["raw_invoice"]) if row["raw_invoice"] else {},
            "error": row.get("error"),
            'evaluation_record_id': row.get('evaluation_record_id'),
            'review_record_id': row.get('review_record_id'),
            'evaluation_result': row.get('evaluation_result'),
            'contextual_review': row.get('contextual_review'),
            'review_status': row.get('review_status'),
            'attention_required': row.get('attention_required'),
        })

    def _flujo(self, file_id: str):
        from backend.flujo import flujo

        file_id = unquote(file_id)
        if Path(file_id).name != file_id or '/' in file_id or '\\' in file_id:
            self._json(422, {'error': 'invalid_file_id'})
            return
        body = flujo(get_store(), file_id,
                     en_disco=(FACTURAS_DIR / file_id).is_file())
        if body is None:
            self._json(404, {"error": "factura_no_encontrada"})
            return
        self._json(200, body)

    def _salud(self):
        body = {"ok": False, "postgres": False, "storage_bucket": None,
                "facturas_dir": str(FACTURAS_DIR), "facturas_en_disco": 0,
                "procesando": _procesando, "error": None}
        try:
            engine = get_store().engine
            try:
                engine.repository.preflight()
                body["postgres"] = True
            except Exception as exc:  # noqa: BLE001 - health probe must not raise
                body["error"] = type(exc).__name__
            try:
                engine.storage.preflight()
                body["storage_bucket"] = True
            except Exception as exc:  # noqa: BLE001 - health probe must not raise
                body["storage_bucket"] = False
                body["error"] = body["error"] or type(exc).__name__
            try:
                body["facturas_en_disco"] = sum(1 for _ in FACTURAS_DIR.glob("*.pdf"))
            except OSError as exc:
                body["error"] = body["error"] or type(exc).__name__
            body["ok"] = body["postgres"] and body["storage_bucket"] is not False
        except Exception as exc:  # noqa: BLE001 - health probe must not raise
            body["error"] = type(exc).__name__
        self._json(200, body)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m backend.server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args(argv)
    get_store()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"revision API en http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
