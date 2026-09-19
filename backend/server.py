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
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.results_store import ResultsStore  # noqa: E402
from backend.run_revision import DATA_DIR, FACTURAS_DIR, revisar_lote_sync  # noqa: E402

STORE = ResultsStore(DATA_DIR / "revisiones.db")
LOTE_TAMANO = 20

_estado_lock = threading.Lock()
_procesando = False
_ultimo_error = None


def _facturas() -> list[str]:
    return sorted(p.name for p in FACTURAS_DIR.glob("*.pdf"))


def _lanzar_en_fondo(file_ids: list[str]) -> None:
    global _procesando, _ultimo_error
    try:
        revisar_lote_sync(file_ids, store=STORE)
    except Exception as exc:  # keep the API alive; surface the error instead of crashing the thread
        _ultimo_error = f"{type(exc).__name__}: {exc}"
        for file_id in file_ids:
            row = STORE.get(file_id)
            if row is None or row["estado"] == "procesando":
                STORE.set_error(file_id, _ultimo_error)
    finally:
        with _estado_lock:
            _procesando = False


def _resumen() -> dict:
    todas = _facturas()
    estado = STORE.all()
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
        if url.path == "/api/resumen":
            self._json(200, _resumen())
        elif url.path.startswith("/api/factura/"):
            self._factura(url.path[len("/api/factura/"):])
        elif url.path == "/api/estado":
            self._json(200, {"procesando": _procesando, "error": _ultimo_error})
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/api/lanzar":
            ok = self._lanzar(parse_qs(self._body()))
            self._json(200, {"ok": ok, "procesando": _procesando})
        else:
            self._json(404, {"error": "not_found"})

    def _body(self) -> str:
        longitud = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(longitud).decode("utf-8", errors="replace") if longitud else ""

    def _lanzar(self, campos: dict[str, list[str]]) -> bool:
        global _procesando
        with _estado_lock:
            if _procesando:
                return False
            _procesando = True
        objetivo = (campos.get("objetivo") or [""])[0]
        if objetivo == "una" and campos.get("file_id"):
            file_ids = [campos["file_id"][0]]
        else:
            todas = _facturas()
            estado = STORE.all()
            pendientes = [f for f in todas if estado.get(f, {}).get("estado") not in ("hecha",)]
            file_ids = pendientes[:LOTE_TAMANO]
        if not file_ids:
            with _estado_lock:
                _procesando = False
            return False
        threading.Thread(target=_lanzar_en_fondo, args=(file_ids,), daemon=True).start()
        return True

    def _factura(self, file_id: str):
        if not (FACTURAS_DIR / file_id).exists():
            self._json(404, {"error": "factura_no_encontrada"})
            return
        row = STORE.get(file_id)
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
        })


def main(port: int = 8010) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"revision API en http://127.0.0.1:{port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
