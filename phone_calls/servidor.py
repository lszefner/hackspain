"""La centralita, por HTTP. Mismo patron que `alberto/web/servidor.py`.

Puerto propio (8011): el 8009 es el bridge ERP y el 8010 la API de la web.
Sirve tambien su propio HTML, lo que ahorra `npm`, ahorra CORS -- todo es el
mismo origen -- y, sobre todo, deja la pagina en `http://localhost`, que es
un contexto seguro: sin eso el navegador no da acceso al microfono.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from phone_calls import consulta, guion, voz

ESTATICO = Path(__file__).parent / "estatico"
GUIONES = Path(__file__).parent / "guiones_demo.json"


def url_audio(texto: str, voz_say: str | None,
              respaldo: str | None = None) -> str | None:
    """URL del audio de esa frase, o None para que hable el navegador.

    Sintetizar no puede tumbar una llamada, y tampoco deberia degradarla
    mas de lo imprescindible: si ElevenLabs no puede -- cuota agotada, sin
    red -- antes de dejarselo al navegador se prueba con `say`. Lo que
    Monica ya tenia cacheado sigue ahi, asi que ese respaldo suele ser
    instantaneo. Es la escalera de tres peldanos que promete el README,
    tambien cuando el primer peldano se rompe a mitad de demo.

    `rapido=True` en la primera: lo del guion ya esta en cache por `make
    voces` con el modelo bueno, asi que si aqui hay que sintetizar es porque
    alguien ha improvisado, y ahi lo que cuenta son los 0,4 s de flash.
    """
    if not voz_say:
        return None
    h = voz.sintetizar(texto, voz_say, rapido=True)
    if h is None and respaldo and respaldo != voz_say:
        h = voz.sintetizar(texto, respaldo)
    f = voz.ruta(h) if h else None
    return f"/api/voz/{f.name}" if f else None


def crear_handler(caja: dict, *, voz_say: str | None = None):
    # Se resuelve UNA vez: `mejor_say()` lanza un `say -v '?'` y no es cosa
    # de hacerlo en cada turno.
    respaldo = voz.mejor_say() if voz.motor(voz_say) == "11l" else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, status: int, payload) -> None:
            cuerpo = json.dumps(payload, ensure_ascii=False,
                                default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _fichero(self, ruta: Path, *, cache: str = "no-store",
                     tipo: str | None = None) -> None:
            if not ruta.is_file():
                return self._json(404, {"error": "no_encontrado"})
            cuerpo = ruta.read_bytes()
            tipo = tipo or mimetypes.guess_type(ruta.name)[0] \
                or "application/octet-stream"
            # `charset` SOLO en texto: ponerselo a un m4a hace que algunos
            # clientes lo traten como texto y lo destrocen.
            if tipo.startswith("text/") or tipo in (
                    "application/javascript", "application/json"):
                tipo = f"{tipo}; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            # Por defecto nada de cache: la demo se retoca hasta el ultimo
            # minuto y no queremos la version de hace media hora. El audio es
            # la excepcion -- su URL lleva el hash del contenido, asi que
            # cambiar la frase cambia la URL y cachear es gratis.
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):                                       # noqa: N802
            ruta = urlparse(self.path).path
            if ruta in ("/", "/index.html"):
                return self._fichero(ESTATICO / "index.html")
            if ruta == "/api/guiones":
                return self._fichero(GUIONES)
            if ruta.startswith("/api/voz/"):
                f = voz.ruta(Path(ruta).stem)
                if f is None:
                    return self._json(404, {"error": "no_encontrado"})
                # El tipo lo da `voz`, no `mimetypes`: este adivina
                # 'audio/mp4a-latm' para un .m4a, que es otro formato y los
                # navegadores lo rechazan.
                return self._fichero(f, tipo=voz.tipo(f),
                                     cache="max-age=31536000, immutable")
            # Sin subir de directorio: `Path.name` tira cualquier '..'.
            nombre = Path(ruta).name
            if ruta.startswith("/estatico/") and nombre:
                return self._fichero(ESTATICO / nombre)
            return self._json(404, {"error": "no_encontrado"})

        def do_POST(self):                                      # noqa: N802
            if urlparse(self.path).path != "/api/turno":
                return self._json(404, {"error": "no_encontrado"})
            def _audio(texto: str) -> str | None:
                return url_audio(texto, voz_say, respaldo)

            largo = int(self.headers.get("Content-Length") or 0)
            try:
                peticion = json.loads(self.rfile.read(largo) or b"{}")
            except json.JSONDecodeError as exc:
                return self._json(400, {"error": f"json invalido: {exc}"})

            # El export se carga UNA vez al arrancar y se comparte: son tres
            # ficheros de solo lectura, no hay nada que sincronizar entre
            # hilos y un fichero corrupto se descubre al levantar, no en
            # mitad de un turno.
            try:
                turno = guion.avanzar(peticion.get("estado") or {},
                                      peticion.get("oye") or [], caja)
                respuesta = {"estado": turno["estado"],
                             "expediente": turno["expediente"],
                             **turno["respuesta"],
                             "audio": _audio(turno["respuesta"]["decir"])}
                # En el saludo se manda tambien la muletilla, para que el
                # navegador la tenga descargada cuando le toque taparle la
                # latencia a la consulta.
                if not (peticion.get("estado") or {}).get("paso"):
                    respuesta["agente"] = guion.AGENTE
                    respuesta["muletilla"] = {"texto": guion.MULETILLA,
                                              "audio": _audio(guion.MULETILLA)}
                self._json(200, respuesta)
            except Exception as exc:                            # noqa: BLE001
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def resolver_voz(pedida: str | None) -> str | None:
    """'Marina' -> '11l:M7m4...', 'Mónica' -> 'say:Mónica'. Con prefijo, tal cual."""
    if not pedida:
        return None
    if ":" in pedida:
        return pedida
    if pedida in voz.VOCES_11L:
        return f"11l:{voz.VOCES_11L[pedida]}"
    return f"say:{pedida}"


def servir(datos: Path = consulta.DATOS, *, puerto: int = 8011,
           voz_say: str | None = None, sin_audio: bool = False) -> int:
    try:
        caja = consulta.cargar(datos)
    except consulta.SinDatos as exc:
        print(f"{exc}\n"
              f"         make export     (extrae, evalua y congela en {datos})")
        return 1
    elegida = None if sin_audio else (resolver_voz(voz_say) or voz.mejor_voz())
    srv = ThreadingHTTPServer(("127.0.0.1", puerto),
                              crear_handler(caja, voz_say=elegida))
    pedidos = consulta.pedidos(caja)
    print(f"Centralita en http://localhost:{puerto}")
    print(f"  {len(pedidos)} pedidos: {', '.join(pedidos)}")
    if elegida:
        cacheadas = sum(1 for f in voz.CACHE.iterdir()
                        if f.suffix.lstrip(".") in voz.TIPO) if voz.CACHE.is_dir() else 0
        print(f"  voz: {voz.nombre_voz(elegida)}   {cacheadas} frases en cache"
              + ("" if cacheadas else "   -- corre `make voces` para que no "
                                      "haya esperas en mitad de la llamada"))
    else:
        print("  voz: la del navegador")
    print("  Safari o Chrome, y desde ESTA maquina: en una IP de red no hay "
          "microfono.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser("phone_calls",
                                description="Centralita de proveedores")
    p.add_argument("--puerto", type=int, default=8011)
    p.add_argument("--datos", type=Path, default=consulta.DATOS)
    p.add_argument("--voz", default=None,
                   help="'Marina', 'Mónica', '11l:<id>' o 'say:<nombre>'; "
                        "por defecto ElevenLabs si hay clave, si no `say`")
    p.add_argument("--sin-audio", action="store_true",
                   help="no sintetizar: que hable el navegador")
    a = p.parse_args(argv)
    return servir(a.datos, puerto=a.puerto, voz_say=a.voz, sin_audio=a.sin_audio)


if __name__ == "__main__":
    raise SystemExit(main())
