"""API JSON para la web. Solo stdlib, sin framework.

Sirve la interfaz `DataSource` que declara `frontend/src/lib/data.ts`, leyendo
lo ya decidido en `alberto.db`. Los GET no deciden nada: si la pantalla y el
JSONL dijeran cosas distintas seria el mismo fallo que ya tuvimos entre
`emite` y `explica`, pero delante del jurado.

Los POST (subir una factura, reprocesarla) SI escriben, y por eso pasan
enteros por `alberto.web.subida`, que llama a las mismas funciones que el
CLI y abre su `pasada`. La regla no es "la web no escribe", es "la web no
tiene una segunda forma de decidir".
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from alberto.db import RUTA_DB, conectar
from alberto.web import datos, subida


def _filtros(q: dict[str, list[str]], lote: str) -> dict:
    f = {k: v[0] for k, v in q.items() if v and v[0]}
    f.setdefault("lote", lote)
    return f


def crear_handler(ruta_db: Path, *, lote: str, caja: Path = Path("."),
                  norma: str = "v3"):
    # `_despachar` tiene su propia `norma` (la de la query). Esta es la
    # familia con la que se decide al subir, y no es lo mismo.
    familia = norma

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
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, X-File-Name")
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
            elif ruta.startswith("/api/documento/"):
                sha = unquote(ruta[len("/api/documento/"):])
                e = subida.estado_documento(con, sha, norma=norma or familia)
                self._json(200 if e else 404, e or {"error": "no_encontrado"})
            elif ruta.startswith("/api/expediente/"):
                fid = unquote(ruta[len("/api/expediente/"):])
                e = datos.expediente(con, fid, norma)
                self._json(200 if e else 404, e or {"error": "no_encontrado"})
            else:
                self._json(404, {"error": "not_found"})

        def do_POST(self):                                      # noqa: N802
            url = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                self._json(411, {"ok": False, "errores": ["falta el cuerpo"]})
                return
            if n > subida.MAX_BYTES:
                self._json(413, {"ok": False, "errores": [
                    f"mas de {subida.MAX_BYTES // 2**20} MB"]})
                return
            cuerpo = self.rfile.read(n)
            con = conectar(ruta_db)
            try:
                self._despachar_post(url.path, cuerpo, con)
            except subida.SubidaInvalida as exc:
                self._json(422, {"ok": False, "errores": [str(exc)]})
            except Exception as exc:                            # noqa: BLE001
                self._json(500, {"ok": False, "errores":
                                 [f"{type(exc).__name__}: {exc}"]})
            finally:
                con.close()

        def _despachar_post(self, ruta, cuerpo, con):
            if ruta == "/api/subir":
                nombre = unquote(self.headers.get("X-File-Name", ""))
                r = subida.subir(con, nombre=nombre, contenido=cuerpo,
                                 carpeta=caja / "facturas", lote=lote,
                                 norma=familia)
                # 409: el fichero ya esta en la plataforma. No es un error del
                # usuario, es la respuesta que venia a buscar.
                self._json(200 if r["ok"] else 409, r)
            elif ruta == "/api/reprocesar":
                doc_id = (json.loads(cuerpo or b"{}") or {}).get("doc_id") or ""
                r = subida.reprocesar(con, doc_id, norma=familia)
                self._json(200 if r["ok"]
                           else (404 if r.get("error") == "no_encontrado" else 409), r)
            else:
                self._json(404, {"error": "not_found"})

    return Handler


def servir(ruta_db: Path = RUTA_DB, *, puerto: int = 8010,
           lote: str = "lote1", caja: Path = Path("."),
           norma: str = "v3") -> int:
    servidor = ThreadingHTTPServer(
        ("127.0.0.1", puerto),
        crear_handler(ruta_db, lote=lote, caja=caja, norma=norma))
    print(f"API de Albertito en http://127.0.0.1:{puerto}   (db={ruta_db})")
    facturas = caja / "facturas"
    print(f"  subidas -> {facturas}"
          + ("" if facturas.is_dir() else "   AVISO: no existe, no se podra subir"))
    print(f"  frontend:  cd frontend && "
          f"NEXT_PUBLIC_API_BASE=http://127.0.0.1:{puerto} npm run dev")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
