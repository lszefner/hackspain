"""Simple local review UI for the invoice pipeline on `main`.

Lists the 500 facturas/*.pdf, lets you launch the real revision (Helmcode OCR
+ DeepSeek interpretation + rules_ingestion business checks) for a batch or a
single file, and shows PASS/FAIL/NEEDS_REVIEW per rule with the reason.
Stdlib only, no extra deps -- same spirit as alberto_erp.py. All markup lives
in webui/frontend/ (templates + static/style.css); this module only computes
values and renders them.
"""
from __future__ import annotations

import html
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from webui.backend.results_store import ResultsStore  # noqa: E402
from webui.backend.run_revision import DATA_DIR, FACTURAS_DIR, revisar_lote_sync  # noqa: E402
from webui.backend.templates import render  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend" / "static"

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
    except Exception as exc:  # keep the UI alive; show the error instead of crashing the thread
        _ultimo_error = f"{type(exc).__name__}: {exc}"
        for file_id in file_ids:
            row = STORE.get(file_id)
            if row is None or row["estado"] == "procesando":
                STORE.set_error(file_id, _ultimo_error)
    finally:
        with _estado_lock:
            _procesando = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _responder(self, status: int, body: str, content_type="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _archivo(self, path: Path, content_type: str):
        if not path.is_file():
            self._responder(404, "<h1>404</h1>")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirigir(self, path: str):
        self.send_response(303)
        self.send_header("Location", path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            self._pagina_home()
        elif url.path.startswith("/factura/"):
            self._pagina_factura(url.path[len("/factura/"):])
        elif url.path == "/static/style.css":
            self._archivo(STATIC_DIR / "style.css", "text/css; charset=utf-8")
        elif url.path == "/estado.json":
            self._responder(200, json.dumps({"procesando": _procesando, "error": _ultimo_error}),
                           "application/json")
        else:
            self._responder(404, "<h1>404</h1>")

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/lanzar":
            self._lanzar(parse_qs(self._body()))
            self._redirigir("/")
        else:
            self._responder(404, "<h1>404</h1>")

    def _body(self) -> str:
        longitud = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(longitud).decode("utf-8", errors="replace") if longitud else ""

    def _lanzar(self, campos: dict[str, list[str]]):
        global _procesando
        with _estado_lock:
            if _procesando:
                return
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
            return
        threading.Thread(target=_lanzar_en_fondo, args=(file_ids,), daemon=True).start()

    def _pagina_home(self):
        todas = _facturas()
        estado = STORE.all()
        conteo = {"pendiente": 0, "procesando": 0, "hecha": 0, "error": 0}
        decisiones = {"PAGAR": 0, "ESCALAR": 0, "NO_PAGAR": 0}
        disabled = "disabled" if _procesando else ""
        filas = []
        for file_id in todas:
            row = estado.get(file_id)
            st = row["estado"] if row else "pendiente"
            conteo[st] = conteo.get(st, 0) + 1
            decision = row.get("decision") if row else None
            if decision in decisiones:
                decisiones[decision] += 1
            deco = f'<span class="{decision}">{decision}</span>' if decision else "&mdash;"
            filas.append(render(
                "fila.html",
                file_id_esc=html.escape(file_id),
                estado=st,
                decision_html=deco,
                disabled=disabled,
            ))
        aviso = ""
        if _procesando:
            aviso = render("aviso_procesando.html")
        elif _ultimo_error:
            aviso = render("aviso_error.html", mensaje_esc=f"Ultimo error: {html.escape(_ultimo_error)}")
        body = render(
            "home.html",
            total=str(len(todas)),
            aviso=aviso,
            n_pendiente=str(conteo.get("pendiente", 0)),
            n_hecha=str(conteo.get("hecha", 0)),
            n_error=str(conteo.get("error", 0)),
            n_pagar=str(decisiones["PAGAR"]),
            n_escalar=str(decisiones["ESCALAR"]),
            n_no_pagar=str(decisiones["NO_PAGAR"]),
            disabled_lote=disabled,
            lote_tamano=str(LOTE_TAMANO),
            filas="".join(filas),
        )
        self._responder(200, body)

    def _pagina_factura(self, file_id: str):
        row = STORE.get(file_id)
        if not (FACTURAS_DIR / file_id).exists():
            self._responder(404, "<h1>Factura no encontrada</h1>")
            return
        disabled = "disabled" if _procesando else ""
        file_id_esc = html.escape(file_id)
        if row is None:
            body = render("factura_pendiente.html", file_id_esc=file_id_esc, disabled=disabled)
            self._responder(200, body)
            return
        checks = json.loads(row["checks"]) if row["checks"] else []
        inv = json.loads(row["raw_invoice"]) if row["raw_invoice"] else {}
        check_html = "".join(render(
            "check.html",
            verdict=c["verdict"],
            canonical_esc=html.escape(c["canonical"]),
            reason_esc=html.escape(c["reason"]),
        ) for c in checks) or "<p>sin checks</p>"
        campos_html = "".join(render(
            "campo_row.html",
            clave_esc=html.escape(str(k)),
            valor_esc=html.escape(str(v)),
        ) for k, v in inv.items() if k != "file_id")
        decision = row.get("decision")
        deco = f'<span class="{decision}">{decision}</span>' if decision else "&mdash;"
        error_html = render("aviso_error.html", mensaje_esc=html.escape(row["error"])) if row.get("error") else ""
        body = render(
            "factura_hecha.html",
            file_id_esc=file_id_esc,
            decision_html=deco,
            error_html=error_html,
            disabled=disabled,
            check_html=check_html,
            campos_html=campos_html,
        )
        self._responder(200, body)


def main(port: int = 8010) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"revision web en http://127.0.0.1:{port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
