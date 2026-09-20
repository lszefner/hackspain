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

from backend.results_store import PostgresResultsStore
from backend.run_revision import (
    FACTURAS_DIR,
    revisar_lote_sync,
)
from rules_ingestion.decision_context import ContextError

STORE = None
_store_lock = threading.Lock()
LOTE_TAMANO = 20

_estado_lock = threading.Lock()
_procesando = False
_ultimo_error = None


def get_store():
    global STORE
    with _store_lock:
        if STORE is None:
            from rules_ingestion.engine_storage import EngineRepository

            repository = EngineRepository()
            try:
                repository.preflight()
            except Exception:
                repository.close()
                raise
            STORE = PostgresResultsStore(repository=repository)
    return STORE


def _facturas(estado=None) -> list[str]:
    estado = get_store().all() if estado is None else estado
    return sorted({p.name for p in FACTURAS_DIR.glob("*.pdf")} | set(estado))


def _lanzar_en_fondo(file_ids: list[str], request_key: str) -> None:
    global _procesando, _ultimo_error
    try:
        revisar_lote_sync(file_ids, store=get_store(), request_key=request_key)
        _ultimo_error = None
    except Exception as exc:  # noqa: BLE001 - report background failures to the API
        _ultimo_error = f"{type(exc).__name__}: {exc}"
    finally:
        with _estado_lock:
            _procesando = False


def _resumen() -> dict:
    estado = get_store().all()
    todas = _facturas(estado)
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
        if url.path.startswith('/api/ui/'):
            self._ui(url.path[len('/api/ui/'):], parse_qs(url.query))
        elif url.path == "/api/salud":
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
                self._json(200, get_store().run_status(key))
            except KeyError:
                self._json(404, {'error': 'run_not_found'})
            except ContextError as exc:
                self._json(503, {'error': getattr(exc, 'code', 'postgres_audit_invalid')})
        elif url.path == "/api/estado":
            self._json(200, {"procesando": _procesando, "error": _ultimo_error})
        else:
            self._json(404, {"error": "not_found"})

    def _ui(self, resource, query):
        from time import perf_counter

        from backend.ui_queries import DeskQueries

        started = perf_counter()
        try:
            desk = DeskQueries(get_store())
            if resource in ('invoice', 'pdf'):
                file_id = (query.get('file') or [''])[0]
                if not file_id or Path(file_id).name != file_id or '\\' in file_id:
                    raise ValueError('invalid_file_id')
                if resource == 'pdf':
                    data = desk.pdf(file_id)
                    if data is None:
                        self._json(404, {'error': 'pdf_not_recorded'})
                        return
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/pdf')
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('Cache-Control', 'private, no-store')
                    self.end_headers()
                    self.wfile.write(data)
                    return
                result = desk.detail(file_id)
                if result is None:
                    self._json(404, {'error': 'invoice_not_recorded'})
                    return
            elif resource == 'invoices':
                result = desk.invoices(query)
            elif resource == 'suppliers':
                result = desk.suppliers(query)
            elif resource == 'summary':
                result = desk.summary()
            elif resource == 'rules':
                result = desk.rules()
            else:
                self._json(404, {'error': 'not_found'})
                return
            self._json(200, result | {'query_ms': round((perf_counter() - started)*1000, 2)})
        except ValueError:
            self._json(422, {'error': 'invalid_query'})
        except Exception:
            self._json(503, {'error': 'backend_query_unavailable'})

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
        elif objetivo == "varias" and campos.get("file_id"):
            # One run for the whole drop. A run's fixed cost -- workbook,
            # supplier/order snapshots, the ERP capture and the processed
            # history -- is paid once rather than once per PDF, and the files
            # extract concurrently instead of each waiting for _procesando.
            # revisar_lote validates every id; dict.fromkeys dedupes in order.
            file_ids = list(dict.fromkeys(campos["file_id"]))[:LOTE_TAMANO]
        else:
            estado = get_store().all()
            todas = _facturas(estado)
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
        try:
            row = get_store().get(file_id)
        except ContextError as exc:
            self._json(503, {'error': getattr(exc, 'code', 'postgres_audit_invalid')})
            return
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
            repository = get_store().repository
            try:
                repository.preflight()
                body["postgres"] = True
            except Exception as exc:  # noqa: BLE001 - health probe must not raise
                body["error"] = type(exc).__name__
            try:
                body["facturas_en_disco"] = sum(1 for _ in FACTURAS_DIR.glob("*.pdf"))
            except OSError as exc:
                body["error"] = body["error"] or type(exc).__name__
            body["ok"] = body["postgres"]
        except Exception as exc:  # noqa: BLE001 - health probe must not raise
            body["error"] = type(exc).__name__
        self._json(200, body)


def main(argv=None) -> int:
    import argparse

    from psycopg import OperationalError
    from psycopg_pool import PoolTimeout

    parser = argparse.ArgumentParser(prog="python -m backend.server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args(argv)
    print("Comprobando conexion a Postgres (hasta 30 s)...", flush=True)
    try:
        get_store()
    except (PoolTimeout, OperationalError):
        print(
            "No se pudo conectar a Postgres; la API no ha arrancado. "
            "Comprueba la conexion DATABASE_URL / SUPABASE_DB_URL "
            "(DATABASE_URL tiene prioridad), el DNS/VPN y el acceso al puerto "
            "de la base de datos. Prueba otra red y revisa el estado del "
            "proyecto Supabase. No se ha cambiado ningun dato.",
            file=sys.stderr,
            flush=True,
        )
        return 1
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"revision API en http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
