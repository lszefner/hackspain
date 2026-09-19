"""La centralita en Vercel: un turno de conversacion por peticion.

Es el `do_POST` de `phone_calls/servidor.py` con dos cambios y ni uno mas:

  - el audio no se sintetiza, se busca en un indice congelado. `voz.py` no
    esta desplegado, asi que aqui no hay ningun camino de codigo que pueda
    llamar a ElevenLabs ni gastar cuota.
  - no hay `ThreadingHTTPServer`: Vercel levanta el proceso, llama al handler
    y lo apaga. El resto es identico porque el runtime de Python de Vercel
    pide exactamente lo que `servidor.py` ya era, un BaseHTTPRequestHandler.

La conversacion la lleva `guion.py`, importado VERBATIM de `centralita_py/`,
que es una copia generada por `make web-centralita`. La politica de lo que se
dice y lo que se calla es el mismo fichero que corre en el 8011.

El servidor no guarda sesiones: el estado va y vuelve en el cuerpo, asi que
que cada peticion caiga en una instancia distinta da exactamente igual.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# api/centralita/turno.py -> api/ -> la raiz del proyecto en Vercel, que es
# `frontend/`. El arbol vendorizado cuelga de ahi, fuera de `api/`: dentro,
# Vercel podria intentar convertir cada .py en una Function suelta.
BUNDLE = Path(__file__).resolve().parents[2] / "centralita_py"
sys.path.insert(0, str(BUNDLE))

from phone_calls import consulta, guion  # noqa: E402

# A nivel de modulo, igual que en `servidor.py` y por el mismo motivo: son
# ficheros de solo lectura, y uno corrupto tiene que descubrirse al levantar
# el proceso, no a mitad de un turno. De regalo, las invocaciones calientes
# se lo encuentran ya cargado.
CAJA = consulta.cargar()
INDICE = json.loads((BUNDLE / "voz_indice.json").read_text("utf-8"))

# Las URLs del audio llevan el hash del contenido, asi que cambiar una frase
# cambia la URL: el CDN puede cachearlas para siempre (vercel.json).
VOZ = "/phone_calls/voz/"

# El sitio entero vive tras un codigo de acceso (`frontend/src/middleware.ts`).
# El middleware de Next deberia cubrir tambien esta ruta, pero no esta
# documentado que lo haga para una Function de Python del mismo despliegue, y
# una URL publica que recita expedientes no es sitio para averiguarlo. Son
# cuatro lineas y cierran la duda.
GATE = "site-access=granted"


def audio(texto: str) -> str | None:
    """La URL de esa frase, o None para que hable el navegador.

    Es el peldano 1 de la escalera del README. El 2 (`say` de macOS) no
    existe aqui -- no hay macOS en una Lambda -- asi que lo improvisado cae
    directo al 3, que `llamada.js` ya sabe manejar.
    """
    fichero = INDICE.get(texto)
    return f"{VOZ}{fichero}" if fichero else None


class handler(BaseHTTPRequestHandler):            # noqa: N801  (lo exige Vercel)
    def log_message(self, fmt, *args):
        pass

    def _json(self, status: int, payload) -> None:
        # `default=str`: la evidencia de las reglas trae Decimal y date, y
        # json.dumps se planta con los dos. `ensure_ascii=False` para que las
        # tildes viajen como tildes -- el sintetizador las necesita.
        cuerpo = json.dumps(payload, ensure_ascii=False,
                            default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _pasa_el_candado(self) -> bool:
        return GATE in (self.headers.get("Cookie") or "")

    def do_GET(self):                                           # noqa: N802
        """Diagnostico: sirve para verificar un despliegue con `curl` antes
        de abrir la pagina, y para saber cuanta voz llego."""
        if not self._pasa_el_candado():
            return self._json(401, {"error": "sin_acceso"})
        self._json(200, {"ok": True,
                         "pedidos": consulta.pedidos(CAJA),
                         "agente": guion.AGENTE,
                         "frases_con_audio": len(INDICE)})

    def do_POST(self):                                          # noqa: N802
        if not self._pasa_el_candado():
            return self._json(401, {"error": "sin_acceso"})

        largo = int(self.headers.get("Content-Length") or 0)
        try:
            peticion = json.loads(self.rfile.read(largo) or b"{}")
        except json.JSONDecodeError as exc:
            return self._json(400, {"error": f"json invalido: {exc}"})

        try:
            estado = peticion.get("estado") or {}
            turno = guion.avanzar(estado, peticion.get("oye") or [], CAJA)
            respuesta = {"estado": turno["estado"],
                         "expediente": turno["expediente"],
                         **turno["respuesta"],
                         "audio": audio(turno["respuesta"]["decir"])}
            # En el saludo se manda tambien la muletilla, para que el
            # navegador la tenga descargada cuando le toque taparle la
            # latencia a la consulta.
            if not estado.get("paso"):
                respuesta["agente"] = guion.AGENTE
                respuesta["muletilla"] = {"texto": guion.MULETILLA,
                                          "audio": audio(guion.MULETILLA)}
            self._json(200, respuesta)
        except Exception as exc:                                # noqa: BLE001
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
