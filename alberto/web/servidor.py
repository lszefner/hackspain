"""API JSON para la web. Solo stdlib, sin framework.

Sirve la interfaz `DataSource` que declara `frontend/src/lib/data.ts`, leyendo
lo ya decidido en `alberto.db`. Aqui no se decide nada: si la pantalla y el
JSONL dijeran cosas distintas seria el mismo fallo que ya tuvimos entre
`emite` y `explica`, pero delante del jurado.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from alberto.db import RUTA_DB, conectar
from alberto.web import datos


def _filtros(q: dict[str, list[str]], lote: str) -> dict:
    f = {k: v[0] for k, v in q.items() if v and v[0]}
    f.setdefault("lote", lote)
    return f


def crear_handler(ruta_db: Path, *, lote: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, status: int, payload) -> None:
            cuerpo = json.dumps(payload, ensure_ascii=False,
                                default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_OPTIONS(self):                                   # noqa: N802
            self._json(204, {})

        def do_GET(self):                                       # noqa: N802
            url = urlparse(self.path)
            q = parse_qs(url.query)
            f = _filtros(q, lote)
            ruta = url.path
            # sqlite3 no comparte conexiones entre hilos y esto es un
            # ThreadingHTTPServer: una conexion por peticion.
            con = conectar(ruta_db)
            try:
                self._despachar(ruta, q, f, con)
            except Exception as exc:                            # noqa: BLE001
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            finally:
                con.close()

        def _despachar(self, ruta, q, f, con):
            norma = f.get("norma")
            if ruta == "/api/normas":
                self._json(200, {"normas": datos.normas(con),
                                 "activa": datos.norma_activa(con)})
            elif ruta == "/api/kpis":
                self._json(200, datos.kpis(con, norma, lote=lote))
            elif ruta == "/api/baldosas":
                self._json(200, datos.baldosas(con, f))
            elif ruta == "/api/facturas":
                self._json(200, datos.facturas(con, f))
            elif ruta == "/api/eventos":
                self._json(200, datos.eventos(con, f))
            elif ruta == "/api/motivos":
                self._json(200, datos.motivos(con, norma, lote=lote))
            elif ruta == "/api/bandeja":
                self._json(200, datos.bandeja(con, norma, lote=lote))
            elif ruta == "/api/coste":
                self._json(200, datos.coste(con, lote=lote))
            elif ruta == "/api/partes":
                self._json(200, datos.partes(con))
            elif ruta == "/api/salud":
                self._json(200, datos.salud(con, lote=lote))
            elif ruta == "/api/diff":
                self._json(200, datos.diff_normas(con, f.get("de", ""),
                                                  f.get("a", "")))
            elif ruta.startswith("/api/expediente/"):
                fid = unquote(ruta[len("/api/expediente/"):])
                e = datos.expediente(con, fid, norma)
                self._json(200 if e else 404, e or {"error": "no_encontrado"})
            else:
                self._json(404, {"error": "not_found"})

        def do_POST(self):                                      # noqa: N802
            # Reprocesar es `alberto decide`, que abre una pasada y registra
            # su contexto. Un boton que dispara un reproceso sin dejar
            # constancia iria contra toda la trazabilidad.
            self._json(200, {"ok": False, "motivo":
                             "el reproceso se lanza con `alberto decide`"})

    return Handler


def servir(ruta_db: Path = RUTA_DB, *, puerto: int = 8010,
           lote: str = "lote1") -> int:
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto),
                                   crear_handler(ruta_db, lote=lote))
    print(f"API de Albertito en http://127.0.0.1:{puerto}   (db={ruta_db})")
    print(f"  frontend:  cd frontend && "
          f"NEXT_PUBLIC_API_BASE=http://127.0.0.1:{puerto} npm run dev")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
