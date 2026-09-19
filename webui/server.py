"""Simple local review UI for the invoice pipeline on `main`.

Lists the 500 facturas/*.pdf, lets you launch the real revision (Helmcode OCR
+ DeepSeek interpretation + rules_ingestion business checks) for a batch or a
single file, and shows PASS/FAIL/NEEDS_REVIEW per rule with the reason.
Stdlib only, no extra deps -- same spirit as alberto_erp.py.
"""
from __future__ import annotations

import html
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webui.results_store import ResultsStore  # noqa: E402
from webui.run_revision import DATA_DIR, FACTURAS_DIR, revisar_lote_sync  # noqa: E402

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
        _ultimo_error = type(exc).__name__
        for file_id in file_ids:
            STORE.set_error(file_id, _ultimo_error)
    finally:
        with _estado_lock:
            _procesando = False


ESTILO = """<style>
body{font-family:system-ui,Arial,sans-serif;margin:24px;background:#f4f4f2;color:#222}
h1{font-size:20px}
.resumen{display:flex;gap:16px;margin:12px 0 20px}
.tarjeta{background:#fff;border:1px solid #ddd;border-radius:6px;padding:10px 16px;min-width:110px}
.tarjeta b{display:block;font-size:20px}
table{border-collapse:collapse;width:100%;background:#fff}
td,th{border:1px solid #ddd;padding:6px 10px;text-align:left;font-size:13px}
th{background:#eee;position:sticky;top:0}
.pendiente{color:#888}.procesando{color:#b8860b}.hecha{color:#1e7e1e}.error{color:#b00020}
.PAGAR{color:#1e7e1e;font-weight:bold}.ESCALAR{color:#b8860b;font-weight:bold}.NO_PAGAR{color:#b00020;font-weight:bold}
.PASS{color:#1e7e1e}.FAIL{color:#b00020;font-weight:bold}.NEEDS_REVIEW{color:#b8860b;font-weight:bold}
form{display:inline}
button{cursor:pointer;padding:6px 12px;border-radius:4px;border:1px solid #999;background:#fafafa}
.aviso{background:#fff3cd;border:1px solid #e0c36a;padding:8px 12px;border-radius:6px;margin:10px 0}
a{color:#0645ad;text-decoration:none}
.checks{margin-top:12px}
.check{border:1px solid #ddd;border-radius:6px;padding:8px 12px;margin-bottom:6px;background:#fff}
</style>"""


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
        filas = []
        for file_id in todas:
            row = estado.get(file_id)
            st = row["estado"] if row else "pendiente"
            conteo[st] = conteo.get(st, 0) + 1
            decision = row.get("decision") if row else None
            if decision in decisiones:
                decisiones[decision] += 1
            deco = f'<span class="{decision}">{decision}</span>' if decision else "&mdash;"
            filas.append(
                f'<tr><td><a href="/factura/{html.escape(file_id)}">{html.escape(file_id)}</a></td>'
                f'<td class="{st}">{st}</td><td>{deco}</td>'
                f'<td><form method="post" action="/lanzar">'
                f'<input type="hidden" name="objetivo" value="una">'
                f'<input type="hidden" name="file_id" value="{html.escape(file_id)}">'
                f'<button {"disabled" if _procesando else ""}>Revisar</button></form></td></tr>'
            )
        aviso = ""
        if _procesando:
            aviso = ('<div class="aviso">Procesando un lote&hellip; esta pagina no se actualiza sola, '
                    'recargala en unos segundos.</div>')
        elif _ultimo_error:
            aviso = f'<div class="aviso">Ultimo error: {html.escape(_ultimo_error)}</div>'
        body = f"""<html><head><title>Revision de facturas</title><link rel="icon" href="data:,">{ESTILO}</head>
<body>
<h1>Revision de facturas &middot; {len(todas)} en facturas/</h1>
{aviso}
<div class="resumen">
  <div class="tarjeta">Pendientes<b>{conteo.get('pendiente', 0)}</b></div>
  <div class="tarjeta">Hechas<b>{conteo.get('hecha', 0)}</b></div>
  <div class="tarjeta">Con error<b>{conteo.get('error', 0)}</b></div>
  <div class="tarjeta" style="border-color:#1e7e1e">PAGAR<b class="PAGAR">{decisiones['PAGAR']}</b></div>
  <div class="tarjeta" style="border-color:#b8860b">ESCALAR<b class="ESCALAR">{decisiones['ESCALAR']}</b></div>
  <div class="tarjeta" style="border-color:#b00020">NO_PAGAR<b class="NO_PAGAR">{decisiones['NO_PAGAR']}</b></div>
</div>
<form method="post" action="/lanzar">
  <input type="hidden" name="objetivo" value="lote">
  <button {"disabled" if _procesando else ""}>Lanzar revision (siguiente lote de {LOTE_TAMANO} pendientes)</button>
</form>
<p style="font-size:12px;color:#666">Cada revision llama de verdad a Helmcode (OCR + DeepSeek) y aplica las 6
reglas de rules_ingestion/checks.py contra el maestro de proveedores/pedidos y el ERP local.</p>
<table>
<tr><th>Factura</th><th>Estado</th><th>Decision</th><th></th></tr>
{''.join(filas)}
</table>
</body></html>"""
        self._responder(200, body)

    def _pagina_factura(self, file_id: str):
        row = STORE.get(file_id)
        pdf_ok = (FACTURAS_DIR / file_id).exists()
        if not pdf_ok:
            self._responder(404, "<h1>Factura no encontrada</h1>")
            return
        if row is None:
            body = f"""<html><head><title>{html.escape(file_id)}</title>{ESTILO}</head><body>
<p><a href="/">&larr; volver</a></p><h1>{html.escape(file_id)}</h1>
<p>Todavia no se ha revisado.</p>
<form method="post" action="/lanzar">
  <input type="hidden" name="objetivo" value="una">
  <input type="hidden" name="file_id" value="{html.escape(file_id)}">
  <button {"disabled" if _procesando else ""}>Revisar ahora</button>
</form></body></html>"""
            self._responder(200, body)
            return
        checks = json.loads(row["checks"]) if row["checks"] else []
        inv = json.loads(row["raw_invoice"]) if row["raw_invoice"] else {}
        check_html = "".join(
            f'<div class="check"><b class="{html.escape(str(c.get("verdict", "NEEDS_REVIEW")), quote=True)}">'
            f'{html.escape(str(c.get("verdict", "NEEDS_REVIEW")))}</b> &middot; '
            f'{html.escape(str(c.get("canonical") or "UNKNOWN"))}<br>'
            f'{html.escape(str(c.get("reason", "")))}</div>'
            for c in checks
        )
        estado_actual = row["estado"]
        context_html = ""
        if row.get("decision_context"):
            titulo = "Contexto de decision (recomendacion, no pago)" \
                if estado_actual == "hecha" else \
                "Ultima evidencia guardada; no recomendacion vigente"
            context_html = (
                f"<h3>{titulo}</h3>"
                f'<details><summary>decision_context</summary><pre>'
                f'{html.escape(json.dumps(json.loads(row["decision_context"]), ensure_ascii=False, indent=2))}'
                f"</pre></details>")
        receipt_html = ""
        if row.get("context_receipt"):
            titulo = "Recibo de persistencia" if estado_actual == "hecha" \
                else "Ultima evidencia guardada; no recomendacion vigente"
            receipt_html = (
                f"<h3>{titulo}</h3>"
                f'<details><summary>context_receipt</summary><pre>'
                f'{html.escape(json.dumps(json.loads(row["context_receipt"]), ensure_ascii=False, indent=2))}'
                f"</pre></details>")
        campos_html = "".join(
            f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>"
            for k, v in inv.items() if k not in ("file_id",)
        )
        decision = row.get("decision") if estado_actual == "hecha" else None
        deco = f'<span class="{decision}">{decision}</span>' if decision else "&mdash;"
        error_html = f'<div class="aviso">{html.escape(row["error"])}</div>' if row.get("error") else ""
        body = f"""<html><head><title>{html.escape(file_id)}</title>{ESTILO}</head><body>
<p><a href="/">&larr; volver</a></p>
<h1>{html.escape(file_id)} &middot; {deco}</h1>
{error_html}
<form method="post" action="/lanzar">
  <input type="hidden" name="objetivo" value="una">
  <input type="hidden" name="file_id" value="{html.escape(file_id)}">
  <button {"disabled" if _procesando else ""}>Volver a revisar</button>
</form>
<h3>Checks (rules_ingestion/checks.py)</h3>
<div class="checks">{check_html or "<p>sin checks</p>"}</div>
<h3>Campos extraidos (mapeados a rules_ingestion.invoice)</h3>
<table>{campos_html}</table>
{context_html}
{receipt_html}
</body></html>"""
        self._responder(200, body)


def main(port: int = 8010) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"revision web en http://127.0.0.1:{port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
