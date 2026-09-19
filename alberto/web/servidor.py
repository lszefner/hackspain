"""API JSON para la web de revision. Solo stdlib, sin framework.

Mismos cuatro endpoints que el backend de `main` -- el frontend de Next.js
no cambia ni una linea-- pero sirviendo lo que ya esta decidido en
`alberto.db` en vez de recalcularlo con otro motor.

`/api/lanzar` NO reprocesa desde la web. Decidir es un verbo de la CLI, con
su pasada registrada y su contexto; disparar un reproceso desde un boton
sin dejar constancia iria contra todo lo demas.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from alberto.db import RUTA_DB, conectar
from alberto.web import datos


def crear_handler(ruta_db: Path, *, lote: str, norma: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, status: int, payload: dict) -> None:
            cuerpo = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_OPTIONS(self):                      # noqa: N802
            self._json(204, {})

        def do_GET(self):                          # noqa: N802
            # Conexion por peticion: sqlite3 no comparte conexiones entre
            # hilos y esto es un ThreadingHTTPServer.
            url = urlparse(self.path)
            con = conectar(ruta_db)
            try:
                if url.path == "/api/resumen":
                    self._json(200, datos.resumen(con, lote=lote, norma=norma))
                elif url.path == "/api/estado":
                    self._json(200, {"procesando": False, "error": None})
                elif url.path.startswith("/api/factura/"):
                    fid = unquote(url.path[len("/api/factura/"):])
                    f = datos.factura(con, fid, norma=norma)
                    self._json(200 if f else 404,
                               f or {"error": "factura_no_encontrada"})
                else:
                    self._json(404, {"error": "not_found"})
            finally:
                con.close()

        def do_POST(self):                         # noqa: N802
            if urlparse(self.path).path == "/api/lanzar":
                # Deliberadamente un no-op: reprocesar es `alberto decide`,
                # que abre una pasada y registra su contexto.
                self._json(200, {"ok": False, "procesando": False,
                                 "motivo": "el reproceso se lanza con "
                                           "`alberto decide`, para que quede "
                                           "registrada la pasada"})
            else:
                self._json(404, {"error": "not_found"})

    return Handler


def servir(ruta_db: Path = RUTA_DB, *, puerto: int = 8010,
           lote: str = "lote1", norma: str = "v3") -> int:
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto),
                                   crear_handler(ruta_db, lote=lote, norma=norma))
    print(f"API de revision en http://127.0.0.1:{puerto}  (db={ruta_db})")
    print(f"  frontend:  cd frontend && NEXT_PUBLIC_API_BASE=http://127.0.0.1:{puerto} npm run dev")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
