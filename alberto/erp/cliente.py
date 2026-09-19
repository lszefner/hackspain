"""Cliente del bridge AS/400 de 2009.

El manual documenta tres averias y las tres son de verdad:
  · ORA-00600 (HTTP 500) en cada 10a consulta autenticada -> reintentar ESA pagina
  · SES-401 a los 900 s o 300 usos                        -> volver a /erp/login
  · ERP-429 por encima de 10 req/s                        -> esperar Retry-After

El fallo del ejemplo que nos pasaron era abandonar la descarga entera cuando una
pagina fallaba. El manual avisa: "un cliente que no reintenta no llega a la
pagina 26". Aqui cada pagina se reintenta por separado y al final se comprueba
el total declarado por <meta>.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from alberto.contratos import Asiento
from .parseo import asientos_de_xml, meta_de_xml, token_de_xml

URL_POR_DEFECTO = "http://127.0.0.1:8009"
USUARIO, CLAVE = "alberto", "FACTURAS2009"


class ErrorERP(RuntimeError):
    pass


@dataclass
class Metricas:
    peticiones: int = 0
    reintentos_ora: int = 0
    relogins: int = 0
    esperas_429: int = 0
    segundos: float = 0.0


class ClienteERP:
    def __init__(self, url: str = URL_POR_DEFECTO, *, max_reintentos: int = 6,
                 timeout: float = 10.0) -> None:
        self.url = url.rstrip("/")
        self.max_reintentos = max_reintentos
        self._http = httpx.Client(timeout=timeout)
        self._token: str | None = None
        self.metricas = Metricas()

    # ------------------------------------------------------------------ auth
    def login(self) -> str:
        r = self._http.post(f"{self.url}/erp/login",
                            data={"usuario": USUARIO, "clave": CLAVE})
        r.raise_for_status()
        self._token = token_de_xml(r.content)
        self.metricas.relogins += 1
        return self._token

    def _asegura_token(self) -> str:
        return self._token or self.login()

    # ------------------------------------------------------------------- get
    def _get(self, ruta: str, params: dict | None = None) -> bytes:
        """Una consulta autenticada, con las tres averias del manual tratadas."""
        inicio = time.monotonic()
        try:
            for intento in range(self.max_reintentos):
                token = self._asegura_token()
                r = self._http.get(f"{self.url}{ruta}", params=params,
                                   headers={"X-ERP-Token": token})
                self.metricas.peticiones += 1
                cuerpo = r.content

                if r.status_code == 200:
                    return cuerpo

                if r.status_code == 500 and b"ORA-00600" in cuerpo:
                    self.metricas.reintentos_ora += 1
                    time.sleep(min(0.1 * 2 ** intento, 1.0))
                    continue

                if r.status_code == 429:
                    espera = float(r.headers.get("Retry-After", "1") or 1)
                    self.metricas.esperas_429 += 1
                    time.sleep(espera)
                    continue

                if r.status_code in (401, 403) or b"SES-401" in cuerpo:
                    self._token = None          # caducado: se renueva y se repite
                    continue

                raise ErrorERP(f"{ruta} devolvio {r.status_code}: {cuerpo[:160]!r}")

            raise ErrorERP(f"{ruta} agoto {self.max_reintentos} reintentos")
        finally:
            self.metricas.segundos += time.monotonic() - inicio

    # --------------------------------------------------------------- publico
    def estado(self) -> bytes:
        return self._http.get(f"{self.url}/erp/estado").content

    def pagina(self, n: int) -> tuple[list[Asiento], dict[str, int]]:
        cuerpo = self._get("/erp/asientos", {"pagina": n})
        return asientos_de_xml(cuerpo), meta_de_xml(cuerpo)

    def descargar_todo(self) -> tuple[list[Asiento], dict]:
        """Descarga completa. Devuelve los asientos y un informe de integridad.

        No para al primer hueco: recorre TODAS las paginas que declara <meta>.
        """
        primera, meta = self.pagina(1)
        paginas = meta.get("paginas", 1)
        total = meta.get("total", 0)

        por_id: dict[str, Asiento] = {a.asiento_id: a for a in primera}
        fallidas: list[int] = []
        for n in range(2, paginas + 1):
            try:
                filas, _ = self.pagina(n)
            except ErrorERP:
                fallidas.append(n)
                continue
            for a in filas:
                por_id[a.asiento_id] = a       # idempotente: reintentar no duplica

        asientos = list(por_id.values())
        informe = {
            "paginas_declaradas": paginas,
            "total_declarado": total,
            "descargados": len(asientos),
            "paginas_fallidas": fallidas,
            "completo": bool(total and len(asientos) == total and not fallidas),
            "metricas": vars(self.metricas),
        }
        return asientos, informe

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ClienteERP":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
